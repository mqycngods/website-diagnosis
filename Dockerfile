# site-diagnosis-zh — FastAPI 服务镜像（tools/api_server.py）
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl 是运行时硬依赖：diagnose/http_utils.py 默认用 curl 子进程抓页面
# （指纹比 requests 更接近真实浏览器，用于绕过 Cloudflare Bot Management）。
# slim 镜像不含 curl，缺了会静默回落 requests —— 不报错，但部分站点抓取异常。
COPY requirements.txt .
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt

# 运行时代码（模板已内联进 render/inline_templates.py，config/ 供 diagnose 读取）
COPY report.py .
COPY diagnose ./diagnose
COPY render ./render
COPY tools ./tools
COPY config ./config
COPY templates ./templates

# 非 root 运行；results/ 为报告落盘目录（需要可写）
RUN useradd --create-home appuser \
    && mkdir -p /app/results \
    && chown -R appuser:appuser /app
USER appuser

# Render 等平台注入 $PORT；本地缺省 8000
ENV PORT=8000

EXPOSE 8000

# 与 kubernetes.yaml 的 readinessProbe/livenessProbe 保持一致。
# HEALTHCHECK 的 CMD 是 exec 形式，不经过 shell，所以这里不能写 ${PORT:-8000}；
# 用 ENV 默认值代替（k8s 里仍可用 env 覆盖，但探针路径与端口由 k8s 自己管）。
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# 必须单 worker（内存暂存 run_id）。
CMD ["sh", "-c", "uvicorn tools.api_server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
