#!/usr/bin/env bash
# 一键探活：面试演示前跑一遍，确认全链路就绪（Git Bash 运行）
# 用法：bash scripts/demo_check.sh
set -u

echo "===== 长安文旅探店助手 · 探活检查 ====="

check_http() {
  local name=$1 url=$2
  code=$(curl -s -m 4 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
  if [ "$code" = "200" ]; then
    echo "  ✅ $name ($url) -> $code"
  elif [ "$code" = "000" ]; then
    echo "  ❌ $name ($url) -> 无法连接"
  else
    echo "  ⚠️  $name ($url) -> $code"
  fi
}

check_http "Java 后端"     "http://127.0.0.1:8081/shop-type/list"
check_http "Python AI"     "http://127.0.0.1:8000/api/ai/health"
check_http "nginx 前端"    "http://127.0.0.1:8080/"
check_http "Milvus 健康"   "http://127.0.0.1:9091/healthz"

echo ""
echo "----- API Key 配置（chang_an_ai/.env）-----"
if [ -f ".env" ]; then
  for k in DEEPSEEK_API_KEY SILICONFLOW_API_KEY; do
    if grep -qE "^${k}=sk-" .env; then
      echo "  ✅ $k 已配置"
    else
      echo "  ❌ $k 未配置"
    fi
  done
else
  echo "  ❌ .env 不存在（cp .env.example .env）"
fi

echo ""
echo "----- 知识库状态 -----"
curl -s -m 6 "http://127.0.0.1:8000/api/ai/admin/status" | head -c 300
echo ""
echo "===== 检查完毕 ====="
