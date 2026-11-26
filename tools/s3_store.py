"""把 results/ 下的报告文件上传到 S3 兼容存储，返回可外部访问的 URL。

面向通用 S3 兼容服务（阿里云 OSS、腾讯 COS、华为 OBS、MinIO、又拍、七牛、
Cloudflare R2、AWS S3 等）：用户只需要 bucket + 访问凭证 + 内外网 endpoint。

设计要点：
  - **上传走内网 endpoint**（`S3_ENDPOINT`）：同机房零流量、低延迟。
  - **返回 URL 走外网地址 / CDN 域名**（`S3_PUBLIC_BASE_URL`）：与上传
    endpoint 解耦——内网地址不能暴露给外部调用方。
  - 配 CDN 域名时 URL 永久有效可归档；配外网 endpoint 时 URL 永久有效但
    依赖 bucket 公有读权限；两者都是「公开 URL」，不需要签名。
  - 可选 fallback：未配 S3_PUBLIC_BASE_URL 时用 SDK presigned URL（在
    外网 endpoint 上签名），TTL 默认 7 天。

环境变量（必填）：
  S3_ENDPOINT            上传用 endpoint
  S3_BUCKET              桶名
  S3_ACCESS_KEY_ID       访问 key
  S3_SECRET_ACCESS_KEY   密钥

环境变量（可选）：
  S3_REGION              AWS region，默认 us-east-1。阿里/腾讯/MinIO 一般
                         不需要；AWS S3 与 R2 建议显式给。
  S3_PUBLIC_BASE_URL     返回给调用方的 URL 前缀：
                           - CDN 自定义域名：https://cdn.example.com
                           - 外网 endpoint（path 风格，bucket 拼在路径里）：
                             https://oss-cn-beijing.aliyuncs.com
                           - 外网 endpoint（virtual-host 风格，bucket 拼在 host 里）：
                             https://oss-cn-beijing.aliyuncs.com + S3_PUBLIC_URL_STYLE=virtual-host
                         必须是裸 http(s) origin，不带 path/query/凭据。
                         不设则用 presigned URL（在 S3_ENDPOINT 上签名），
                         TTL 由 S3_PRESIGNED_TTL_S 控制，默认 7 天。
  S3_PUBLIC_URL_STYLE    public URL 拼接风格：
                           - path（默认）：{base}/{bucket}/{key}
                             阿里 OSS / 腾讯 COS / AWS 外网 endpoint 常用
                           - virtual-host：{scheme}://{bucket}.{host}/{key}
                             AWS 风格；MinIO 也可配
                           - root：{base}/{key}（不带 bucket 段）
                             CDN 已绑定到 bucket 根路径时用——例如七牛、
                             又拍、或 CDN 回源时已经把 bucket 加到 host/path
  S3_KEY_PREFIX          key 命名空间前缀，默认 "reports"。最终 key 形如
                         "{prefix}/{dir_name}/{filename}"。
  S3_PRESIGNED_TTL_S     presigned URL 有效期（秒），默认 604800（7 天）。
                         仅在未配 S3_PUBLIC_BASE_URL 时使用。
  S3_FORCE_PATH_STYLE    "true" 时强制 path-style 寻址（MinIO 必需），
                         默认 SDK 自动选择（virtual-host 优先）。
"""
from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse

_DEFAULT_KEY_PREFIX = "reports"
_DEFAULT_PRESIGNED_TTL_S = 7 * 24 * 3600  # 7 天
_DEFAULT_REGION = "us-east-1"
# 与 api_server._NAME_SAFE 对齐：dir/file 名先过这层校验，防 path traversal 进入 key
_KEY_SAFE = re.compile(r"^[0-9A-Za-z._-]+$")


class S3ConfigError(RuntimeError):
    """S3 配置缺失或非法。让上层把它转成 success=false / 500。"""


class S3UploadError(RuntimeError):
    """实际上传或签名失败。"""


