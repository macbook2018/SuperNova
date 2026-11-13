from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime

from openpyxl import Workbook
import pytest

from app.chat_import.parsers import parse_chat_export


def test_json_export_normalizes_messages_and_does_not_infer_target_identity() -> None:
    payload = {
        "conversation_id": "wxid_customer_1",
        "conversation_title": "客户A",
        "conversation_type": "direct",
        "messages": [
            {
                "message_id": "m-1",
                "sender_id": "wxid_owner",
                "sender_name": "本人",
                "receiver_id": "wxid_customer_1",
                "content": "你好",
                "message_type": "text",
                "timestamp": "2026-11-12T10:00:00+08:00",
            }
        ],
    }

    [message] = list(parse_chat_export("export.json", json.dumps(payload).encode()))

    assert message.conversation_external_id == "wxid_customer_1"
    assert message.conversation_title == "客户A"
    assert message.conversation_type == "direct"
    assert message.sender_ref == "wxid_owner"
    assert message.sender_name == "本人"
    assert message.receiver_ref == "wxid_customer_1"
    assert message.content == "你好"
    assert message.timestamp == datetime(2026, 11, 12, 2, 0, tzinfo=UTC)
    assert message.metadata["timestamp_assumed_utc"] is False
    assert "is_target_person" not in message.metadata


def test_json_export_supports_nested_conversations() -> None:
    payload = {
        "conversations": [
            {
                "chat_id": "group-1",
                "chat_name": "项目群",
                "chat_type": "group",
                "messages": [
                    {
                        "sender": "Alice",
                        "content": "收到",
                        "time": 1794456000,
                    }
                ],
            }
        ]
    }

    [message] = list(parse_chat_export("nested.json", json.dumps(payload).encode()))

    assert message.conversation_external_id == "group-1"
    assert message.conversation_title == "项目群"
    assert message.conversation_type == "group"
    assert message.sender_name == "Alice"


def test_csv_export_supports_common_chinese_headers() -> None:
    body = (
        "会话id,发送者,内容,时间,消息类型\n"
        "chat-1,本人,已处理,2026-11-12 10:00:00,text\n"
    ).encode()

    [message] = list(parse_chat_export("messages.csv", body))

    assert message.conversation_external_id == "chat-1"
    assert message.sender_name == "本人"
    assert message.content == "已处理"
    assert message.metadata["timestamp_assumed_utc"] is True


def test_txt_export_supports_timestamped_lines_and_multiline_content() -> None:
    body = (
        "[2026-11-12 10:00:00] 本人：第一行\n"
        "第二行补充\n"
        "2026-11-12 10:01:00 客户A: 收到\n"
    ).encode()

    messages = list(parse_chat_export("chat.txt", body))

    assert len(messages) == 2
    assert messages[0].content == "第一行\n第二行补充"
    assert messages[1].sender_name == "客户A"


def test_html_export_supports_tabular_messages() -> None:
    body = (
        "<html><body><table><tr><th>时间</th><th>发送者</th><th>内容</th></tr>"
        "<tr><td>2026-11-12 10:00:00</td><td>本人</td><td>测试消息</td></tr>"
        "</table></body></html>"
    ).encode()

    [message] = list(parse_chat_export("chat.html", body))

    assert message.sender_name == "本人"
    assert message.content == "测试消息"


def test_xlsx_export_supports_header_row() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["会话id", "发送者", "内容", "时间"])
    sheet.append(["chat-2", "客户B", "请问价格", datetime(2026, 11, 12, 10, 0)])
    buffer = io.BytesIO()
    workbook.save(buffer)

    [message] = list(parse_chat_export("chat.xlsx", buffer.getvalue()))

    assert message.conversation_external_id == "chat-2"
    assert message.sender_name == "客户B"
    assert message.content == "请问价格"
    assert message.metadata["sheet_name"] == "Sheet"


def test_zip_export_parses_supported_files() -> None:
    csv_body = b"conversation_id,sender,content,timestamp\nchat-3,Owner,Hello,1794456000\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("exports/messages.csv", csv_body)

    [message] = list(parse_chat_export("bundle.zip", buffer.getvalue()))

    assert message.source_file == "bundle.zip:exports/messages.csv"
    assert message.content == "Hello"


def test_zip_export_rejects_path_traversal() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../outside.csv", b"conversation_id,sender,content,timestamp\n")
    with pytest.raises(ValueError, match="unsafe_zip_member_path"):
        list(parse_chat_export("bundle.zip", buffer.getvalue()))


def test_parser_rejects_unknown_schema_and_missing_identity() -> None:
    with pytest.raises(ValueError, match="unsupported_json_chat_shape"):
        list(parse_chat_export("unknown.json", b'{"items": []}'))
    body = b"conversation_id,content,timestamp\nchat-1,Hello,1794456000\n"
    with pytest.raises(ValueError, match="missing_sender_identity"):
        list(parse_chat_export("missing-sender.csv", body))
