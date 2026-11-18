#!/usr/bin/env bash
# 从 deploy/.env 读取配置，生成 Deployment 需要的 Secret / ConfigMap。
# 幂等：已存在则覆盖更新，不报错。
#
# 用法：
#   ./deploy/apply-secrets.sh              # 使用 deploy/.env
#   ENV_FILE=deploy/.env.prod ./deploy/apply-secrets.sh
#
# 注意：SITE_DIAGNOSIS_API_KEY 在进程启动时读取，
#       更新 Secret 后必须 rollout restart 才生效。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$SCRIPT_DIR/.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "找不到 $ENV_FILE" >&2
  echo "先复制模板：cp $SCRIPT_DIR/.env.example $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a
source "$ENV_FILE"
set +a

: "${APP_NAME:?APP_NAME 未设置}"
: "${SITE_DIAGNOSIS_API_KEY:?SITE_DIAGNOSIS_API_KEY 未设置}"

if [[ "$SITE_DIAGNOSIS_API_KEY" == "change-me" ]]; then
  echo "警告：SITE_DIAGNOSIS_API_KEY 仍是模板默认值 change-me" >&2
fi

NS_ARGS=()
if [[ -n "${NAMESPACE:-}" ]]; then
  NS_ARGS=(-n "$NAMESPACE")
fi

SECRET_NAME="${APP_NAME}-secret"

# REDIS_URL 由 REDIS_HOST / REDIS_PORT / REDIS_PASSWORD 拼装（可选）
REDIS_URL_VALUE="${REDIS_URL:-}"
if [[ -z "$REDIS_URL_VALUE" && -n "${REDIS_PASSWORD:-}" ]]; then
  REDIS_URL_VALUE="redis://:${REDIS_PASSWORD}@${REDIS_HOST:-plat-arch-site-diagnosis-redis}:${REDIS_PORT:-6379}/0"
fi

SECRET_ARGS=(--from-literal="SITE_DIAGNOSIS_API_KEY=${SITE_DIAGNOSIS_API_KEY}")
if [[ -n "$REDIS_URL_VALUE" ]]; then
  SECRET_ARGS+=(--from-literal="REDIS_URL=${REDIS_URL_VALUE}")
fi

echo "==> 创建/更新 Secret: $SECRET_NAME"
kubectl "${NS_ARGS[@]}" create secret generic "$SECRET_NAME" \
  "${SECRET_ARGS[@]}" \
  --dry-run=client -o yaml \
  | kubectl "${NS_ARGS[@]}" apply -f -

echo "==> 创建/更新 ConfigMap: ${APP_NAME}-config"
kubectl "${NS_ARGS[@]}" create configmap "${APP_NAME}-config" \
  --from-literal="GEO_HTTP_BACKEND=${GEO_HTTP_BACKEND:-curl}" \
  --from-literal="PORT=${PORT:-8000}" \
  --dry-run=client -o yaml \
  | kubectl "${NS_ARGS[@]}" apply -f -

echo
echo "完成。若 Api Key / REDIS_URL 有变化，需重启 Pod 使其生效："
echo "  kubectl ${NS_ARGS[*]} rollout restart deployment $APP_NAME"
