"""父子切片器：纯代码（非 LLM）实现父子切片方案 v2.1。

父切片 = 动态探测出的「模块层级」功能模块（上下文组织单元，不向量化）；
子切片 = 模块的下级标题 / 语义段（检索单元，向量化）。
依据 docs/文档切片策略.md 第七节，将清洗后的 markdown 解析为标题层级树后：
- 按各层「直属正文 + 图片 × IMG_WEIGHT」的内容权重探测模块层级（不再固定 H3）；
- 每个模块层级节点生成一个父切片（保留完整子树与全部图片引用，不合并/不拆分）；
- 模块有下级标题时每个下级标题独立成子切片（模块直属引言并入首片，更深层级折叠）；
- 无下级标题时按语义段落切分（目标 100~300 纯文本字符，<50 字并入相邻片）；
- 层级浅于模块层、但自身带正文/图片的「错位模块」也按模块切片，避免丢内容；
- 首个标题节点（层级浅于模块层）的直属正文（封面/目录）丢弃。
"""
import re
import uuid
from dataclasses import dataclass, field

from app.knowledge.models import ChunkSpec

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_IMG_RE = re.compile(r"!\[[^\]]*\]\([^)]+\)")

# 子切片目标区间（纯文本字符，不含图片链接）；超长单段暂不强制拆分，由父切片兜底
_TARGET_MIN = 100
_MERGE_BELOW = 50

# 模块层级探测：一张截图约等于多少字符的内容信号
_IMG_WEIGHT = 50


@dataclass
class _Node:
    """解析出的标题节点：含直属正文段落与子标题。"""

    level: int
    text: str
    body: list[str] = field(default_factory=list)
    children: list["_Node"] = field(default_factory=list)


def _text_len(text: str) -> int:
    """纯文本字符数（去除图片引用）。"""
    return len(_IMG_RE.sub("", text))


def _count_images(text: str) -> int:
    return len(_IMG_RE.findall(text))


def _parse(text: str) -> _Node:
    """将 markdown 解析为标题层级树。正文按空行分隔为段落，归属最近标题。"""
    root = _Node(level=0, text="")
    stack = [root]
    buf: list[str] = []

    def flush() -> None:
        if buf:
            stack[-1].body.append("\n".join(buf))
            buf.clear()

    for raw in text.splitlines():
        line = raw.strip()
        m = _HEADING_RE.match(line)
        if m:
            flush()
            level = len(m.group(1))
            node = _Node(level=level, text=m.group(2).strip())
            while stack and stack[-1].level >= level:
                stack.pop()
            stack[-1].children.append(node)
            stack.append(node)
        elif line:
            buf.append(line)
        else:
            flush()
    flush()
    return root


def _join(parts: list[str]) -> str:
    return "\n\n".join(p for p in parts if p)


def _has_direct_content(node: _Node) -> bool:
    """节点直属 body 是否含正文或图片（不含子节点）。"""
    body = "\n".join(node.body)
    return _text_len(body) > 0 or _count_images(body) > 0


def _detect_module_level(root: _Node) -> int:
    """按直属内容权重探测模块层级：weight(L) = 直属字符数 + 图片数 × IMG_WEIGHT。

    取权重最大的层级；并列时取更深层级（更细颗粒）。无任何标题正文时兜底返回 1。
    """
    weights: dict[int, int] = {}

    def visit(node: _Node) -> None:
        if node.level > 0:
            body = "\n".join(node.body)
            w = _text_len(body) + _count_images(body) * _IMG_WEIGHT
            if w > 0:
                weights[node.level] = weights.get(node.level, 0) + w
        for child in node.children:
            visit(child)

    visit(root)
    if not weights:
        return 1
    return max(weights, key=lambda lv: (weights[lv], lv))


def _collect_subtree(node: _Node) -> list[str]:
    """递归收集节点完整子树：直属正文 + 各级子标题 + 其正文，展平为段落列表。"""
    parts = list(node.body)
    for child in node.children:
        parts.append(f"{'#' * child.level} {child.text}")
        parts.extend(_collect_subtree(child))
    return parts


def _parent_content(breadcrumb: list[str], node: _Node) -> str:
    """父切片正文：面包屑 + 模块完整子树。"""
    parts = [f"## {' > '.join(breadcrumb)}", *_collect_subtree(node)]
    return _join(parts)


