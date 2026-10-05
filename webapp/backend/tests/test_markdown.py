"""The Markdown viewer's renderer (_MdRenderer, via _convert_md).

It is markdown-it-py underneath: CommonMark plus GFM tables and strikethrough.
What the frontend relies on is pinned here -- top-level blocks numbered
md-a-0..N-1 for reading progress, TOC jumps and annotations; the md-* classes;
code blocks in the shape highlight.js is pointed at -- and so is what must
never get through: raw HTML, and URLs outside _safe_url's allow-list.
"""
from __future__ import annotations

import re


def _md(app_ctx, text: str) -> dict:
    helpers, _captured, _ = app_ctx
    return helpers["main"]._convert_md(text)


def _anchors(html: str) -> list[int]:
    return [int(n) for n in re.findall(r'data-anchor="(\d+)"', html)]


def test_table_renders_as_a_table(app_ctx):
    r = _md(app_ctx, "| A | B |\n|---|:-:|\n| 1 | 2 |\n")
    html = r["html"]
    assert '<table id="md-a-0" data-anchor="0" class="md-table">' in html
    assert "<th>A</th>" in html
    assert '<td style="text-align:center">2</td>' in html
    assert r["anchor_count"] == 1


def test_fenced_code_inside_a_list_item_stays_a_code_block(app_ctx):
    text = "- Linux:\n\n  ```bash\n  xzcat a.img.xz | sudo dd of=/dev/sdX\n  ```\n- Windows\n"
    html = _md(app_ctx, text)["html"]
    assert '<pre class="md-codeblock"><code class="language-bash">xzcat a.img.xz | sudo dd of=/dev/sdX</code></pre>' in html
    # Inside the list's own anchor, not one of its own.
    assert _anchors(html) == [0]
    assert html.index("<li") < html.index("<pre") < html.index("</ul>")


def test_top_level_fence_carries_the_anchor_on_pre(app_ctx):
    html = _md(app_ctx, "Text\n\n```python\nx = 1\n```\n")["html"]
    assert '<pre id="md-a-1" data-anchor="1" class="md-codeblock"><code class="language-python">x = 1</code></pre>' in html


def test_nested_list_inside_an_ordered_item(app_ctx):
    html = _md(app_ctx, "1. Boot.\n2. The menu offers:\n   - GUI\n   - TTY\n3. Done.\n")["html"]
    assert re.search(r'<ol[^>]*class="md-ol">.*<li class="md-li">The menu offers:\s*<ul class="md-ul">', html, re.S)
    assert html.count('class="md-li"') == 5
    assert _anchors(html) == [0]


def test_anchors_are_top_level_blocks_numbered_in_order(app_ctx):
    text = (
        "# Title\n\nPara one.\n\n## Section\n\n- a\n- b\n\n> quoted\n> more\n\n"
        "| x |\n|---|\n| y |\n\n---\n\nPara two.\n"
    )
    r = _md(app_ctx, text)
    assert _anchors(r["html"]) == list(range(r["anchor_count"]))
    assert r["anchor_count"] == 8  # h1, p, h2, ul, blockquote, table, hr, p
    assert r["title"] == "Title"
    assert [(e["title"], e["anchor"]) for e in r["toc"]] == [("Title", 0)]
    assert [(c["title"], c["anchor"]) for c in r["toc"][0]["children"]] == [("Section", 2)]


def test_raw_html_is_escaped(app_ctx):
    html = _md(app_ctx, "<script>alert(1)</script>\n\nText <b>bold</b>\n")["html"]
    assert "<script>" not in html and "<b>" not in html
    assert "&lt;script&gt;" in html and "&lt;b&gt;" in html


def test_unsafe_and_relative_urls_are_refused(app_ctx):
    html = _md(app_ctx, "[x](javascript:alert(1)) [r](notes/other.md) ![i](img/a.png) [ok](https://example.org/)\n")["html"]
    # markdown-it leaves a javascript: link as literal text, never as an href.
    assert 'href="javascript' not in html
    assert '<a href="#" class="md-link">r</a>' in html
    assert 'src="#"' in html and 'class="md-image"' in html
    assert '<a href="https://example.org/" class="md-link">ok</a>' in html


def test_inline_formatting(app_ctx):
    html = _md(app_ctx, "**b** *i* ~~s~~ `c`  \nnext\nsoft\n")["html"]
    assert "<strong>b</strong>" in html and "<em>i</em>" in html
    assert "<s>s</s>" in html and "<code>c</code>" in html
    assert "<br />" in html or "<br>" in html
    # A soft line break is a space, as the text annotations quote was rendered.
    assert "next soft" in html


def test_indented_text_is_a_paragraph_not_code(app_ctx):
    html = _md(app_ctx, "Verse:\n\n    And the evening and the morning\n    were the first day.\n")["html"]
    assert "<pre" not in html
    assert html.count('class="md-p"') == 2


def test_snippet_renderer_collects_no_toc(app_ctx):
    _md(app_ctx, "")  # app_ctx has imported main, and with it the router
    from routers.content import _MdRenderer
    r = _MdRenderer(collect_toc=False)
    html = r.render("# Heading\n\nBody\n")
    assert r.toc_flat == []
    assert 'class="md-h1"' in html
