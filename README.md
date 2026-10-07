# 政策裁决服务（Policy Adjudication Service）

基于 **Python + FastAPI + PostgreSQL** 的版本化政策规则裁决服务。

- 每次发布提交**完整规则集**，成功返回新的**版本号**；发布是原子的，失败不产生新版本。
- 规则包含：业务键、地区、对象类别（均支持通配）、半开有效时间段、整数优先级、结论。
- 发布时自动检测冲突：**同业务键、同优先级、结论不同**且地区/类别/时间**可同时命中**的规则对，
  会导致整次发布失败（HTTP 409）并返回全部冲突规则。
- 裁决时固定使用某一个版本（显式指定，或请求开始时刻的最新已发布版本），
  在该版本内取**数值最高优先级**的结论，返回版本、该优先级全部命中规则及结论；无命中返回**未覆盖**。
- 已发布规则集**不可变**：更正只能发布新的完整规则集；指定旧版本重放，结果永远不变；
  并发发布不会使一次裁决混用多个版本。

## 快速开始

### Docker Compose（推荐，一键启动服务 + 数据库并自动初始化）

```bash
docker compose up --build
```

- 服务地址：<http://localhost:8000>
- 交互式 API 文档（Swagger UI）：<http://localhost:8000/docs>
- PostgreSQL：`localhost:5432`（用户/密码 `policy`/`policy`，库名 `policydb`，数据持久化在 `dbdata` 卷）

Compose 会等待数据库健康检查通过后启动服务；服务启动时自动建表（带重试等待），无需手工初始化。

### 本地开发

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 指向本地 PostgreSQL（或使用 SQLite 快速体验）
export DATABASE_URL="postgresql+psycopg2://policy:policy@localhost:5432/policydb"
# export DATABASE_URL="sqlite:///./dev.db"

uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 运行测试

```bash
pip install -r requirements-dev.txt
pytest tests/ -q        # 测试使用临时 SQLite 库，无需外部依赖
```

## 配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+psycopg2://policy:policy@localhost:5432/policydb` | SQLAlchemy 数据库连接串；Compose 中已注入指向 `db` 容器的地址 |
| `DB_INIT_MAX_ATTEMPTS` | `30` | 启动时等待数据库就绪的重试次数（每次间隔 1 秒） |

端口：服务监听 `8000`（Compose 映射 `8000:8000`，数据库映射 `5432:5432`）。

## 数据与规则语义

单条规则字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `business_key` | string，必填 | 业务键 |
| `region` | string / null | 地区；`"*"`、`null` 或空串表示**通配**（匹配任意地区） |
| `category` | string / null | 对象类别；通配规则同上 |
| `valid_from` | datetime，必填 | 有效期起点（**含**）；未带时区按 UTC 解释 |
| `valid_to` | datetime / null | 有效期终点（**不含**）；`null` 表示无限期。必须满足 `valid_from < valid_to` |
| `priority` | int，必填 | 整数优先级，**数值越大优先级越高** |
| `conclusion` | string，必填 | 裁决结论 |

- 有效时间段为**半开区间** `[valid_from, valid_to)`：`occurred_at == valid_from` 命中，`occurred_at == valid_to` 不命中。
- **冲突定义**：两条规则业务键相同、优先级相同、结论不同，且地区、类别、有效时间段三个维度都可能同时命中
  （存在某个具体输入使两条规则同时匹配）。发布时如有冲突，整次发布失败并返回全部冲突规则对。
- 因此同一版本内，同业务键同优先级的命中规则结论必然一致；同优先级、同结论的规则允许重叠，裁决时全部返回。

## API

### `POST /rulesets` — 发布完整规则集

请求：

```json
{
  "rules": [
    {"business_key": "loan", "region": "上海", "category": "小微企业",
     "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2027-01-01T00:00:00Z",
     "priority": 10, "conclusion": "APPROVE"},
    {"business_key": "loan", "region": "*", "category": "*",
     "valid_from": "2026-01-01T00:00:00Z", "valid_to": null,
     "priority": 1, "conclusion": "REVIEW"}
  ]
}
```

- 成功：`201` → `{"version": 1}`
- 冲突：`409` → `{"detail": {"message": "规则集存在冲突，本次发布已整体失败，未产生新版本", "conflicts": [{"reason": ..., "rule_a": {...}, "rule_b": {...}}]}}`
- 参数非法（时间段非法、字段缺失等）：`422` → `{"detail": "请求参数校验失败（字段缺失或取值非法）", "errors": [...]}`

### `POST /adjudications` — 裁决

请求：

```json
{
  "business_key": "loan",
  "region": "上海",
  "category": "小微企业",
  "occurred_at": "2026-06-01T12:00:00Z",
  "version": 1            // 可选；缺省时固定为请求开始时刻最新已发布版本
}
```

命中时 `200`：

```json
{
  "version": 1,
  "covered": true,
  "priority": 10,
  "conclusion": "APPROVE",
  "matched_rules": [
    {"rule_id": 1, "business_key": "loan", "region": "上海", "category": "小微企业",
     "valid_from": "2026-01-01T00:00:00Z", "valid_to": "2027-01-01T00:00:00Z",
     "priority": 10, "conclusion": "APPROVE"}
  ]
}
```

未覆盖时 `200`：`{"version": 1, "covered": false, "priority": null, "conclusion": null, "matched_rules": []}`

错误：

- 版本不存在：`404` → `{"detail": "规则集版本 99 不存在"}`
- 尚未发布任何规则集：`409` → `{"detail": "尚未发布任何规则集，无法裁决"}`
- 字段缺失/非法（如 `region` 传 `"*"`）：`422`

### 其他接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `GET` | `/rulesets` | 列出全部已发布版本（版本号、发布时间、规则数） |
| `GET` | `/rulesets/{version}` | 查看指定版本的完整规则集（版本不存在返回 404） |

## 一致性设计

- **发布原子性**：版本号与全部规则在同一个数据库事务中写入，冲突或任何失败都会整体回滚，不产生半成品版本。
- **并发发布**：PostgreSQL 下通过 `pg_advisory_xact_lock` 串行化发布事务，版本号唯一且单调递增。
- **裁决不混用版本**：裁决在请求开始时固定版本号，随后只读取该版本的规则；已发布规则集不可变，
  因此并发发布不会影响进行中的裁决，指定旧版本重放的结果与首次裁决完全一致。

## 调用示例

- Python 客户端（演示发布、冲突、更正、旧版本重放、未覆盖）：

  ```bash
  python examples/client_example.py            # 默认 http://localhost:8000
  ```

- curl 脚本：

  ```bash
  bash examples/curl_examples.sh
  ```

## 项目结构

```
app/
  config.py    # 环境变量配置
  db.py        # 引擎与会话
  models.py    # ORM 模型（rulesets / rules）
  schemas.py   # 请求/响应模型与校验
  service.py   # 冲突检测、发布、裁决核心逻辑
  main.py      # FastAPI 路由与错误处理
examples/      # 调用示例（Python 客户端、curl 脚本）
tests/         # pytest 端到端测试
Dockerfile
docker-compose.yml
```
