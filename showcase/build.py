"""把 page.html（Artifact 片段：没有 doctype/html/head/body）包装成可离线直接双击打开的 index.html。"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
frag = (HERE / "page.html").read_text(encoding="utf-8")
cut = frag.index("</style>") + len("</style>")  # title、字体、样式放进 head，其余放进 body
head, body = frag[:cut], frag[cut:]
html = (
    "<!doctype html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
    "<!-- 由 showcase/build.py 从 page.html 生成；离线打开时 Google Fonts 加载失败会自动用系统字体 -->\n"
    + head + "\n</head>\n<body>" + body + "\n</body>\n</html>\n"
)
(HERE / "index.html").write_text(html, encoding="utf-8")
print("index.html", len(html) // 1024, "KB")
