"""转换器清洗逻辑单元测试：封面 / 目录 / 免责声明 / 版权 / 修改记录 / 阅读对象删除。"""
from app.knowledge.converter import clean_html

BODY = "<h1>操作手册</h1><h2>模块一</h2><p>正文内容</p>"


def test_cover_before_first_heading_removed():
    html = "<p>封面标题</p><img src='logo.png'><h1>操作手册</h1><h2>模块一</h2><p>正文</p>"
    out = clean_html(html)
    assert "封面标题" not in out
    assert "操作手册" in out


def test_toc_removed():
    html = "<h1>操作手册</h1><h2>目录</h2><p>1. 模块一</p><p>2. 模块二</p><h2>模块一</h2><p>正文</p>"
    out = clean_html(html)
    assert "目录" not in out
    assert "1. 模块一" not in out
    assert "正文" in out


def test_disclaimer_and_copyright_removed():
    html = (
        "<h1>操作手册</h1>"
        "<h2>免责声明</h2><p>本手册仅供参考</p>"
        "<h2>版权声明</h2><p>Copyright 2026</p>"
        "<h2>模块一</h2><p>正文</p>"
    )
    out = clean_html(html)
    assert "免责声明" not in out
    assert "本手册仅供参考" not in out
    assert "版权声明" not in out
    assert "Copyright 2026" not in out
    assert "正文" in out


def test_revision_record_removed():
    html = (
        "<h1>操作手册</h1>"
        "<h2>文件修改记录</h2><p>v1.0 初版</p>"
        "<h2>阅读对象</h2><p>全体教职工</p>"
        "<h2>模块一</h2><p>正文</p>"
    )
    out = clean_html(html)
    assert "文件修改记录" not in out
    assert "v1.0 初版" not in out
    assert "阅读对象" not in out
    assert "正文" in out


def test_skip_stops_at_same_level_heading():
    """删除目录章节时，不应误删同级或上级的后续正文模块。"""
    html = (
        "<h1>操作手册</h1>"
        "<h2>目录</h2><p>列表</p>"
        "<h2>模块一</h2><p>模块一正文</p>"
    )
    out = clean_html(html)
    assert "目录" not in out
    assert "模块一正文" in out
