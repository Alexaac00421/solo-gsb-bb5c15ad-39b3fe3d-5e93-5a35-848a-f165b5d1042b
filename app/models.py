"""ORM 模型。

设计要点：
- 规则集（rulesets）一旦发布即不可变，更正只能通过发布新的完整规则集完成，
  因此指定旧版本的裁决重放结果永远不变。
- 规则中的 region / category 为 NULL 时表示通配（匹配任意值）。
- valid_to 为 NULL 时表示有效期无限（半开区间 [valid_from, +∞)）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Ruleset(Base):
    __tablename__ = "rulesets"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rules: Mapped[list["Rule"]] = relationship(back_populates="ruleset")


class Rule(Base):
    __tablename__ = "rules"

    # SQLite 仅 INTEGER PRIMARY KEY 才能自增（ROWID 别名），故在 SQLite 上退化为 Integer。
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    version: Mapped[int] = mapped_column(
        ForeignKey("rulesets.version", ondelete="CASCADE"), nullable=False
    )
    business_key: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # NULL = 通配
    category: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # NULL = 通配
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    conclusion: Mapped[str] = mapped_column(Text, nullable=False)

    ruleset: Mapped[Ruleset] = relationship(back_populates="rules")

    __table_args__ = (
        Index("ix_rules_version_business_key", "version", "business_key"),
    )