def _split_segments(paragraphs: list[str]) -> list[list[str]]:
    """无下级标题时按语义段落切分：累积至目标区间后成片，短尾段并入前一片。"""
    segments: list[list[str]] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        current.append(para)
        current_len += _text_len(para)
        if current_len >= _TARGET_MIN:
            segments.append(current)
            current = []
            current_len = 0
    if current:
        if segments and current_len < _MERGE_BELOW:
            segments[-1].extend(current)
        else:
            segments.append(current)
    return segments


def _make_child(
    parent_id: str,
    child_index: int,
    path: str,
    level: int,
    chunk_index: int,
    content: str,
    doc: str,
    doc_id: str,
) -> ChunkSpec:
    return ChunkSpec(
        id=uuid.uuid4().hex,
        doc_id=doc_id,
        doc=doc,
        chunk_type="child",
        parent_id=parent_id,
        child_index=child_index,
        path=path,
        level=level,
        chunk_index=chunk_index,
        content=content,
        char_count=_text_len(content),
        has_images=_count_images(content) > 0,
        image_count=_count_images(content),
        vector_state="pending",
        vector_id=None,
    )


def _make_parent(
    breadcrumb: list[str],
    node: _Node,
    doc: str,
    doc_id: str,
    chunk_index: int,
) -> ChunkSpec:
    path = " > ".join(breadcrumb)
    content = _parent_content(breadcrumb, node)
    return ChunkSpec(
        id=uuid.uuid4().hex,
        doc_id=doc_id,
        doc=doc,
        chunk_type="parent",
        parent_id=None,
        child_index=0,
        path=path,
        level=len(breadcrumb),
        chunk_index=chunk_index,
        content=content,
        char_count=_text_len(content),
        has_images=_count_images(content) > 0,
        image_count=_count_images(content),
        vector_state=None,
        vector_id=None,
    )


def _children(
    breadcrumb: list[str],
    node: _Node,
    parent_id: str,
    doc: str,
    doc_id: str,
    seq: int,
) -> tuple[list[ChunkSpec], int]:
    """为一个模块生成子切片，返回（子切片列表，下一个可用 chunk_index）。"""
    specs: list[ChunkSpec] = []
    base_path = " > ".join(breadcrumb)
    if node.children:  # 有下级标题：每个下级标题独立成片，更深层级折叠进来
        intro = list(node.body)
        for i, sub in enumerate(node.children):
            body = _collect_subtree(sub)
            if i == 0 and intro:
                body = intro + body
            sub_path = f"{base_path} > {sub.text}"
            content = _join([f"## {sub_path}", *body])
            specs.append(
                _make_child(parent_id, i, sub_path, len(breadcrumb) + 1, seq, content, doc, doc_id)
            )
            seq += 1
    else:  # 无下级标题：语义段切分
        for i, seg in enumerate(_split_segments(node.body)):
            content = _join([f"## {base_path}", *seg])
            specs.append(
                _make_child(parent_id, i, base_path, len(breadcrumb), seq, content, doc, doc_id)
            )
            seq += 1
    return specs, seq


def chunk(md_text: str, doc: str, doc_id: str) -> list[ChunkSpec]:
    """将清洗后的 markdown 切片为父子切片列表。

    模块层级由 _detect_module_level 动态探测，再按相对层级递归：
    - 层级 >= 模块层，或层级浅于模块层但带直属内容 → 模块（父切片 + 子切片）；
    - 其余（容器）→ 仅并入面包屑，递归子节点；
    - 首个标题节点（层级浅于模块层）的直属正文（封面/目录）丢弃，且标题不入面包屑。
    """
    root = _parse(md_text)
    module_level = _detect_module_level(root)
    specs: list[ChunkSpec] = []
    seq = 0

    def _walk(node: _Node, ancestors: list[str]) -> None:
        nonlocal seq
        if node.level >= module_level or _has_direct_content(node):
            # 模块（含模块层 / 错位模块 / 超深节点）：生成父切片 + 子切片
            breadcrumb = ancestors + [node.text]
            specs.append(_make_parent(breadcrumb, node, doc, doc_id, seq))
            seq += 1
            children, seq = _children(breadcrumb, node, specs[-1].id, doc, doc_id, seq)
            specs.extend(children)
        else:
            # 容器：无直属内容且层级浅于模块层，仅并入面包屑后递归
            for child in node.children:
                _walk(child, ancestors + [node.text])

    top_nodes = root.children
    for i, top in enumerate(top_nodes):
        if i == 0 and top.level < module_level:
            # 标题节点：直属正文（封面/目录）丢弃，标题不入面包屑，仅遍历其子节点
            top.body = []
            for child in top.children:
                _walk(child, [])
        else:
            _walk(top, [])

    return specs
