"""run_id 暂存后端：memory / redis 两种实现的契约一致性测试。

redis 用例需要本地能连到 Redis，通过环境变量提供：
    TEST_REDIS_URL=redis://:pass@127.0.0.1:6379/15

未设置时仅跑 memory 用例（CI 默认行为）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 导入模块前必须设好 API key，避免落到默认值
os.environ.setdefault("SITE_DIAGNOSIS_API_KEY", "test-key")

from tools import api_server as s  # noqa: E402


def _exercise(store: s._RunStore) -> None:
    """put → get → 读改写 → 404，两种后端行为必须一致。"""
    store.put("r1", {"url": "https://a.example.com", "fetch": {"title": "A"}})
    assert store.get("r1")["url"] == "https://a.example.com"

    data = store.get("r1")
    data["articles"] = {"pages": [1, 2, 3]}
    store.put("r1", data)
    assert store.get("r1")["articles"] == {"pages": [1, 2, 3]}

    with pytest.raises(s.HTTPException) as ei:
        store.get("does-not-exist")
    assert ei.value.status_code == 404
    assert "不存在或已过期" in ei.value.detail

    # 中文不能被转义成 \uXXXX（ensure_ascii=False）
    store.put("r2", {"title": "中文标题"})
    assert store.get("r2")["title"] == "中文标题"

    store.delete("r1")
    with pytest.raises(s.HTTPException):
        store.get("r1")


def test_memory_store():
    _exercise(s._MemoryStore())


def test_memory_store_capacity_evicts_oldest():
    store = s._MemoryStore()
    for i in range(s._STORE_MAX + 5):
        store.put(f"k{i}", {"i": i})
    assert len(store._data) == s._STORE_MAX
    # 最早写入的应被淘汰
    with pytest.raises(s.HTTPException):
        store.get("k0")


@pytest.mark.skipif(
    not os.environ.get("TEST_REDIS_URL"),
    reason="未设置 TEST_REDIS_URL，跳过 Redis 用例",
)
def test_redis_store():
    store = s._RedisStore(os.environ["TEST_REDIS_URL"])
    _exercise(store)


@pytest.mark.skipif(
    not os.environ.get("TEST_REDIS_URL"),
    reason="未设置 TEST_REDIS_URL，跳过 Redis 用例",
)
def test_redis_store_sets_ttl():
    store = s._RedisStore(os.environ["TEST_REDIS_URL"])
    store.put("ttl-probe", {"x": 1})
    ttl = store._r.ttl(store._key("ttl-probe"))
    assert 0 < ttl <= s._STORE_TTL


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setattr(s, "_STORE_BACKEND", "bogus")
    with pytest.raises(RuntimeError, match="未知 STORE_BACKEND"):
        s._make_store()


def test_redis_backend_without_url_raises(monkeypatch):
    monkeypatch.setattr(s, "_STORE_BACKEND", "redis")
    monkeypatch.setattr(s, "_REDIS_URL", "")
    with pytest.raises(RuntimeError, match="未配置 REDIS_URL"):
        s._make_store()
