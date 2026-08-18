"""父子切片器单元测试：动态模块层级、父子切片、面包屑注入、图片跟随、唯一 id。"""
import re

from app.knowledge.chunker import chunk

DOC = "测试手册"
DOC_ID = "doc-001"


def _is_uuid(value: str) -> bool:
    """校验切片 id 为 32 位 hex（uuid4().hex，唯一主键、非中文）。"""
    return bool(re.fullmatch(r"[0-9a-f]{32}", value))

SAMPLE = """# 测试手册

## 模块A

### 功能一

这是功能一的介绍段落，包含一些操作步骤说明文字，用于测试语义段切分处理逻辑是否正常。

![截图](./media/a.png)

#### 子功能1

点击按钮进行操作。

![截图](./media/b.png)

#### 子功能2

填写表单提交即可完成操作。

### 功能二

这个功能没有子功能，只有一段较短的描述。
"""


def test_chunk_structure():
    specs = chunk(SAMPLE, DOC, DOC_ID)
    parents = [s for s in specs if s.chunk_type == "parent"]
    children = [s for s in specs if s.chunk_type == "child"]

    assert len(parents) == 2
    assert len(children) == 3


def test_parent_id_and_path():
    specs = chunk(SAMPLE, DOC, DOC_ID)
    parent = next(s for s in specs if s.chunk_type == "parent" and "功能一" in s.path)
    assert _is_uuid(parent.id)
    assert parent.path == "模块A > 功能一"
    assert parent.level == 2
    assert parent.parent_id is None


def test_ids_are_unique_uuids():
    specs = chunk(SAMPLE, DOC, DOC_ID)
    ids = [s.id for s in specs]
    assert len(ids) == len(set(ids))
    assert all(_is_uuid(i) for i in ids)


def test_child_parent_linkage():
    specs = chunk(SAMPLE, DOC, DOC_ID)
    parents = {s.id for s in specs if s.chunk_type == "parent"}
    children = [s for s in specs if s.chunk_type == "child"]
    assert len(children) == 3
    # 每个子切片 parent_id 指向存在的父切片
    assert all(c.parent_id in parents for c in children)


def test_breadcrumb_injected():
    specs = chunk(SAMPLE, DOC, DOC_ID)
    child = next(s for s in specs if s.chunk_type == "child" and "子功能1" in s.path)
    assert child.content.startswith("## 模块A > 功能一 > 子功能1")
    assert child.level == 3


def test_intro_merged_into_first_child():
    specs = chunk(SAMPLE, DOC, DOC_ID)
    child = next(
        s for s in specs
        if s.chunk_type == "child" and s.child_index == 0 and "功能一" in s.path
    )
    assert "这是功能一的介绍段落" in child.content


def test_images_follow_paragraph():
    specs = chunk(SAMPLE, DOC, DOC_ID)
    child = next(
        s for s in specs
        if s.chunk_type == "child" and s.child_index == 0 and "功能一" in s.path
    )
    assert "![截图](./media/a.png)" in child.content
    assert "![截图](./media/b.png)" in child.content
    assert child.image_count >= 2
    assert child.has_images


def test_no_h4_semantic_segment():
    specs = chunk(SAMPLE, DOC, DOC_ID)
    child = next(s for s in specs if s.chunk_type == "child" and s.parent_id and "功能二" in s.path)
    assert child.path == "模块A > 功能二"
    assert child.level == 2
    assert "这个功能没有子功能" in child.content


# 标题整体上移一级的文档（仿「积点评价操作手册」）：功能模块在 H2，H1 兼作标题与错位模块
SHIFTED = """# 积点评价系统操作指南

**目录**

[过程管理（教职工）](#a) 3

# 2、业务流程

![](./media/flow.png)

# 3、角色权限

| 角色 | 菜单 | 权限 |
| --- | --- | --- |
| 教职工 | 过程管理 | 新增 |

# 4、pc端

## 1、过程管理（教职工）

在过程管理页面中，点击警示性标签以筛选对应条目。

![](./media/pc1.png)

切换分类查看数据，点击表格视图切换按钮。

![](./media/pc2.png)

## 2、过程管理（他评组）

点击分类可查看不同数据，切换幼儿园查看数据，点击指导申请进入操作界面。

![](./media/pc3.png)

# 5、移动端

## 过程管理（园所）

可切换学年学期，点击进入详情界面。

![](./media/m1.png)

### 详情页

可切换材料查看详情。
"""


def test_shifted_level_detection():
    specs = chunk(SHIFTED, DOC, DOC_ID)
    parents = [s for s in specs if s.chunk_type == "parent"]
    children = [s for s in specs if s.chunk_type == "child"]

    parent_paths = {p.path for p in parents}
    # 模块层应动态探测为 H2：H2 功能模块成为父切片
    assert "4、pc端 > 1、过程管理（教职工）" in parent_paths
    assert "4、pc端 > 2、过程管理（他评组）" in parent_paths
    assert "5、移动端 > 过程管理（园所）" in parent_paths
    # 错位模块（层级浅于模块层但自带正文/图片）也成父切片
    assert "2、业务流程" in parent_paths
    assert "3、角色权限" in parent_paths
    # 容器 4、pc端 / 5、移动端 本身不应成为父切片
    assert not any(p.path == "4、pc端" for p in parents)
    assert not any(p.path == "5、移动端" for p in parents)

    # 每个父切片都有子切片（无「仅父切片」导致的内容丢失）
    child_parent_ids = {c.parent_id for c in children}
    assert all(p.id in child_parent_ids for p in parents)

    # 模块的下级标题（详情页）成为子切片
    assert any("详情页" in c.path for c in children)

    # 错位模块 level = 1（面包屑仅含自身标题）
    flow_parent = next(p for p in parents if p.path == "2、业务流程")
    assert flow_parent.level == 1


# 仅 H1 + H2 的浅层文档：模块层应探测为 H2，H2 生成父+语义子切片
SHALLOW = """# 使用准备

## 操作系统要求

需要 Windows 10 及以上操作系统，建议使用最新版本浏览器访问系统以获得最佳体验。

## 浏览器要求

推荐使用 Chrome 或 Edge 浏览器访问系统。

## 角色权限

教职工拥有新增权限，管理员拥有编辑权限。
"""


def test_shallow_doc():
    specs = chunk(SHALLOW, DOC, DOC_ID)
    parents = [s for s in specs if s.chunk_type == "parent"]
    children = [s for s in specs if s.chunk_type == "child"]

    assert len(parents) == 3
    assert len(children) >= 3
    child_parent_ids = {c.parent_id for c in children}
    assert all(p.id in child_parent_ids for p in parents)
