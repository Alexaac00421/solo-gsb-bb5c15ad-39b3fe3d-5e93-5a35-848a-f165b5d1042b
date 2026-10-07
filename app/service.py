"""核心业务逻辑：发布（含冲突检测）与裁决。

一致性设计：
- 发布：单个事务内插入新版本号 + 全部规则，要么全部成功要么整体回滚；
  PostgreSQL 下用 advisory lock 串行化并发发布，保证版本号唯一且单调递增。
- 裁决：请求开始时固定版本号（显式指定或取当时最新已发布版本），随后只读
  该版本的规则。已发布规则集不可变，因此并发发布不会使一次裁决混用版本，
  指定旧版本重放的结果也永远不变。
"""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import combinations
from typing import List, Optional, Sequence

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from .models import Rule, Ruleset
from .schemas import AdjudicateRequest, RuleIn

# pg_advisory_xact_lock 的锁键（任意固定的 64 位整数，用于串行化发布）。
PUBLISH_LOCK_KEY = 0x504F4C4943590001  # "POLICY"


class ConflictError(Exception):
    """规则集内部存在冲突：同业务键、同优先级、结论不同且可同时命中。"""

    def __init__(self, conflicts: List[dict]):
        super().__init__(f"ruleset contains {len(conflicts)} conflicting rule pair(s)")
        self.conflicts = conflicts


class VersionNotFoundError(Exception):
    def __init__(self, version: int):
        super().__init__(f"ruleset version {version} does not exist")
        self.version = version


class NoRulesetPublishedError(Exception):
    pass


# ---------------------------------------------------------------- 冲突检测

def _patterns_overlap(a: Optional[str], b: Optional[str]) -> bool:
    """两个取值模式是否可同时命中同一具体值（None 表示通配）。"""
    return a is None or b is None or a == b


def _periods_overlap(
    a_start: datetime, a_end: Optional[datetime],
    b_start: datetime, b_end: Optional[datetime],
) -> bool:
    """半开区间 [start, end) 是否相交；end 为 None 表示 +∞。"""
    return (b_end is None or a_start < b_end) and (a_end is None or b_start < a_end)


def _rule_snapshot(index: int, rule: RuleIn) -> dict:
    return {
        "index": index,
        "business_key": rule.business_key,
        "region": rule.region if rule.region is not None else "*",
        "category": rule.category if rule.category is not None else "*",
        "valid_from": rule.valid_from.isoformat(),
        "valid_to": rule.valid_to.isoformat() if rule.valid_to is not None else None,
        "priority": rule.priority,
        "conclusion": rule.conclusion,
    }


def find_conflicts(rules: Sequence[RuleIn]) -> List[dict]:
    """找出全部冲突规则对。

    冲突定义：业务键相同、优先级相同、结论不同，且地区、类别、有效时间段
    三个维度上都可能同时命中（存在某个具体输入使两条规则同时匹配）。
    """
    groups: dict[tuple[str, int], list[tuple[int, RuleIn]]] = {}
    for index, rule in enumerate(rules):
        groups.setdefault((rule.business_key, rule.priority), []).append((index, rule))

    conflicts: List[dict] = []
    for (business_key, priority), members in groups.items():
        for (i, a), (j, b) in combinations(members, 2):
            if a.conclusion == b.conclusion:
                continue
            if not _patterns_overlap(a.region, b.region):
                continue
            if not _patterns_overlap(a.category, b.category):
                continue
            if not _periods_overlap(a.valid_from, a.valid_to, b.valid_from, b.valid_to):
                continue
            conflicts.append(
                {
                    "reason": (
                        f"业务键 '{business_key}'、优先级 {priority} 的两条规则结论不同"
                        "（'{}' vs '{}'），且地区、类别、有效时间段可同时命中".format(
                            a.conclusion, b.conclusion
                        )
                    ),
                    "business_key": business_key,
                    "priority": priority,
                    "rule_a": _rule_snapshot(i, a),
                    "rule_b": _rule_snapshot(j, b),
                }
            )
    return conflicts


# ---------------------------------------------------------------- 发布

