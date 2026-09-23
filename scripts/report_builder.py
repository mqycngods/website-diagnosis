"""兼容薄壳 — 实现已迁至 diagnose/recommendations/engine.py。

保留本模块只为不破坏既有 `from scripts.report_builder import build_client_report_data`
调用（render/build.py）。新代码请直接从 diagnose.recommendations.engine 导入。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from diagnose.recommendations.engine import (  # noqa: E402,F401
    PRIORITY_THRESHOLDS, ClientReportData, build_client_report_data,
)
