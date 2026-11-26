import io

import pytest
from app.knowledge.chunking import chunk_blocks
from app.knowledge.parsers import parse_document, validate_document_bytes
from app.knowledge.structured_office import office_blocks
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from openpyxl import Workbook
from pptx import Presentation
from pptx.util import Inches
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)


def save_office(document):
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def public_chunks(filename, body, chunk_size=200):
    mime = validate_document_bytes(filename, "", body)
    blocks = parse_document(filename, mime, body, strategy="structured_v1")
    return chunk_blocks(blocks, chunk_size=chunk_size, chunk_overlap=8, strategy="structured_v1")


def test_word_preserves_interleaved_table_and_body_order_and_positions():
    document = Document()
    document.add_heading("退换货", level=2)
    document.add_paragraph("")
    table = document.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "品类"
    table.cell(0, 2).text = "期限"
    table.cell(1, 0).text = "项链"
    table.cell(1, 2).text = "7天"
    document.add_paragraph("最后联系售后。")
    blocks = list(office_blocks(".docx", save_office(document)))
    assert [b.text for b in blocks] == [
        "退换货",
        "品类 |  | 期限",
        "项链 |  | 7天",
        "最后联系售后。",
    ]
    assert blocks[0].metadata["heading_level"] == 2
    assert blocks[1].metadata["is_table_header"]
    assert blocks[2].metadata["table_id"] == blocks[1].metadata["table_id"]
    assert blocks[2].metadata["table_header"] == blocks[1].text
    assert blocks[2].metadata["row_number"] == 2
    assert blocks[-1].metadata["paragraph_index"] == 2
    chunks = public_chunks("退货.docx", save_office(document))
    row = next(c for c in chunks if "项链" in c["content"])
    assert row["metadata"]["heading_path"] == ["退换货"]
    assert row["metadata"]["source_spans"][-1]["table_index"] == 0
    assert row["metadata"]["source_spans"][-1]["row_number"] == 2
    assert chunks[-1]["metadata"]["source_spans"][0]["paragraph_index"] == 2


def test_word_keeps_grid_coordinates_for_omitted_and_merged_cells():
    document = Document()
    table = document.add_table(rows=3, cols=3)
    for index, text in enumerate(("A", "B", "C")):
        table.cell(0, index).text = text
    row = table.rows[1]
    row.cells[1].text = "middle"
    row._tr.remove(row._tr.tc_lst[0])
    before = OxmlElement("w:gridBefore")
    before.set(qn("w:val"), "1")
    row._tr.get_or_add_trPr().append(before)
    table.rows[2].cells[0].merge(table.rows[2].cells[1]).text = "merged"
    blocks = list(office_blocks(".docx", save_office(document)))
    assert blocks[1].text == " | middle | "
    assert blocks[2].text == "merged |  | "


def test_slides_preserve_title_body_group_table_and_slide_boundaries():
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    slide.shapes.title.text = "运输"
    group = slide.shapes.add_group_shape()
    group.shapes.add_textbox(0, 0, Inches(3), Inches(1)).text = "海运"
    table = slide.shapes.add_table(2, 2, 0, 0, Inches(3), Inches(1)).table
    table.cell(0, 0).text = "地区"
    table.cell(0, 1).text = "时长"
    table.cell(1, 1).text = "30天"
    second = presentation.slides.add_slide(presentation.slide_layouts[5])
    second.shapes.title.text = "空运"
    blocks = list(office_blocks(".pptx", save_office(presentation)))
    assert [b.text for b in blocks] == ["运输", "海运", "地区 | 时长", " | 30天", "空运"]
    assert blocks[0].metadata["block_type"] == "heading"
    assert blocks[1].metadata["slide_title"] == "运输"
    assert blocks[3].metadata["slide_number"] == 1
    assert blocks[2].metadata["table_id"] == blocks[3].metadata["table_id"]
    assert blocks[-1].metadata["scope"] != blocks[1].metadata["scope"]
    chunks = public_chunks("运输.pptx", save_office(presentation))
    sea = next(c for c in chunks if "海运" in c["content"])
    assert "运输" in sea["index_text"]
    assert sea["metadata"]["source_spans"][0]["slide_number"] == 1
    assert sea["metadata"]["source_spans"][0]["shape_id"] == blocks[1].metadata["shape_id"]
    table = next(c for c in chunks if "30天" in c["content"])
    assert table["metadata"]["source_spans"][-1]["row_number"] == 2


