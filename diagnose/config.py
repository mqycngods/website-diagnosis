"""统一配置加载：config/default.yaml 覆盖代码内默认值。

yaml 不可用时（未安装 pyyaml）静默使用默认值，保证最小依赖可运行。
用法：from diagnose.config import cfg
      cfg("scoring.weights.citability", 0.3125)
"""
from __future__ import annotations

from pathlib import Path

_DEFAULTS = {
    "crawler": {
        "timeout": 30, "robots_timeout": 15, "probe_timeout": 15,
        "sitemap_timeout": 10, "sitemap_limit": 500,
        "discovery_sitemap_limit": 200,
        "sample_articles": 5, "sample_products": 3, "polite_delay": 1,
    },
    "scoring": {
        "weights": {
            "citability": 0.3125, "content": 0.25, "technical": 0.1875,
            "schema": 0.125, "platform": 0.125,
        },
    },
    "citability": {
        "dim_max": {
            "answer_block_quality": 30, "self_containment": 25,
            "structural_readability": 20, "statistical_density": 15,
            "uniqueness_signals": 10,
        },
    },
    "freshness": {"tiers": [90, 365, 730]},
}

_CONFIG = None


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load(path: str = None) -> dict:
    """加载配置：default.yaml（若存在且 pyyaml 可用）覆盖 _DEFAULTS。"""
    global _CONFIG
    if _CONFIG is not None and path is None:
        return _CONFIG

    cfg_path = Path(path) if path else \
        Path(__file__).resolve().parent.parent / "config" / "default.yaml"
    data = _DEFAULTS
    if cfg_path.exists():
        try:
            import yaml
            user = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                data = _deep_merge(_DEFAULTS, user)
        except ImportError:
            pass
    if path is None:
        _CONFIG = data
    return data


def cfg(dotpath: str, default=None):
    """点路径取值：cfg("crawler.timeout", 30)。"""
    node = load()
    for part in dotpath.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def reload(path: str = None):
    """强制重载（测试用）。"""
    global _CONFIG
    _CONFIG = None
    return load(path)
