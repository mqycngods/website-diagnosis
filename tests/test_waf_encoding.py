"""WAF 规避编解码的回归测试。

背景：Render 边缘 WAF 会对请求体做内容匹配，正文里出现 "../../etc/passwd"
这类目录穿越特征串时，请求在到达本服务前就被 403 拦掉。客户端可把大文本
Base64 后再发，服务端按 encoding 声明解码。这些测试锁住解码行为与向后兼容。
"""
from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _load_api_server():
    """直接加载 tools/api_server.py（它不在包的 __init__ 里）。"""
    if "api_server" in sys.modules:
        return sys.modules["api_server"]
    spec = importlib.util.spec_from_file_location(
        "api_server", _ROOT / "tools" / "api_server.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["api_server"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def api():
    return _load_api_server()


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


# ── _maybe_b64 ───────────────────────────────────────────────

def test_maybe_b64_roundtrip_with_traversal_text(api):
    """被 WAF 命中的原文，编码后能原样还原。"""
    risky = "修复：检查 ../../etc/passwd 与 ../../../etc/shadow"
    assert api._maybe_b64(_b64(risky)) == risky


def test_maybe_b64_passthrough_when_not_base64(api):
    """不是合法 Base64 就原样返回，不吞内容。"""
    plain = "这不是 base64，包含中文与标点。"
    assert api._maybe_b64(plain) == plain


def test_maybe_b64_handles_empty_and_none(api):
    assert api._maybe_b64("") == ""
    assert api._maybe_b64(None) is None


# ── _decoded ─────────────────────────────────────────────────

def test_decoded_plaintext_unchanged(api):
    """未声明 encoding 时按明文取，历史行为不变。"""
    body = {"plan_md": "../../etc/passwd"}
    assert api._decoded(body, "plan_md") == "../../etc/passwd"


def test_decoded_with_global_encoding_flag(api):
    risky = "../../etc/passwd"
    body = {"encoding": "base64", "plan_md": _b64(risky)}
    assert api._decoded(body, "plan_md") == risky


def test_decoded_with_per_field_encoding_flag(api):
    risky = "../../proc/self/environ"
    body = {"plan_md_encoding": "base64", "plan_md": _b64(risky)}
    assert api._decoded(body, "plan_md") == risky


def test_decoded_encoding_flag_case_insensitive(api):
    body = {"encoding": "BASE64", "plan_md": _b64("abc")}
    assert api._decoded(body, "plan_md") == "abc"


def test_decoded_empty_encoding_value_keeps_plaintext(api):
    """模板变量为空时 encoding="" —— 必须理解为「未编码」，不能瞎解码。"""
    body = {"encoding": "", "plan_md": "明文计划"}
    assert api._decoded(body, "plan_md") == "明文计划"


def test_decoded_bad_base64_falls_back_to_raw(api):
    """声明了 base64 但内容不是——原样返回，不让请求整体失败。"""
    body = {"encoding": "base64", "plan_md": "not-valid-base64!!"}
    assert api._decoded(body, "plan_md") == "not-valid-base64!!"


def test_decoded_covers_scores_key(api):
    import json
    scores = {"dimensions": {"citability": {"score": 17}}}
    body = {"encoding": "base64", "scores": _b64(json.dumps(scores))}
    decoded = api._as_json(api._decoded(body, "scores"))
    assert decoded == scores


def test_scores_decoded_then_parsed_keeps_dimensions(api):
    """端到端：Base64 的 scores 解码 + 解析后仍是完整对象。"""
    import json
    payload = {"description": "DClaw 机器侧近乎空白", "dimensions": {"content": {"score": 15}}}
    body = {"encoding": "base64", "scores": _b64(json.dumps(payload, ensure_ascii=False))}
    assert api._as_json(api._decoded(body, "scores")) == payload
