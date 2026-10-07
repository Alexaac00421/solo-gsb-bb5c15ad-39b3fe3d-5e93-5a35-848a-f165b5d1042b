"""数据库引擎与会话管理。"""
from __future__ import annotations

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_database_url

DATABASE_URL = get_database_url()

_connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # SQLite 仅用于本地开发/测试；允许跨线程使用连接（FastAPI TestClient 需要）。
    _connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, pool_pre_ping=True, connect_args=_connect_args)

if DATABASE_URL.startswith("sqlite"):
    # SQLite 下用 BEGIN IMMEDIATE 让写事务一开始就拿到库级写锁，
    # 从而串行化并发发布，保证版本号不重复（PostgreSQL 下由 advisory lock 保证）。
    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_session():
    """FastAPI 依赖：每个请求一个会话。"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