def test_workbook_keeps_skipped_columns_physical_rows_and_per_sheet_headers():
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "规格"
    sheet.append(["主石", "颜色", "重量"])
    sheet.append([])
    sheet.append(["祖母绿", None, 0])
    sheet.append(["缺省色重"])
    other = workbook.create_sheet("运费")
    other.append(["重量", "运费"])
    other.append([2, 10])
    blocks = list(office_blocks(".xlsx", save_office(workbook)))
    assert [b.text for b in blocks] == [
        "主石 | 颜色 | 重量",
        "祖母绿 |  | 0",
        "缺省色重 |  | ",
        "重量 | 运费",
        "2 | 10",
    ]
    assert blocks[1].metadata["row_number"] == blocks[1].metadata["row_start"] == 3
    assert blocks[1].metadata["sheet_name"] == "规格"
    assert blocks[3].metadata["is_table_header"]
    assert blocks[3].metadata["table_id"] != blocks[1].metadata["table_id"]
    chunks = public_chunks("清单.xlsx", save_office(workbook))
    assert [c["metadata"]["heading_path"] for c in chunks] == [["规格"], ["运费"]]
    assert [s["row_number"] for s in chunks[0]["metadata"]["source_spans"]] == [1, 3, 4]
    assert chunks[1]["metadata"]["source_spans"][-1]["sheet_name"] == "运费"


@pytest.mark.parametrize(
    ("row", "column", "error"),
    [(10001, 1, "xlsx_row_limit_exceeded"), (1, 1025, "xlsx_column_limit_exceeded")],
)
def test_workbook_rejects_out_of_range_sparse_coordinates(row, column, error):
    workbook = Workbook()
    workbook.active.cell(row=row, column=column, value="远端")
    with pytest.raises(ValueError, match=error):
        list(office_blocks(".xlsx", save_office(workbook)))


def save_pdf(*pages: list[str]) -> bytes:
    pdf = PdfWriter()
    cid_font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/CIDSystemInfo"): DictionaryObject(
                {
                    NameObject("/Registry"): TextStringObject("Adobe"),
                    NameObject("/Ordering"): TextStringObject("GB1"),
                    NameObject("/Supplement"): NumberObject(4),
                }
            ),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/STSong-Light"),
            NameObject("/Encoding"): NameObject("/UniGB-UCS2-H"),
            NameObject("/DescendantFonts"): ArrayObject([cid_font]),
        }
    )
    for lines in pages:
        page = pdf.add_blank_page(width=500, height=300)
        if not lines:
            continue
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
            }
        )
        text = " ".join(f"<{line.encode('utf-16-be').hex()}> Tj T*" for line in lines)
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 10 250 Td 16 TL {text} ET".encode())
        page.replace_contents(content)
    stream = io.BytesIO()
    pdf.write(stream)
    return stream.getvalue()


def test_pdf_keeps_actual_page_numbers_across_empty_pages_and_text_spans():
    body = save_pdf(["first page"], [], ["third page"])
    blocks = list(office_blocks(".pdf", body))
    assert [block.text for block in blocks] == ["first page", "third page"]
    assert [block.metadata["page_number"] for block in blocks] == [1, 3]
    for block in blocks:
        metadata = block.metadata
        assert metadata["page_char_end"] - metadata["page_char_start"] == len(block.text)


def test_pdf_chapters_have_page_anchors_and_carry_across_a_page_break():
    body = save_pdf(
        ["Chapter 1: Sales", "Chapter 1 covers sales.", "shipment details", "第二章 退换货"],
        ["七天内可以退货。", "第1节：退款方式", "第一章的规定仍然适用。", "款项原路退回。"],
    )
    pages = [page.extract_text() for page in PdfReader(io.BytesIO(body)).pages]
    blocks = list(office_blocks(".pdf", body))
    assert [
        (b.text, b.metadata["heading_level"])
        for b in blocks
        if b.metadata["block_type"] == "heading"
    ] == [
        ("Chapter 1: Sales", 1),
        ("第二章 退换货", 1),
        ("第1节：退款方式", 2),
    ]
    for block in blocks:
        meta = block.metadata
        page = pages[meta["page_number"] - 1]
        assert block.text == page[meta["page_char_start"] : meta["page_char_end"]]
    chunks = public_chunks("服务.pdf", body, chunk_size=100)
    refund = next(c for c in chunks if "七天内" in c["content"])
    assert refund["metadata"]["heading_path"] == ["第二章 退换货"]
    assert "第二章 退换货" in refund["index_text"]
    assert refund["metadata"]["source_spans"][0]["page_number"] == 2
    method = next(c for c in chunks if "原路" in c["content"])
    assert method["metadata"]["heading_path"] == ["第二章 退换货", "第1节：退款方式"]
    for chunk in chunks:
        for span in chunk["metadata"]["source_spans"]:
            page = pages[span["page_number"] - 1]
            excerpt = chunk["content"][span["chunk_start"] : span["chunk_end"]]
            assert excerpt == page[span["page_char_start"] : span["page_char_end"]]
