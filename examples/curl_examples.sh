#!/usr/bin/env bash
# 政策裁决服务 curl 调用示例。前置：服务已启动（默认 http://localhost:8000）。
set -euo pipefail
BASE_URL="${1:-http://localhost:8000}"

echo "== 健康检查 =="
curl -s "$BASE_URL/health"; echo

echo "== 1. 发布完整规则集（返回新版本号） =="
curl -s -X POST "$BASE_URL/rulesets" -H 'Content-Type: application/json' -d '{
  "rules": [
    {"business_key":"loan","region":"上海","category":"小微企业",
     "valid_from":"2026-01-01T00:00:00Z","valid_to":"2027-01-01T00:00:00Z",
     "priority":10,"conclusion":"APPROVE"},
    {"business_key":"loan","region":"*","category":"*",
     "valid_from":"2026-01-01T00:00:00Z","valid_to":null,
     "priority":1,"conclusion":"REVIEW"}
  ]}'; echo

echo "== 2. 裁决（不指定版本 → 固定为请求开始时最新已发布版本） =="
curl -s -X POST "$BASE_URL/adjudications" -H 'Content-Type: application/json' -d '{
  "business_key":"loan","region":"上海","category":"小微企业",
  "occurred_at":"2026-06-01T12:00:00Z"}'; echo

echo "== 3. 发布冲突规则集（预期 409，返回冲突规则，整次发布失败） =="
curl -s -X POST "$BASE_URL/rulesets" -H 'Content-Type: application/json' -d '{
  "rules": [
    {"business_key":"loan","region":"上海","category":"小微企业",
     "valid_from":"2026-01-01T00:00:00Z","valid_to":null,"priority":10,"conclusion":"APPROVE"},
    {"business_key":"loan","region":"*","category":"小微企业",
     "valid_from":"2026-06-01T00:00:00Z","valid_to":null,"priority":10,"conclusion":"REJECT"}
  ]}'; echo

echo "== 4. 指定版本裁决（重放旧版本，结果不变） =="
curl -s -X POST "$BASE_URL/adjudications" -H 'Content-Type: application/json' -d '{
  "business_key":"loan","region":"上海","category":"小微企业",
  "occurred_at":"2026-06-01T12:00:00Z","version":1}'; echo

echo "== 5. 未覆盖（无规则命中） =="
curl -s -X POST "$BASE_URL/adjudications" -H 'Content-Type: application/json' -d '{
  "business_key":"unknown","region":"北京","category":"大型企业",
  "occurred_at":"2026-06-01T00:00:00Z"}'; echo

echo "== 6. 查看版本列表与指定版本规则集 =="
curl -s "$BASE_URL/rulesets"; echo
curl -s "$BASE_URL/rulesets/1"; echo
