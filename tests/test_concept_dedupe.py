"""analyzer 补充发现与 agent 主 findings 的概念级判重回归测试。

背景（2026-11-19 prod/43 报告事故）：
  agent 主 finding #05「全站零外部引用，首页 4123 词只有 2 个具体数字」
  与 analyzer 三条补充发现（零第三方外链 / 低数字密度 / 未链向权威平台）
  同根因（缺第三方背书外链），却因 _CONCEPTS 表缺「外部引用」桶而判重失效，
  同一根因在主清单与补充发现里重复出现。
修复：_CONCEPTS 增加 external_refs 桶。本测试锁定判重行为与误伤边界。
"""
import re

# 与 render/build.py 的 _CONCEPTS 保持一致（独立副本，避免拉起完整 build）
_CONCEPTS = [
    ("faq", r"faq"),
    ("og", r"og:|open ?graph"),
    ("canonical", r"canonical"),
    ("h1", r"\bh1\b"),
    ("sameas", r"sameas"),
    ("description", r"description"),
    ("author", r"author|作者"),
    ("sitemap", r"sitemap"),
    ("robots", r"robots"),
    ("llms", r"llms"),
    ("entity", r"organization|实体|@id"),
    ("schema", r"schema|json-?ld|结构化"),
    ("citability", r"可引用|citability"),
    ("external_refs", r"外链|外部引用|第三方|权威|external|引用.*来源|来源.*引用|媒体|报道|背书"),
]


def _concepts(text):
    low = (text or "").lower()
    return {name for name, pat in _CONCEPTS if re.search(pat, low)}


def _primary(text):
    low = (text or "").lower()
    for name, pat in _CONCEPTS:
        if re.search(pat, low):
            return name
    return None


# ── 判重命中：同根因的 analyzer 补充发现应被 agent 主 finding 吸收 ──

def test_authority_platform_link_dedupes_with_zero_external_refs():
    agent_titles = {"全站零外部引用，首页 4123 词只有 2 个具体数字"}
    analyzer_title = "主页未链向任何权威外部平台"
    p = _primary(analyzer_title)
    assert p == "external_refs"
    agent_concepts = [_concepts(t) for t in agent_titles]
    assert any(p in ac for ac in agent_concepts)


def test_third_party_link_density_dedupes_with_zero_external_refs():
    agent_titles = {"全站零外部引用，首页 4123 词只有 2 个具体数字"}
    analyzer_title = "正文 4123 词但无任何指向独立第三方来源/评测的外链"
    p = _primary(analyzer_title)
    assert p == "external_refs"
    agent_concepts = [_concepts(t) for t in agent_titles]
    assert any(p in ac for ac in agent_concepts)


# ── 误伤边界：可引用性 finding 不进 external_refs 桶 ──

def test_citability_title_not_in_external_refs():
    """「可引用性」含「引用」二字，不能被 external_refs 误吞——
    否则 analyzer 的权威外链问题会被可引用性 finding 错误判重。"""
    assert "external_refs" not in _concepts("可引用性均分 40.8：26 个正文区块")
    assert "external_refs" not in _concepts("段落可引用性低")
    assert _primary("可引用性均分 40.8") == "citability"


def test_external_refs_titles_still_match():
    for t in ("主页未链向任何权威外部平台", "全站零外部引用",
              "无任何指向独立第三方来源的外链", "缺第三方评测背书"):
        assert _primary(t) == "external_refs", t


# ── 既有桶不受影响 ──

def test_other_buckets_unchanged():
    assert _primary("首页缺 Organization 实体") == "entity"
    assert _primary("缺 FAQ 页面") == "faq"
    assert _primary("robots.txt 全禁") == "robots"
