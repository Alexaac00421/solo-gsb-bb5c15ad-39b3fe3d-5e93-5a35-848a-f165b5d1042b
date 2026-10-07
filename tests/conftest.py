"""测试环境：在导入应用前把数据库切到临时 SQLite 文件。

核心逻辑（冲突检测、优先级裁决、版本固定、半开区间）与数据库无关；
PostgreSQL 特有的 advisory lock 在 SQLite 下自动跳过（由 BEGIN IMMEDIATE 串行化代替）。
"""
import os
import tempfile

_fd, _db_path = tempfile.mkstemp(prefix="policy_test_", suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ["DB_INIT_MAX_ATTEMPTS"] = "3"

# TestClient 不以上下文管理器方式使用时不会触发 lifespan，这里直接建表。
# 注意先导入 models 注册表结构，再 create_all。
from app import models  # noqa: E402, F401
from app.db import Base, engine  # noqa: E402

Base.metadata.create_all(engine)
