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

# S3 报告归档（可选）：4 个必需变量都设置才注入，缺一个就跳过（保持本地落盘）。
# 部分填写会静默失效，这里显式提示，避免以为已启用其实没生效。
S3_VARS_SET=0
for v in S3_ENDPOINT S3_BUCKET S3_ACCESS_KEY_ID S3_SECRET_ACCESS_KEY; do
  if [[ -n "${!v:-}" ]]; then
    S3_VARS_SET=$((S3_VARS_SET+1))
  fi
done
if [[ $S3_VARS_SET -gt 0 && $S3_VARS_SET -lt 4 ]]; then
  echo "警告：S3 必需变量只配置了 $S3_VARS_SET/4 个，未注入 Secret，报告仍落本地 results/" >&2
  echo "      需要 S3 归档请补齐: S3_ENDPOINT / S3_BUCKET / S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY" >&2
fi
if [[ $S3_VARS_SET -eq 4 ]]; then
  SECRET_ARGS+=(--from-literal="S3_ENDPOINT=${S3_ENDPOINT}")
  SECRET_ARGS+=(--from-literal="S3_BUCKET=${S3_BUCKET}")
  SECRET_ARGS+=(--from-literal="S3_ACCESS_KEY_ID=${S3_ACCESS_KEY_ID}")
  SECRET_ARGS+=(--from-literal="S3_SECRET_ACCESS_KEY=${S3_SECRET_ACCESS_KEY}")
  # 公开 URL 配置不敏感，但放 Secret 保持 env 注入路径统一（避免 Deployment 同时引用 Secret + ConfigMap）
  [[ -n "${S3_PUBLIC_BASE_URL:-}" ]] && \
    SECRET_ARGS+=(--from-literal="S3_PUBLIC_BASE_URL=${S3_PUBLIC_BASE_URL}")
  [[ -n "${S3_PUBLIC_URL_STYLE:-}" ]] && \
    SECRET_ARGS+=(--from-literal="S3_PUBLIC_URL_STYLE=${S3_PUBLIC_URL_STYLE}")
  [[ -n "${S3_REGION:-}" ]] && \
    SECRET_ARGS+=(--from-literal="S3_REGION=${S3_REGION}")
  [[ -n "${S3_FORCE_PATH_STYLE:-}" ]] && \
    SECRET_ARGS+=(--from-literal="S3_FORCE_PATH_STYLE=${S3_FORCE_PATH_STYLE}")
  [[ -n "${S3_KEY_PREFIX:-}" ]] && \
    SECRET_ARGS+=(--from-literal="S3_KEY_PREFIX=${S3_KEY_PREFIX}")
  [[ -n "${S3_PRESIGNED_TTL_S:-}" ]] && \
    SECRET_ARGS+=(--from-literal="S3_PRESIGNED_TTL_S=${S3_PRESIGNED_TTL_S}")
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
