"""HTTP 缓存 — 同一诊断会话内同一 URL（含 UA 维度）只请求一次。

两级：
  MemoryCache    进程内字典（collect 默认开启：fetch/bots 基线/citability/sitemap 不重复请求）
  FileCache      可选文件系统缓存（调试用：改评分/模板后重跑不重新抓站）

缓存的是 (status_code, headers, text, final_url) 四元组，足以支撑
BeautifulSoup 解析、challenge 判定与响应头分析。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def cache_key(url: str, ua: str = "") -> str:
    return hashlib.sha256(f"{ua}\n{url}".encode("utf-8")).hexdigest()[:24]


class CachedResponse:
    """requests.Response 的最小替身：status_code / headers / text / url。"""

    def __init__(self, status_code: int, headers: dict, text: str, url: str):
        self.status_code = status_code
        self.headers = headers
        self.text = text
        self.url = url
        self.content = text.encode("utf-8", errors="replace")
        self.encoding = "utf-8"

    def json(self):
        return json.loads(self.text)

    def to_dict(self):
        return {"status_code": self.status_code, "headers": dict(self.headers),
                "text": self.text, "url": self.url}

    @classmethod
    def from_dict(cls, d):
        return cls(d["status_code"], d.get("headers") or {}, d.get("text") or "",
                   d.get("url") or "")

    @classmethod
    def from_response(cls, resp):
        return cls(resp.status_code, dict(resp.headers), resp.text or "", resp.url)


class MemoryCache:
    def __init__(self):
        self._d = {}
        import threading
        self._lock = threading.Lock()

    def get(self, url: str, ua: str = ""):
        return self._d.get(cache_key(url, ua))

    def set(self, url: str, ua: str, resp: CachedResponse):
        with self._lock:
            self._d[cache_key(url, ua)] = resp


class FileCache:
    """文件系统缓存：每 URL 一个 JSON 文件。供调试/重跑，不做过期管理。"""

    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._mem = MemoryCache()

    def get(self, url: str, ua: str = ""):
        hit = self._mem.get(url, ua)
        if hit:
            return hit
        f = self.dir / f"{cache_key(url, ua)}.json"
        if f.exists():
            try:
                resp = CachedResponse.from_dict(json.loads(f.read_text(encoding="utf-8")))
                self._mem.set(url, ua, resp)
                return resp
            except Exception:
                return None
        return None

    def set(self, url: str, ua: str, resp: CachedResponse):
        self._mem.set(url, ua, resp)
        try:
            (self.dir / f"{cache_key(url, ua)}.json").write_text(
                json.dumps(resp.to_dict(), ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