class S3Store:
    """通用 S3 兼容存储的最小封装：put_file + 生成调用方可访问的 URL。

    只在第一次用时初始化 boto3 client — 模块 import 不需要 S3 凭据，
    也未 import boto3，这样未启用 S3 的本地开发环境零依赖。
    """

    def __init__(self) -> None:
        self._client = None  # lazy
        self._bucket: Optional[str] = None
        self._endpoint: Optional[str] = None
        self._public_base_url: Optional[str] = None
        self._public_url_style: str = "path"  # "path" | "virtual-host"
        self._key_prefix: str = _DEFAULT_KEY_PREFIX
        self._presigned_ttl: int = _DEFAULT_PRESIGNED_TTL_S
        self._region: str = _DEFAULT_REGION
        self._force_path_style: bool = False
        self._client_kwargs: dict = {}

    # ── 配置 / 初始化 ───────────────────────────────────────

    @classmethod
    def from_env(cls) -> "S3Store":
        """从环境变量构建。缺必需变量时抛 S3ConfigError，调用方决定是否 fail。

        保持惰性：boto3 client 推迟到第一次 put/presign 才创建，避免在
        不真正用 S3 的请求路径上付出握手成本。
        """
        store = cls()
        store._endpoint = os.environ.get("S3_ENDPOINT", "").strip() or None
        store._bucket = os.environ.get("S3_BUCKET", "").strip() or None
        access_key = os.environ.get("S3_ACCESS_KEY_ID", "").strip()
        secret_key = os.environ.get("S3_SECRET_ACCESS_KEY", "").strip()
        region = os.environ.get("S3_REGION", "").strip() or _DEFAULT_REGION
        public_base = os.environ.get("S3_PUBLIC_BASE_URL", "").strip()
        public_style = os.environ.get("S3_PUBLIC_URL_STYLE", "").strip().lower() or "path"
        prefix = os.environ.get("S3_KEY_PREFIX", "").strip() or _DEFAULT_KEY_PREFIX
        ttl_raw = os.environ.get("S3_PRESIGNED_TTL_S", "").strip()
        force_path = os.environ.get("S3_FORCE_PATH_STYLE", "").strip().lower() in ("1", "true", "yes")

        missing = []
        if not store._endpoint:
            missing.append("S3_ENDPOINT")
        if not store._bucket:
            missing.append("S3_BUCKET")
        if not access_key:
            missing.append("S3_ACCESS_KEY_ID")
        if not secret_key:
            missing.append("S3_SECRET_ACCESS_KEY")
        if missing:
            raise S3ConfigError(
                "S3 配置缺失：" + ", ".join(missing) +
                "。如需关闭 S3 上传，请保持这些变量全部未设置；"
                "设置了就必须配齐。"
            )

        _validate_endpoint(store._endpoint)

        if public_base:
            store._public_base_url = _validate_public_base_url(public_base)

        if public_style not in ("path", "virtual-host", "root"):
            raise S3ConfigError(
                f"S3_PUBLIC_URL_STYLE 只支持 path / virtual-host / root，得到 {public_style!r}"
            )
        store._public_url_style = public_style

        # 校验 key prefix：只允许 a-zA-Z0-9-_/，不允许 ..，避免 key 跑出预期命名空间
        if not re.fullmatch(r"[0-9A-Za-z_\-/]+", prefix) or ".." in prefix:
            raise S3ConfigError(
                f"S3_KEY_PREFIX 含非法字符: {prefix!r} — 只允许字母/数字/-/ _ /，且不能含 .."
            )
        store._key_prefix = prefix.strip("/")

        if ttl_raw:
            try:
                store._presigned_ttl = int(ttl_raw)
                if store._presigned_ttl <= 0:
                    raise ValueError
            except ValueError:
                raise S3ConfigError(
                    f"S3_PRESIGNED_TTL_S 必须是正整数秒，得到 {ttl_raw!r}"
                )

        store._region = region
        store._force_path_style = force_path

        # 保存 client 构造参数，推迟到第一次用时再 new
        store._client_kwargs = {
            "endpoint_url": store._endpoint,
            "region_name": region,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }
        return store

    def _ensure_client(self):
        if self._client is None:
            # lazy import boto3：只在真正用 S3 时才需要装它
            import boto3
            from botocore.config import Config as BotoConfig
            kwargs = dict(self._client_kwargs)
            if self._force_path_style:
                kwargs["config"] = BotoConfig(s3={"addressing_style": "path"})
            self._client = boto3.client("s3", **kwargs)
        return self._client

    # ── 公开 API ───────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        """True 表示已正确配置（from_env 成功）。"""
        return self._bucket is not None

    @property
    def bucket(self) -> Optional[str]:
        """已配置的 bucket 名（未配置为 None）。用于日志 / s3:// 路径展示。"""
        return self._bucket

    def build_key(self, dir_name: str, file_name: str) -> str:
        """拼接 S3 key，并做最小安全校验，避免 dir/file 名跑出预期前缀。"""
        if not _KEY_SAFE.match(dir_name) or not _KEY_SAFE.match(file_name):
            raise S3UploadError(
                f"dir/file 名含非法字符（只允许字母数字 ._-）: dir={dir_name!r} file={file_name!r}"
            )
        return f"{self._key_prefix}/{dir_name}/{file_name}"

    def put_file(self, local_path: Path, key: str, content_type: Optional[str] = None) -> None:
        """上传文件到内网 endpoint。失败抛 S3UploadError。"""
        if not self._bucket:
            raise S3ConfigError("S3Store 未正确初始化（缺 S3_BUCKET）")
        if content_type is None:
            content_type, _ = mimetypes.guess_type(str(local_path))
            content_type = content_type or "application/octet-stream"
        from botocore.exceptions import ClientError  # lazy
        try:
            self._ensure_client().put_object(
                Bucket=self._bucket,
                Key=key,
                Body=local_path.read_bytes(),
                ContentType=content_type,
            )
        except ClientError as e:
            raise S3UploadError(f"上传 s3://{self._bucket}/{key} 失败: {e}") from e

    def get_download_url(self, key: str) -> str:
        """返回给调用方的下载 URL。

        优先级：
          1. 配了 S3_PUBLIC_BASE_URL → 永久公开 URL（CDN 或外网 endpoint）
          2. 否则 → presigned GET URL（在 S3_ENDPOINT 上签名；若 endpoint
             是内网地址，外部调用方将无法访问，仅用于同网段调试）
        """
        if self._public_base_url:
            return self._build_public_url(key)
        return self._presigned_get_url(key, self._presigned_ttl)

    def _build_public_url(self, key: str) -> str:
        """按 public_url_style 拼接公开 URL。

        - path: {base}/{bucket}/{key} — bucket 名拼进路径
        - virtual-host: {scheme}://{bucket}.{host}/{key} — bucket 名拼进 host
        - root: {base}/{key} — 不带 bucket 段，CDN 已绑定到 bucket 根时用

        注意：path 风格会把 bucket 名拼进路径。若 bucket 名与 key 前缀相同
        （例如 bucket="reports" + S3_KEY_PREFIX="reports"），最终 URL 会出
        现 .../reports/reports/...，这是预期行为。
        """
        encoded_key = "/".join(quote(seg, safe="._-") for seg in key.split("/"))
        base = self._public_base_url
        assert base is not None  # 调用方保证
        if self._public_url_style == "virtual-host":
            parsed = urlparse(base)
            return f"{parsed.scheme}://{self._bucket}.{parsed.netloc}/{encoded_key}"
        if self._public_url_style == "root":
            return f"{base}/{encoded_key}"
        # 默认 path 风格：{base}/{bucket}/{key}
        return f"{base}/{self._bucket}/{encoded_key}"

    def _presigned_get_url(self, key: str, ttl_s: int) -> str:
        if not self._bucket:
            raise S3ConfigError("S3Store 未正确初始化（缺 S3_BUCKET）")
        from botocore.exceptions import ClientError  # lazy
        try:
            return self._ensure_client().generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=ttl_s,
            )
        except ClientError as e:
            raise S3UploadError(f"生成 presigned URL 失败 s3://{self._bucket}/{key}: {e}") from e


