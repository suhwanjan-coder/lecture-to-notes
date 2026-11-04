"""scripts/note_to_html.py: Obsidian markdown -> HTML-ish conversion (no pandoc needed)."""
import base64

import pytest

import note_to_html as n

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


@pytest.fixture
def img_dir(tmp_path):
    d = tmp_path / "附件 一"
    d.mkdir()
    (d / "胡_s01.png").write_bytes(PNG)
    return d


def test_find_image_in_bases_with_cjk_space_and_forward_slash(tmp_path, img_dir):
    assert n.find_image("胡_s01.png", [None, str(img_dir)]) == str(img_dir / "胡_s01.png")
    assert n.find_image("附件 一/胡_s01.png", [str(tmp_path)]) == str(img_dir / "胡_s01.png")
    assert n.find_image("missing.png", [str(img_dir)]) is None
    assert n.find_image("x.png", []) is None


def test_convert_inlines_image_as_data_uri(img_dir):
    out, k, missing = n.convert("![[胡_s01.png|700]]\n", [str(img_dir)])
    assert k == 1 and missing == []
    assert 'src="data:image/png;base64,' in out
    assert "width:100%" in out                       # >400 -> responsive
    assert 'alt="胡_s01.png"' in out


def test_convert_small_width_keeps_px(img_dir):
    out, _, _ = n.convert("![[胡_s01.png|300]]", [str(img_dir)])
    assert "width:300px" in out
    default, _, _ = n.convert("![[胡_s01.png]]", [str(img_dir)])
    assert "width:100%" in default                   # default 600 -> responsive


def test_convert_missing_image_is_reported_and_escaped():
    out, k, missing = n.convert("![[<b>x</b>.png|300]]", [])
    assert k == 0 and missing == ["<b>x</b>.png"]
    assert "<b>x</b>" not in out and "&lt;b&gt;" in out


def test_convert_frontmatter_crlf_wikilinks_and_highlight():
    text = "---\r\ntitle: x\r\n---\r\n# T\r\n[[Note|別名]] [[Plain]] ==重點==\r\n"
    out, _, _ = n.convert(text, [])
    assert "title: x" not in out
    assert "別名" in out and "[[" not in out and "Plain" in out
    assert "<mark>重點</mark>" in out
    assert "\r" not in out


def test_convert_callout_plain_and_foldable():
    text = "> [!NOTE] 標題 <x>\n> 內文一\n> 內文二\n\n> [!tip]- 收合\n> 藏起來\n\n> [!warning]+ 展開\n> 開著\n"
    out, _, _ = n.convert(text, [])
    assert '<div class="co note"><div class="ct">標題 &lt;x&gt;</div>' in out
    assert '<details class="co tip"><summary class="ct">收合</summary>' in out
    assert '<details class="co warning" open>' in out
    assert "內文一\n內文二" in out


def test_convert_tabs_become_spaces():
    out, _, _ = n.convert("a\tb", [])
    assert out == "a    b"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: an embed with a text alias, ![[x.png|說明]], does not match IMG_RE; the wikilink "
    "rule then turns it into '!說明' -- the image is silently dropped and not reported missing"))
def test_convert_embed_with_text_alias_keeps_image(img_dir):
    out, k, _ = n.convert("![[胡_s01.png|說明文字]]", [str(img_dir)])
    assert k == 1


def test_doc_title():
    assert n.doc_title("---\na: b\n---\n# 標題 \n正文", "/x/y.md") == "標題"
    assert n.doc_title("無標題\n## 二級", "/x/我的 筆記.md") == "我的 筆記"
    assert n.doc_title("---\r\na: b\r\n---\r\n# CRLF\r\n", "/x/y.md") == "CRLF"
