"""运行配置：全部通过环境变量覆盖。"""
from __future__ import annotations

import os

# 默认连接本地 PostgreSQL；Docker Compose 中会注入指向 db 容器的地址。
DEFAULT_DATABASE_URL = "postgresql+psycopg2://policy:policy@localhost:5432/policydb"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_db_init_max_attempts() -> int:
    """服务启动时等待数据库就绪的重试次数（每次间隔 1 秒）。"""
    return int(os.environ.get("DB_INIT_MAX_ATTEMPTS", "30"))