# ── 内部工具 ────────────────────────────────────────────────

def _validate_endpoint(raw: str) -> None:
    """endpoint 必须是 http(s)，不带 path/query/fragment/凭据。"""
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise S3ConfigError(
            f"S3_ENDPOINT 必须是 http(s)，得到 {raw!r}"
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or \
            parsed.username or parsed.password:
        raise S3ConfigError(
            f"S3_ENDPOINT 必须是裸 origin（无 path/query/fragment/凭据），得到 {raw!r}"
        )


def _validate_public_base_url(raw: str) -> str:
    """校验 S3_PUBLIC_BASE_URL：必须是裸 http(s) origin。返回去掉尾斜杠的形式。"""
    trimmed = raw.rstrip("/")
    parsed = urlparse(trimmed)
    if parsed.scheme not in ("http", "https"):
        raise S3ConfigError(
            f"S3_PUBLIC_BASE_URL 必须是 http(s) origin，得到 scheme={parsed.scheme!r}"
        )
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or \
            parsed.username or parsed.password:
        raise S3ConfigError(
            f"S3_PUBLIC_BASE_URL 必须是裸 origin（无 path/query/fragment/凭据），得到 {raw!r}"
        )
    return trimmed


# ── 模块级单例（lazy）────────────────────────────────────────

_store_singleton: Optional[S3Store] = None
_store_init_error: Optional[S3ConfigError] = None


def get_store() -> S3Store:
    """返回模块级 S3Store 单例。未配置时抛 S3ConfigError（已缓存，不会重复抛）。

    未设置任何 S3 env 时也会抛错——上层用 `is_s3_configured()` 先判断，避免
    在「不需要 S3 的部署」里每次请求都抛异常。
    """
    global _store_singleton, _store_init_error
    if _store_singleton is not None:
        return _store_singleton
    if _store_init_error is not None:
        raise _store_init_error
    try:
        _store_singleton = S3Store.from_env()
        return _store_singleton
    except S3ConfigError as e:
        _store_init_error = e
        raise


def is_s3_configured() -> bool:
    """是否配置了 S3 任一必需变量（用来决定是否走 S3 上传路径）。

    采用「任一变量设置即视为想启用 S3」：全部缺失 → False，走原本地落盘路径；
    设置了但缺其他必需变量 → from_env 抛 S3ConfigError，让上层显式失败，
    避免「以为在上传实际没传」的静默偏差。
    """
    return any(os.environ.get(k, "").strip() for k in (
        "S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
    ))
