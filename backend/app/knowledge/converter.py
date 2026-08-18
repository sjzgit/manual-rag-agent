"""docx → markdown 转换：mammoth 转 HTML，BeautifulSoup 清洗非操作内容，图片提取落盘。

清洗规则（按标题关键词 + 位置删除）：
- 封面：首个 H1~H4 标题之前的所有内容；
- 目录 / 免责声明 / 版权声明 / 文件修改记录 / 阅读对象：命中关键词的标题整段删除。

注意：mammoth 对自定义标题样式需按真实 docx 的样式名核对 STYLE_MAP；复杂排版（文本框/
艺术字/SmartArt）保真度有限，转换结果保留 preview.html 供人工核对，可随时 reprocess。
"""
import re
from pathlib import Path
from urllib.parse import quote

import mammoth
from bs4 import BeautifulSoup
from markdownify import markdownify as md_convert

_HEADING_TAGS = {"h1", "h2", "h3", "h4"}
_HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}

# mammoth 样式映射：Word 标题样式 → HTML 标题（含中文 Word 常见的自定义样式需按样本补充）
STYLE_MAP = (
    "p[style-name='Heading 1'] => h1:fresh\n"
    "p[style-name='Heading 2'] => h2:fresh\n"
    "p[style-name='Heading 3'] => h3:fresh\n"
    "p[style-name='Heading 4'] => h4:fresh\n"
    "p[style-name='Title'] => h1:fresh\n"
    "p[style-name='标题 1'] => h1:fresh\n"
    "p[style-name='标题 2'] => h2:fresh\n"
    "p[style-name='标题 3'] => h3:fresh\n"
    "p[style-name='标题 4'] => h4:fresh\n"
)

# 非操作章节标题关键词（命中即删除该章节整段）
_SKIP_HEADINGS = (
    "目录",
    "免责",
    "版权",
    "Copyright",
    "修改记录",
    "修订记录",
    "变更记录",
    "版本记录",
    "文档历史",
    "阅读对象",
    "适用对象",
    "读者对象",
    "适用人群",
)


def _is_skip_heading(text: str) -> bool:
    t = re.sub(r"\s+", "", text)
    if not t:
        return False
    return any(kw in t for kw in _SKIP_HEADINGS)


def _remove_section(heading) -> None:
    """删除标题及其到下一个同级/上级标题之间的全部内容。"""
    level = _HEADING_LEVEL[heading.name]
    node = heading
    while node is not None:
        nxt = node.next_sibling
        node.decompose()
        if nxt is not None and nxt.name in _HEADING_TAGS and _HEADING_LEVEL[nxt.name] <= level:
            break
        node = nxt


def clean_html(html: str) -> str:
    """清洗 HTML：删除封面与目录/免责/版权/修改记录/阅读对象等非操作章节。"""
    soup = BeautifulSoup(html, "html.parser")
    # 1. 删除首个标题之前的封面/前言
    first = soup.find(_HEADING_TAGS)
    if first is not None:
        node = first.previous_sibling
        while node is not None:
            nxt = node.previous_sibling
            node.decompose()
            node = nxt
    # 2. 删除非操作章节
    for h in list(soup.find_all(_HEADING_TAGS)):
        if _is_skip_heading(h.get_text(" ", strip=True)):
            _remove_section(h)
    return str(soup)


def _rewrite_img_src(doc_name: str, html: str) -> str:
    """将预览 HTML 中的 ./media/x.png 相对路径改写为 /api/images/{doc}/{filename} 绝对链接。

    md 文件保持相对路径（供切片与运行时 to_source_chunk 改写），预览 HTML 单独改写，
    避免 iframe 加载时图片相对路径解析到 /admin/api/... 导致 404。
    """
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if src.startswith("./media/"):
            filename = src.split("/")[-1]
            img["src"] = f"/api/images/{quote(doc_name)}/{quote(filename)}"
    return str(soup)


def convert_docx(docx_path: Path, dest_dir: Path) -> tuple[str, str]:
    """转换 docx 为清洗后的 markdown，图片提取到 dest_dir/media/，预览 HTML 存 dest_dir/preview.html。

    返回 (markdown 文本, 预览 HTML 文本)。
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    media_dir = dest_dir / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    counter = {"n": 0}

    def _convert_image(image) -> dict:
        ext = "png"
        if image.content_type and "/" in image.content_type:
            ext = image.content_type.split("/")[-1]
        counter["n"] += 1
        filename = f"图片-{counter['n']:03d}.{ext}"
        with image.open() as image_file:
            (media_dir / filename).write_bytes(image_file.read())
        return {"src": f"./media/{filename}"}

    with open(docx_path, "rb") as f:
        result = mammoth.convert_to_html(
            f,
            style_map=STYLE_MAP,
            convert_image=mammoth.images.img_element(_convert_image),
        )

    html = clean_html(result.value)
    md_text = md_convert(html, heading_style="ATX")
    preview_html = _rewrite_img_src(dest_dir.name, html)
    (dest_dir / "preview.html").write_text(preview_html, encoding="utf-8")
    return md_text, preview_html
