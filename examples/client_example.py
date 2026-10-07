#!/usr/bin/env python3
"""政策裁决服务调用示例。

前置：服务已启动（如 docker compose up --build，或本地 uvicorn）。
运行：python examples/client_example.py [BASE_URL]   # 默认 http://localhost:8000

演示内容：
  1. 发布完整规则集 v1（含通配规则与不同优先级）
  2. 裁决：不指定版本 → 固定为请求开始时的最新版本，取最高优先级结论
  3. 发布冲突规则集 → 409，返回冲突规则对，且不产生新版本
  4. 更正：发布新的完整规则集 v2
  5. 指定旧版本 v1 重放 → 结果与首次裁决完全一致
  6. 未覆盖场景
"""
import json
import sys
import urllib.request
import urllib.error

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")


def call(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def show(title: str, status: int, body: dict) -> None:
    print(f"\n=== {title} (HTTP {status}) ===")
    print(json.dumps(body, ensure_ascii=False, indent=2))


# 1. 发布完整规则集 v1
ruleset_v1 = {
    "rules": [
        {  # 精确规则：上海的小微企业贷款 → 通过（高优先级）
            "business_key": "loan",
            "region": "上海",
            "category": "小微企业",
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": "2027-01-01T00:00:00Z",
            "priority": 10,
            "conclusion": "APPROVE",
        },
        {  # 通配兜底：所有地区所有类别 → 人工复核（低优先级，无限期）
            "business_key": "loan",
            "region": "*",
            "category": "*",
            "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": None,
            "priority": 1,
            "conclusion": "REVIEW",
        },
    ]
}
status, body = call("POST", "/rulesets", ruleset_v1)
show("1. 发布规则集 v1", status, body)
v1 = body["version"]

# 2. 裁决：上海小微企业 → 命中精确规则（优先级 10 胜出）
query = {
    "business_key": "loan",
    "region": "上海",
    "category": "小微企业",
    "occurred_at": "2026-06-01T12:00:00Z",
}
status, first_result = call("POST", "/adjudications", query)
show("2. 裁决（不指定版本 → 固定为最新已发布版本）", status, first_result)

# 3. 冲突发布：同业务键、同优先级、结论不同且地区/类别/时间可同时命中 → 整体失败
conflicting = {
    "rules": [
        {"business_key": "loan", "region": "上海", "category": "小微企业",
         "valid_from": "2026-01-01T00:00:00Z", "valid_to": None,
         "priority": 10, "conclusion": "APPROVE"},
        {"business_key": "loan", "region": "*", "category": "小微企业",
         "valid_from": "2026-06-01T00:00:00Z", "valid_to": None,
         "priority": 10, "conclusion": "REJECT"},
    ]
}
status, body = call("POST", "/rulesets", conflicting)
show("3. 发布冲突规则集（预期 409，返回冲突规则）", status, body)

# 4. 更正：发布新的完整规则集 v2（上海小微企业改为 REJECT）
ruleset_v2 = {
    "rules": [
        {**ruleset_v1["rules"][0], "conclusion": "REJECT"},
        ruleset_v1["rules"][1],
    ]
}
status, body = call("POST", "/rulesets", ruleset_v2)
show("4. 更正：发布新完整规则集 v2", status, body)
v2 = body["version"]

# 5. 指定旧版本重放：结果与第 2 步完全一致
status, replayed = call("POST", "/adjudications", {**query, "version": v1})
show(f"5. 指定旧版本 v{v1} 重放（结果不变）", status, replayed)
assert replayed == first_result, "重放结果与首次裁决不一致！"

status, latest = call("POST", "/adjudications", query)
show(f"5b. 不指定版本 → 使用最新版本 v{v2}", status, latest)

# 6. 未覆盖：通配规则有效期外的时刻
status, body = call("POST", "/adjudications", {
    "business_key": "loan", "region": "上海", "category": "小微企业",
    "occurred_at": "2020-01-01T00:00:00Z",
})
show("6. 未覆盖（无规则命中）", status, body)

print("\n全部示例执行完毕。")
