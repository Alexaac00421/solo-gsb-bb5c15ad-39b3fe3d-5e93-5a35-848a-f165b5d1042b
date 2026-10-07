"""请求/响应模型与入参校验。

约定：
- 通配符：region / category 传 "*" 或 null（或留空字符串）均表示通配。
- 半开有效时间段：[valid_from, valid_to)，valid_to 为 null 表示无限期。
- 时间统一按带时区时间处理；未带时区的输入按 UTC 解释。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

WILDCARD = "*"


def _as_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _normalize_pattern(value: Optional[str]) -> Optional[str]:
    """把 "*"、空串、null 统一归一化为 None（通配）。"""
    if value is None:
        return None
    value = value.strip()
    if value == "" or value == WILDCARD:
        return None
    return value


class RuleIn(BaseModel):
    """发布时提交的单条规则。"""

    business_key: str = Field(min_length=1, max_length=200, description="业务键")
    region: Optional[str] = Field(default=None, description="地区；'*' 或 null 表示通配")
    category: Optional[str] = Field(default=None, description="对象类别；'*' 或 null 表示通配")
    valid_from: datetime = Field(description="有效期起点（含）")
    valid_to: Optional[datetime] = Field(default=None, description="有效期终点（不含）；null 表示无限期")
    priority: int = Field(description="整数优先级，数值越大优先级越高")
    conclusion: str = Field(min_length=1, max_length=500, description="裁决结论")

    @field_validator("business_key", "conclusion")
    @classmethod
    def _strip_and_require_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空字符串")
        return v

    @field_validator("region", "category")
    @classmethod
    def _normalize(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_pattern(v)

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _ensure_aware(cls, v: Optional[datetime]) -> Optional[datetime]:
        return None if v is None else _as_utc_aware(v)

    @model_validator(mode="after")
    def _check_period(self) -> "RuleIn":
        if self.valid_to is not None and self.valid_from >= self.valid_to:
            raise ValueError(
                "有效时间段非法：要求 valid_from < valid_to（半开区间 "
                f"[valid_from, valid_to)），实际 valid_from={self.valid_from.isoformat()}，"
                f"valid_to={self.valid_to.isoformat()}"
            )
        return self

    def as_row(self) -> dict:
        return {
            "business_key": self.business_key,
            "region": self.region,
            "category": self.category,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "priority": self.priority,
            "conclusion": self.conclusion,
        }


class PublishRequest(BaseModel):
    """发布请求：一次提交完整规则集。"""

    rules: List[RuleIn] = Field(default_factory=list, description="完整规则集")


class AdjudicateRequest(BaseModel):
    """裁决请求。version 缺省时，固定为请求开始时刻最新已发布版本。"""

    business_key: str = Field(min_length=1, max_length=200)
    region: str = Field(min_length=1, max_length=200, description="具体地区，不可通配")
    category: str = Field(min_length=1, max_length=200, description="具体对象类别，不可通配")
    occurred_at: datetime = Field(description="业务发生时刻")
    version: Optional[int] = Field(default=None, ge=1, description="规则集版本；缺省取最新已发布版本")

    @field_validator("business_key")
    @classmethod
    def _strip_business_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空字符串")
        return v

    @field_validator("region", "category")
    @classmethod
    def _require_concrete(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("不能为空字符串")
        if v == WILDCARD:
            raise ValueError("裁决输入必须是具体值，不能使用通配符 '*'")
        return v

    @field_validator("occurred_at")
    @classmethod
    def _ensure_aware(cls, v: datetime) -> datetime:
        return _as_utc_aware(v)


class RuleOut(BaseModel):
    """输出规则；region / category 为 "*" 表示该规则在该维度上通配。"""

    rule_id: int
    business_key: str
    region: str
    category: str
    valid_from: datetime
    valid_to: Optional[datetime]
    priority: int
    conclusion: str


class PublishResponse(BaseModel):
    version: int


class RulesetSummary(BaseModel):
    version: int
    created_at: datetime
    rule_count: int


class RulesetDetail(BaseModel):
    version: int
    created_at: datetime
    rules: List[RuleOut]


class AdjudicationResponse(BaseModel):
    version: int = Field(description="本次裁决实际使用的规则集版本")
    covered: bool = Field(description="是否有规则命中；false 表示未覆盖")
    priority: Optional[int] = Field(description="命中的最高优先级；未覆盖时为 null")
    conclusion: Optional[str] = Field(description="最高优先级命中规则的结论；未覆盖时为 null")
    matched_rules: List[RuleOut] = Field(description="该优先级下全部命中规则")
