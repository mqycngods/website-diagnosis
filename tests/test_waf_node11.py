"""验证 Dify render 节点里的 WAF 识别逻辑（NODE11_CODE）。

节点代码是平台执行的 Python 源码字符串，这里把它抽出来单独跑，
锁住「什么情况算 WAF 拦截」这个判断——判错了要么白重试要么掩盖真错误。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_GEN = _ROOT / "dify-site-diagnosis-zh" / "generate_dsl.py"


@pytest.fixture(scope="module")
def node_module():
    """执行 generate_dsl 里 NODE11_CODE 定义的 _waf_blocked / _encode_payload。"""
    src = _GEN.read_text(encoding="utf-8")
    m = re.search(r"^NODE11_CODE = '''(.*?)'''$", src, re.S | re.M)
    assert m, "未在 generate_dsl.py 中找到 NODE11_CODE"
    code = m.group(1)
    ns = {"__name__": "node11"}
    exec(compile(code, "NODE11_CODE", "exec"), ns)
    return ns


class FakeResp:
    def __init__(self, status_code, content_type, text=""):
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.text = text


def test_waf_blocked_html_403(node_module):
    """Render WAF 拦截页：403 + text/html。"""
    r = FakeResp(403, "text/html; charset=UTF-8", "<html>Blocked</html>")
    assert node_module["_waf_blocked"](r) is True


def test_waf_blocked_json_403_is_not_waf(node_module):
    """业务侧的 403 是 JSON —— 不该被当 WAF 误判后重试。"""
    r = FakeResp(403, "application/json", '{"ok": false, "error": "forbidden"}')
    assert node_module["_waf_blocked"](r) is False


def test_waf_blocked_401_not_treated_as_waf(node_module):
    """鉴权失败（401）与 WAF 无关，不能触发 Base64 重试。"""
    r = FakeResp(401, "application/json", '{"detail": "invalid key"}')
    assert node_module["_waf_blocked"](r) is False


def test_waf_blocked_200_not_waf(node_module):
    r = FakeResp(200, "application/json", '{"ok": true}')
    assert node_module["_waf_blocked"](r) is False


def test_waf_blocked_detects_body_signature(node_module):
    """偶尔 Content-Type 缺失，靠正文特征仍能识别。"""
    r = FakeResp(403, "", "Your request was blocked by this site's web application firewall (WAF).")
    assert node_module["_waf_blocked"](r) is True


def test_b64_payload_marks_encoding(node_module):
    import base64
    payload = {
        "run_id": "r1", "url": "https://x.com",
        "scores": '{"a": 1}',
        "plan_md": "检查 ../../etc/passwd",
    }
    enc = node_module["_b64_payload"](payload)
    assert enc["encoding"] == "base64"
    assert base64.b64decode(enc["plan_md"]).decode() == "检查 ../../etc/passwd"
    assert base64.b64decode(enc["scores"]).decode() == '{"a": 1}'
    # 编码后请求体里不再有 WAF 特征串
    import json
    assert "../../etc/passwd" not in json.dumps(enc, ensure_ascii=False)


def test_b64_payload_keeps_other_fields(node_module):
    payload = {"run_id": "r1", "url": "https://x.com", "domain": "x.com",
               "scores": "{}", "plan_md": ""}
    enc = node_module["_b64_payload"](payload)
    assert enc["run_id"] == "r1" and enc["url"] == "https://x.com"
    assert enc["domain"] == "x.com"


# ── 请求顺序：一律先发 Base64 ────────────────────────────────

class _FakeResp:
    def __init__(self, status, ctype, payload):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self._payload = payload
        self.text = payload if isinstance(payload, str) else ""

    def json(self):
        if isinstance(self._payload, dict):
            return self._payload
        raise ValueError("not json")


def _fake_requests(responses, sent):
    class FakeExceptions:
        class Timeout(Exception):
            pass
        class ConnectionError(Exception):
            pass

    class FakeRequests:
        exceptions = FakeExceptions

        @staticmethod
        def post(endpoint, json=None, headers=None, timeout=None):
            sent.append(json)
            return responses.pop(0)

    return FakeRequests


_OK = {"ok": True, "data": {"geo_score": 42, "label_zh": "中等",
                            "report_html": "<html>", "report_markdown": "md",
                            "download_url_html": "h", "download_url_md": "m"}}
_EMPTY = {"ok": False, "error": "", "geo_score": 0, "label_zh": "", "report_html": "",
          "report_markdown": "", "download_url_html": "", "download_url_md": ""}
_PAYLOAD = {"run_id": "r1", "url": "https://x.com", "domain": "x.com",
            "scores": "{}", "plan_md": "检查 ../../etc/passwd"}


def test_post_sends_base64_first_and_only_once(node_module):
    """服务端支持编码：只发一次，且首发即 Base64、不含明文穿越串。"""
    import json
    sent, responses = [], [_FakeResp(200, "application/json", _OK)]
    node_module["requests"] = _fake_requests(responses, sent)
    out = node_module["_post"]("ep", {}, _PAYLOAD, 10, dict(_EMPTY))
    assert len(sent) == 1
    assert sent[0]["encoding"] == "base64"
    assert "../../etc/passwd" not in json.dumps(sent[0], ensure_ascii=False)
    assert out["geo_score"] == 42


def test_post_falls_back_to_plaintext_for_old_server(node_module):
    """旧服务端把 Base64 当明文（响应非 JSON）→ 回落明文重试一次。"""
    sent, responses = [], [
        _FakeResp(200, "text/html", "<html>渲染失败</html>"),
        _FakeResp(200, "application/json", _OK),
    ]
    node_module["requests"] = _fake_requests(responses, sent)
    out = node_module["_post"]("ep", {}, _PAYLOAD, 10, dict(_EMPTY))
    assert len(sent) == 2
    assert "encoding" not in sent[1]
    assert out["geo_score"] == 42


def test_post_raises_when_both_blocked(node_module):
    """Base64 与明文都被 WAF 拦 → 明确抛错，不伪装成成功。"""
    sent, responses = [], [
        _FakeResp(403, "text/html", "<html>WAF</html>"),
        _FakeResp(403, "text/html", "<html>WAF</html>"),
    ]
    node_module["requests"] = _fake_requests(responses, sent)
    with pytest.raises(RuntimeError, match="WAF"):
        node_module["_post"]("ep", {}, _PAYLOAD, 10, dict(_EMPTY))
    assert len(sent) == 2

