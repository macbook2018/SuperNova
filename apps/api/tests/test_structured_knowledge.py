from __future__ import annotations

import hashlib

import pytest
from app.knowledge.chunking import chunk_blocks
from app.knowledge.parsers import parse_document
from app.knowledge.structured_chunking import MAX_CHUNKS


def index(name, text, size=130):
    blocks = parse_document(name, "", text.encode(), strategy="structured_v1")
    chunks = chunk_blocks(blocks, chunk_size=size, chunk_overlap=20, strategy="structured_v1")
    assert chunks == chunk_blocks(
        blocks, chunk_size=size, chunk_overlap=20, strategy="structured_v1"
    )
    for item in chunks:
        assert len(item["index_text"]) <= size
        assert item["content_hash"] == hashlib.sha256(item["content"].encode()).hexdigest()
        for span in item["metadata"]["source_spans"]:
            ref = blocks[span["block_index"]]
            assert (
                ref.text[span["start"] : span["end"]]
                == (item["content"][span["chunk_start"] : span["chunk_end"]])
            )
    return blocks, chunks


def test_structured_markdown_sections_short_paragraphs_and_code():
    md = (
        "# 手册\n## 安装\n\n先下载。\n\n再安装。\n\n"
        "```python\n# 假标题\n    print('done')\n```\n\n"
        "## 卸载\n\n" + "操作说明。" * 80 + "\n"
    )
    blocks, chunks = index("用户指南.md", md)
    assert all(b.metadata["line_start"] <= b.metadata["line_end"] for b in blocks)
    first, others = chunks[0], chunks[1:]
    assert first["metadata"]["heading_path"] == ["手册", "安装"]
    assert "先下载。\n\n再安装。" in first["content"]
    assert "    print" in first["content"]
    assert "用户指南" in first["index_text"] and "用户指南" not in first["content"]
    assert len(others) > 1
    assert all(c["metadata"]["heading_path"] == ["手册", "卸载"] for c in others)
    assert all("卸载" in c["index_text"] for c in others)
    groups = [c["metadata"]["parent_section_id"] for c in chunks]
    assert groups[0] != groups[1] and len(set(groups[1:])) == 1


def test_plain_text_faq_and_chinese_chapter_anchors():
    text = (
        "第一章 服务\n问：退款期多久？\n答："
        + "七天内可退款。" * 45
        + "\n问：如何换货？\n答：保留票据。\n第二章 其他\n这里是其他条款。"
    )
    _, chunks = index("售后.txt", text)
    for c in chunks:
        if "七天" in c["content"]:
            assert "退款期多久" in c["index_text"]
            assert "换货" not in c["index_text"]
    assert chunks[-2]["content"] == "问：如何换货？\n\n答：保留票据。"
    assert chunks[-1]["metadata"]["heading_path"] == ["第二章 其他"]


def test_structured_csv_schema_positions_and_multiline_quoted_rows():
    rows = '产品,原价,活动价\nA,,99\nB,99,\n"C\n改名",,101\n' + "相当长的产品名,,96\n" * 12
    blocks, chunks = index("价格.csv", rows)
    assert blocks[1].text == "A |  | 99"
    assert blocks[2].text == "B | 99 | "
    assert blocks[3].metadata["row_number"] == 4
    assert (blocks[3].metadata["line_start"], blocks[3].metadata["line_end"]) == (4, 5)
    assert len(chunks) > 1
    assert all("产品 | 原价 | 活动价" in c["index_text"] for c in chunks)
    assert "活动价" not in chunks[-1]["content"]


def test_structured_html_headings_inline_text_and_tables():
    html = (
        "<html><head><title>指南</title></head><body><nav>忽略导航</nav>"
        "<header><h1>政策</h1></header><div>重要<strong>提示</strong>。</div>"
        "<h2>优惠</h2><p>适用范围</p><table><tr><th>用户</th><th>等级</th><th>折扣</th></tr>"
        "<tr><td>A</td><td></td><td>8折</td></tr></table><script>不要返回</script>"
        "<div>结束</div></body></html>"
    )
    blocks, chunks = index("条款.htm", html)
    assert [b.text for b in blocks] == [
        "政策",
        "重要提示。",
        "优惠",
        "适用范围",
        "用户 | 等级 | 折扣",
        "A |  | 8折",
        "结束",
    ]
    assert chunks[0]["metadata"]["heading_path"] == ["政策"]
    assert all(c["metadata"]["heading_path"] == ["政策", "优惠"] for c in chunks[1:])
    assert chunks[-1]["content"] == "结束"


def test_markdown_tables_keep_schema_but_separator_is_not_evidence():
    table = "# 手册\n\n|档位|价格|数量|\n|:---|---:|---|\n" + "|A||9|\n" * 35
    blocks, chunks = index("套餐.md", table)
    assert blocks[2].metadata["line_start"] == 5
    assert all("|档位|价格|数量|" in c["index_text"] for c in chunks)
    assert all("---" not in c["content"] for c in chunks)
    assert chunks[-1]["metadata"]["row_end"] == 36


def test_legacy_defaults_unchanged_and_new_mode_rejects_unknown_or_excessive_input():
    body = b"# A\n\nB\n\nC"
    old = parse_document("doc.md", "", body)
    assert len(old) == 1 and old[0].metadata == {"format": "md"}
    assert list(chunk_blocks(old, chunk_size=100, chunk_overlap=30)[0]) == [
        "content",
        "content_hash",
        "metadata",
    ]
    with pytest.raises(ValueError, match="unsupported_knowledge_strategy"):
        parse_document("doc.md", "", body, strategy="unknown")
    with pytest.raises(ValueError, match="no_extractable_text"):
        parse_document("doc.md", "", b"   ", strategy="structured_v1")
    with pytest.raises(ValueError, match="extracted_text_too_large"):
        parse_document("doc.txt", "", b"x" * 2_000_001, strategy="structured_v1")
    data = parse_document("d.txt", "", b"x" * (MAX_CHUNKS * 100), strategy="structured_v1")
    with pytest.raises(ValueError, match="document_chunk_limit_exceeded"):
        chunk_blocks(data, chunk_size=100, chunk_overlap=80, strategy="structured_v1")


def test_legacy_and_structured_chunk_size_guards_and_heading_only_input():
    for strategy in ("legacy", "structured_v1"):
        with pytest.raises(ValueError, match="chunk_size_out_of_range"):
            chunk_blocks([], chunk_size=20, chunk_overlap=0, strategy=strategy)
        with pytest.raises(ValueError, match="chunk_overlap_out_of_range"):
            chunk_blocks([], chunk_size=100, chunk_overlap=100, strategy=strategy)
    _, chunks = index("title.md", "# 单独标题")
    assert chunks[0]["content"] == "# 单独标题"


def test_published_reviewed_markdown_question_and_answer_stay_together():
    text = (
        "# 退换须知\n\n## 常见问题\n可以退货吗？\n\n## 标准答复\n"
        + "七天内可以退。" * 60
        + "\n\n## 相关问法\n- 可以退吗\n\n## 分类\n售后\n"
    )
    _, chunks = index("审核发布的 FAQ.md", text)
    answers = [c for c in chunks if "七天" in c["content"]]
    assert len(answers) > 1
    assert "可以退货吗？" in answers[0]["content"]
    assert all(c["metadata"]["faq_question"] == "可以退货吗？" for c in answers)
    assert all("可以退货吗？" in c["index_text"] for c in answers)
    assert chunks[-1]["metadata"]["heading_path"] == ["退换须知", "分类"]
    assert "faq_question" not in chunks[-1]["metadata"]
