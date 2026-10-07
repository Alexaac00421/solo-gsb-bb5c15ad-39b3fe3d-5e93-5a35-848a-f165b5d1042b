"""端到端 API 测试（FastAPI TestClient + SQLite）。"""
import threading

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def make_rule(**overrides):
    rule = {
        "business_key": "loan",
        "region": "上海",
        "category": "小微企业",
        "valid_from": "2026-01-01T00:00:00Z",
        "valid_to": "2027-01-01T00:00:00Z",
        "priority": 10,
        "conclusion": "APPROVE",
    }
    rule.update(overrides)
    return rule


def publish(rules):
    return client.post("/rulesets", json={"rules": rules})


def adjudicate(**payload):
    return client.post("/adjudications", json=payload)


# ---------------------------------------------------------------- 健康检查

def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------- 发布与裁决主流程

def test_publish_and_adjudicate_highest_priority_wins():
    resp = publish(
        [
            make_rule(priority=1, conclusion="REVIEW"),
            make_rule(priority=10, conclusion="APPROVE"),
            make_rule(priority=5, conclusion="HOLD", region="*", category="*"),
        ]
    )
    assert resp.status_code == 201, resp.text
    version = resp.json()["version"]

    resp = adjudicate(
        business_key="loan",
        region="上海",
        category="小微企业",
        occurred_at="2026-06-01T12:00:00Z",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == version  # 未指定版本 → 固定为最新已发布版本
    assert body["covered"] is True
    assert body["priority"] == 10  # 数值最高优先级胜出
    assert body["conclusion"] == "APPROVE"
    assert len(body["matched_rules"]) == 1
    assert body["matched_rules"][0]["conclusion"] == "APPROVE"


def test_wildcard_rule_matches_and_is_returned_as_star():
    resp = publish(
        [
            make_rule(
                business_key="subsidy",
                region="*",
                category=None,
                priority=3,
                conclusion="ELIGIBLE",
            )
        ]
    )
    assert resp.status_code == 201, resp.text

    resp = adjudicate(
        business_key="subsidy",
        region="乌鲁木齐",
        category="个体工商户",
        occurred_at="2026-06-01T00:00:00Z",
    )
    body = resp.json()
    assert body["covered"] is True
    assert body["conclusion"] == "ELIGIBLE"
    assert body["matched_rules"][0]["region"] == "*"
    assert body["matched_rules"][0]["category"] == "*"


def test_same_priority_same_conclusion_overlap_is_allowed_and_all_returned():
    resp = publish(
        [
            make_rule(business_key="tax", region="上海", priority=7, conclusion="REDUCED"),
            make_rule(business_key="tax", region="*", priority=7, conclusion="REDUCED"),
        ]
    )
    assert resp.status_code == 201, resp.text

    resp = adjudicate(
        business_key="tax",
        region="上海",
        category="小微企业",
        occurred_at="2026-06-01T00:00:00Z",
    )
    body = resp.json()
    assert body["covered"] is True
    assert body["priority"] == 7
    assert body["conclusion"] == "REDUCED"
    assert len(body["matched_rules"]) == 2  # 该优先级全部命中规则都返回


def test_uncovered_returns_not_covered():
    resp = adjudicate(
        business_key="nonexistent-biz",
        region="上海",
        category="小微企业",
        occurred_at="2026-06-01T00:00:00Z",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["covered"] is False
    assert body["conclusion"] is None
    assert body["priority"] is None
    assert body["matched_rules"] == []


# ---------------------------------------------------------------- 半开区间边界

def test_half_open_period_boundaries():
    resp = publish(
        [
            make_rule(
                business_key="period",
                valid_from="2026-03-01T00:00:00Z",
                valid_to="2026-06-01T00:00:00Z",
                priority=1,
                conclusion="IN",
            )
        ]
    )
    assert resp.status_code == 201, resp.text

    base = {"business_key": "period", "region": "上海", "category": "小微企业"}
    # 起点（含）：命中
    assert adjudicate(**base, occurred_at="2026-03-01T00:00:00Z").json()["covered"] is True
    # 区间内：命中
    assert adjudicate(**base, occurred_at="2026-04-15T10:30:00Z").json()["covered"] is True
    # 终点（不含）：不命中
    assert adjudicate(**base, occurred_at="2026-06-01T00:00:00Z").json()["covered"] is False
    # 起点之前：不命中
    assert adjudicate(**base, occurred_at="2026-02-28T23:59:59Z").json()["covered"] is False


def test_open_ended_period_matches_far_future():
    resp = publish(
        [
            make_rule(
                business_key="open",
                valid_from="2026-01-01T00:00:00Z",
                valid_to=None,
                priority=1,
                conclusion="ALWAYS",
            )
        ]
    )
    assert resp.status_code == 201, resp.text
    body = adjudicate(
        business_key="open",
        region="上海",
        category="小微企业",
        occurred_at="2099-01-01T00:00:00Z",
    ).json()
    assert body["covered"] is True
    assert body["conclusion"] == "ALWAYS"


# ---------------------------------------------------------------- 冲突检测

def test_conflicting_ruleset_rejected_and_returns_conflicts():
    resp = publish(
        [
            make_rule(business_key="clash", priority=10, conclusion="A", region="上海"),
            make_rule(business_key="clash", priority=10, conclusion="B", region="*"),
        ]
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "冲突" in detail["message"]
    assert len(detail["conflicts"]) == 1
    pair = detail["conflicts"][0]
    assert pair["business_key"] == "clash"
    assert pair["priority"] == 10
    assert {pair["rule_a"]["conclusion"], pair["rule_b"]["conclusion"]} == {"A", "B"}


def test_no_conflict_when_dimensions_do_not_overlap():
    # 地区不相交：不冲突
    resp = publish(
        [
            make_rule(business_key="dim", priority=1, conclusion="A", region="上海"),
            make_rule(business_key="dim", priority=1, conclusion="B", region="北京"),
        ]
    )
    assert resp.status_code == 201, resp.text

    # 时间段不相交（半开区间首尾相接）：不冲突
    resp = publish(
        [
            make_rule(
                business_key="dim2",
                priority=1,
                conclusion="A",
                valid_from="2026-01-01T00:00:00Z",
                valid_to="2026-06-01T00:00:00Z",
            ),
            make_rule(
                business_key="dim2",
                priority=1,
                conclusion="B",
                valid_from="2026-06-01T00:00:00Z",
                valid_to="2027-01-01T00:00:00Z",
            ),
        ]
    )
    assert resp.status_code == 201, resp.text

    # 不同业务键 / 不同优先级：不冲突
    resp = publish(
        [
            make_rule(business_key="dim3", priority=1, conclusion="A"),
            make_rule(business_key="dim3", priority=2, conclusion="B"),
            make_rule(business_key="dim4", priority=1, conclusion="C"),
        ]
    )
    assert resp.status_code == 201, resp.text


def test_failed_publish_creates_no_version():
    before = client.get("/rulesets").json()
    resp = publish(
        [
            make_rule(business_key="atomic", priority=1, conclusion="A"),
            make_rule(business_key="atomic", priority=1, conclusion="B"),
        ]
    )
    assert resp.status_code == 409
    after = client.get("/rulesets").json()
    assert [r["version"] for r in after] == [r["version"] for r in before]  # 整体失败，未产生新版本


# ---------------------------------------------------------------- 版本固定与重放

def test_replay_with_old_version_is_stable_and_latest_uses_new_version():
    v1 = publish([make_rule(business_key="replay", priority=1, conclusion="OLD")]).json()["version"]

    query = {
        "business_key": "replay",
        "region": "上海",
        "category": "小微企业",
        "occurred_at": "2026-06-01T00:00:00Z",
    }
    first = adjudicate(**query).json()
    assert first["version"] == v1 and first["conclusion"] == "OLD"

    # 更正：发布新的完整规则集
    v2 = publish([make_rule(business_key="replay", priority=1, conclusion="NEW")]).json()["version"]
    assert v2 > v1

    # 指定旧版本重放：结果不变
    replayed = adjudicate(**query, version=v1).json()
    assert replayed["version"] == v1
    assert replayed["conclusion"] == "OLD"
    assert replayed["matched_rules"] == first["matched_rules"]

    # 不指定版本：固定为请求开始时的最新版本
    latest = adjudicate(**query).json()
    assert latest["version"] == v2
    assert latest["conclusion"] == "NEW"


def test_concurrent_publishes_get_unique_versions_and_adjudication_never_mixes():
    barrier = threading.Barrier(8)
    results, errors = [], []

    def do_publish(i):
        try:
            barrier.wait(timeout=10)
            resp = publish([make_rule(business_key=f"conc-{i}", priority=1, conclusion=f"C{i}")])
            results.append(resp.json()["version"])
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=do_publish, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(set(results)) == 8  # 版本号互不重复

    # 每个版本都是完整规则集：任一版本的裁决只含该版本自己的规则
    for version in results:
        detail = client.get(f"/rulesets/{version}").json()
        assert len(detail["rules"]) == 1
        body = adjudicate(
            business_key=detail["rules"][0]["business_key"],
            region="上海",
            category="小微企业",
            occurred_at="2026-06-01T00:00:00Z",
            version=version,
        ).json()
        assert body["version"] == version
        assert all(r["rule_id"] == detail["rules"][0]["rule_id"] for r in body["matched_rules"])


# ---------------------------------------------------------------- 明确报错

def test_invalid_period_rejected():
    resp = publish(
        [make_rule(valid_from="2026-06-01T00:00:00Z", valid_to="2026-01-01T00:00:00Z")]
    )
    assert resp.status_code == 422
    assert "校验失败" in resp.json()["detail"]
    assert any("valid_from" in str(e) or "valid_to" in str(e) for e in resp.json()["errors"])

    # 起点 == 终点同样非法（空区间）
    resp = publish(
        [make_rule(valid_from="2026-06-01T00:00:00Z", valid_to="2026-06-01T00:00:00Z")]
    )
    assert resp.status_code == 422


def test_missing_fields_rejected():
    resp = publish([{"business_key": "loan"}])  # 缺 valid_from / priority / conclusion
    assert resp.status_code == 422
    missing = {e["loc"][-1] for e in resp.json()["errors"]}
    assert {"valid_from", "priority", "conclusion"} <= missing

    resp = adjudicate(business_key="loan", region="上海")  # 缺 category / occurred_at
    assert resp.status_code == 422


def test_version_not_found():
    resp = adjudicate(
        business_key="loan",
        region="上海",
        category="小微企业",
        occurred_at="2026-06-01T00:00:00Z",
        version=999999,
    )
    assert resp.status_code == 404
    assert "999999" in resp.json()["detail"]

    resp = client.get("/rulesets/999999")
    assert resp.status_code == 404


def test_wildcard_not_allowed_in_adjudication_input():
    resp = adjudicate(
        business_key="loan",
        region="*",
        category="小微企业",
        occurred_at="2026-06-01T00:00:00Z",
    )
    assert resp.status_code == 422