def publish_ruleset(session: Session, rules: Sequence[RuleIn]) -> int:
    """校验并发布完整规则集，返回新版本号。整个发布是一个事务。"""
    conflicts = find_conflicts(rules)
    if conflicts:
        raise ConflictError(conflicts)

    if session.bind is not None and session.bind.dialect.name == "postgresql":
        # 串行化并发发布：同一时刻只允许一个发布事务计算下一个版本号。
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PUBLISH_LOCK_KEY})

    current_max = session.scalar(select(func.max(Ruleset.version)))
    next_version = (current_max or 0) + 1

    session.add(Ruleset(version=next_version))
    session.add_all(Rule(version=next_version, **rule.as_row()) for rule in rules)
    session.commit()
    return next_version


# ---------------------------------------------------------------- 查询

def _utc(dt: datetime) -> datetime:
    """SQLite 等不带时区信息的驱动读出的时间按 UTC 处理。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def rule_to_out(rule: Rule) -> dict:
    return {
        "rule_id": rule.id,
        "business_key": rule.business_key,
        "region": rule.region if rule.region is not None else "*",
        "category": rule.category if rule.category is not None else "*",
        "valid_from": _utc(rule.valid_from),
        "valid_to": _utc(rule.valid_to) if rule.valid_to is not None else None,
        "priority": rule.priority,
        "conclusion": rule.conclusion,
    }


def list_rulesets(session: Session) -> List[dict]:
    rows = session.execute(
        select(Ruleset.version, Ruleset.created_at, func.count(Rule.id))
        .outerjoin(Rule, Rule.version == Ruleset.version)
        .group_by(Ruleset.version, Ruleset.created_at)
        .order_by(Ruleset.version)
    ).all()
    return [
        {"version": version, "created_at": _utc(created_at), "rule_count": count}
        for version, created_at, count in rows
    ]


def get_ruleset(session: Session, version: int) -> dict:
    ruleset = session.get(Ruleset, version)
    if ruleset is None:
        raise VersionNotFoundError(version)
    rules = session.scalars(
        select(Rule).where(Rule.version == version).order_by(Rule.id)
    ).all()
    return {
        "version": ruleset.version,
        "created_at": _utc(ruleset.created_at),
        "rules": [rule_to_out(rule) for rule in rules],
    }


# ---------------------------------------------------------------- 裁决

def adjudicate(session: Session, req: AdjudicateRequest) -> dict:
    """在固定版本内裁决：取数值最高优先级的结论。"""
    # 1) 固定版本：显式指定则校验存在性；否则取请求开始时刻最新已发布版本。
    if req.version is not None:
        exists = session.scalar(
            select(func.count()).select_from(Ruleset).where(Ruleset.version == req.version)
        )
        if not exists:
            raise VersionNotFoundError(req.version)
        version = req.version
    else:
        version = session.scalar(select(func.max(Ruleset.version)))
        if version is None:
            raise NoRulesetPublishedError()

    # 2) 只在该版本内匹配：业务键相等，地区/类别精确或通配，时刻落在半开区间内。
    rows = session.scalars(
        select(Rule).where(
            Rule.version == version,
            Rule.business_key == req.business_key,
            or_(Rule.region.is_(None), Rule.region == req.region),
            or_(Rule.category.is_(None), Rule.category == req.category),
            Rule.valid_from <= req.occurred_at,
            or_(Rule.valid_to.is_(None), Rule.valid_to > req.occurred_at),
        )
    ).all()

    if not rows:
        return {
            "version": version,
            "covered": False,
            "priority": None,
            "conclusion": None,
            "matched_rules": [],
        }

    # 3) 取数值最高优先级；该优先级上的命中规则结论必然一致（发布时已做冲突检测）。
    top_priority = max(rule.priority for rule in rows)
    matched = [rule for rule in rows if rule.priority == top_priority]
    matched.sort(key=lambda rule: rule.id)
    return {
        "version": version,
        "covered": True,
        "priority": top_priority,
        "conclusion": matched[0].conclusion,
        "matched_rules": [rule_to_out(rule) for rule in matched],
    }
