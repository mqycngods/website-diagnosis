#!/usr/bin/env python3
"""把 templates/ 目录内联为 render/inline_templates.py（单 Python 文件）。

用途：平台只接受 Python 代码上传、不能传 templates/ 目录时，
用本脚本重新生成 inline_templates.py 一并上传即可。

用法: python scripts/inline_templates.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TPL_DIR = ROOT / "templates"
OUT = ROOT / "render" / "inline_templates.py"

HEADER = '''"""自动生成的内联模板 — 由 scripts/inline_templates.py 产生，请勿手改。

平台部署模式：templates/ 目录无法上传时，render/html.py 优先从这里取模板。
本地开发模式：修改 templates/*.j2 后重新运行 scripts/inline_templates.py。
"""
TEMPLATES = {
'''


def main():
    files = sorted(TPL_DIR.rglob("*.j2"))
    if not files:
        raise SystemExit("templates/ 下没有 .j2 文件")

    parts = [HEADER]
    total = 0
    for f in files:
        rel = f.relative_to(TPL_DIR).as_posix()  # 例: sections/citability.html.j2
        text = f.read_text(encoding="utf-8")
        total += len(text)
        # repr() 处理所有转义，安全
        parts.append(f"    {rel!r}: {text!r},\n")
    parts.append("}\n")

    OUT.write_text("".join(parts), encoding="utf-8")
    print(f"✓ {OUT} 已生成：{len(files)} 个模板，{total/1024:.1f} KB 源文本")


if __name__ == "__main__":
    main()
