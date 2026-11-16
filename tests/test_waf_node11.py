"""验证 Dify render 节点（NODE11_CODE）的 WAF 识别与上报逻辑。

节点代码是平台执行的 Python 源码字符串，这里把它抽出来单独跑。锁住两件事：
1. 什么情况算 WAF 拦截（判错会掩盖真错误，或对正常响应误报失败）
2. 被 WAF 拦时不重试、如实上报（WAF 在服务之前，重试没意义）
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_GEN = _ROOT / "dify-site-diagnosis-zh" / "generate_dsl.py"


@pytest.fixture(scope="module")
def node_module():
    """执行 generate_dsl 里 NODE11_CODE 定义的所有函数。"""
    src = _GEN.read_text(encoding="utf-8")
    m = re.search(r"^NODE11_CODE = '''(.*?)'''$", src, re.S | re.M)
    assert m, "未在 generate_dsl.py 中找到 NODE11_CODE"
    ns = {"__name__": "node11"}
    exec(compile(m.group(1), "NODE11_CODE", "exec"), ns)
    return ns


class FakeResp:
    def __init__(self, status_code, content_type, payload):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self._payload = payload
        self.text = payload if isinstance(payload, str) else ""

    def json(self):
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("not json")


# ── WAF 识别 ─────────────────────────────────────────────────

def test_waf_blocked_html_403(node_module):
    """Render WAF 拦截页：403 + text/html。"""
    r = FakeResp(403, "text/html; charset=UTF-8", "<html>Blocked</html>")
    assert node_module["_waf_blocked"](r) is True


def test_waf_blocked_json_403_is_not_waf(node_module):
    """业务侧的 403 是 JSON —— 不该被当 WAF 误判。"""
    r = FakeResp(403, "application/json", '{"ok": false, "error": "forbidden"}')
    assert node_module["_waf_blocked"](r) is False


def test_waf_blocked_401_not_treated_as_waf(node_module):
    """鉴权失败（401）与 WAF 无关。"""
    r = FakeResp(401, "application/json", '{"detail": "invalid key"}')
    assert node_module["_waf_blocked"](r) is False


def test_waf_blocked_200_not_waf(node_module):
    r = FakeResp(200, "application/json", '{"ok": true}')
    assert node_module["_waf_blocked"](r) is False


def test_waf_blocked_detects_body_signature(node_module):
    """Content-Type 缺失时，靠正文特征仍能识别。"""
    r = FakeResp(403, "", "Your request was blocked by this site's web application firewall (WAF).")
    assert node_module["_waf_blocked"](r) is True


# ── 不重试、如实上报 ─────────────────────────────────────────

def _fake_requests(responses, sent):
    class FakeExceptions:
        class Timeout(Exception):
            pass
        class ConnectionError(Exception):
            pass

    class FakeRequests:
        exceptions = FakeExceptions

        @staticmethod
        def post(endpoint, json=None, data=None, headers=None, timeout=None):
            sent.append({"json": json, "data": data, "headers": headers})
            return responses.pop(0)

    return FakeRequests


_OK_DATA = {"geo_score": 42, "label_zh": "中等", "report_html": "<html>",
            "report_markdown": "md", "download_url_html": "h", "download_url_md": "m"}

_ARGS = ("r1", "https://x.com", "x.com", '{"a": 1}', "# 计划", "https://api", "tok")


def test_main_success(node_module):
    sent = []
    node_module["requests"] = _fake_requests(
        [FakeResp(200, "application/json", {"ok": True, "data": _OK_DATA})], sent)
    out = node_module["main"](*_ARGS)
    assert out["ok"] is True
    assert out["geo_score"] == 42
    assert len(sent) == 1


def test_main_sends_json_with_text_plain_content_type(node_module):
    """请求体是 JSON 字符串，但 Content-Type 必须是 text/plain。

    Render 边缘 WAF 只在 Content-Type 为 application/json 时解析正文；
    换成 text/plain 就不再检查内容（实测含 ../../etc/passwd 也放行）。
    """
    import json
    sent = []
    node_module["requests"] = _fake_requests(
        [FakeResp(200, "application/json", {"ok": True, "data": _OK_DATA})], sent)
    node_module["main"](*_ARGS)

    req = sent[0]
    assert req["headers"]["Content-Type"] == "text/plain"
    assert req["json"] is None, "不应走 json= 参数（那会设成 application/json）"
    body = json.loads(req["data"])
    assert body["scores"] == '{"a": 1}'
    assert body["plan_md"] == "# 计划"
    assert "encoding" not in body


def test_main_waf_blocked_reports_without_retry(node_module):
    """被 WAF 拦时只发一次请求，并返回明确的失败原因。"""
    sent = []
    node_module["requests"] = _fake_requests(
        [FakeResp(403, "text/html", "<html>WAF</html>")], sent)
    out = node_module["main"](*_ARGS)
    assert out["ok"] is False
    assert "WAF" in out["error"]
    assert len(sent) == 1, "WAF 拦截不应重试"


def test_main_business_error_passthrough(node_module):
    """业务返回 ok=false 时透传错误，不当成 WAF。"""
    sent = []
    node_module["requests"] = _fake_requests(
        [FakeResp(200, "application/json", {"ok": False, "error": "bad scores"})], sent)
    out = node_module["main"](*_ARGS)
    assert out["ok"] is False
    assert out["error"] == "bad scores"
