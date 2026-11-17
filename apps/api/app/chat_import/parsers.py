from __future__ import annotations

import csv
import io
import json
import re
import stat
import zipfile
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bs4 import BeautifulSoup
from openpyxl import load_workbook

SUPPORTED_CHAT_EXTENSIONS = {".json", ".csv", ".txt", ".html", ".htm", ".xlsx", ".zip"}
MAX_ZIP_MEMBERS = 100
MAX_ZIP_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 50 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 100
MAX_RECORDS_PER_FILE = 500_000

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "conversation_id": (
        "conversation_id", "chat_id", "session_id", "talker_id", "conversation",
        "chat", "talker", "会话id", "聊天id", "群聊id",
    ),
    "conversation_title": (
        "conversation_title", "conversation_name", "chat_name", "chat_title",
        "session_name", "group_name", "会话名称", "聊天名称", "群名称", "联系人",
    ),
    "conversation_type": (
        "conversation_type", "chat_type", "session_type", "type", "会话类型", "聊天类型",
    ),
    "message_id": ("message_id", "msg_id", "server_id", "local_id", "消息id"),
    "sender_id": (
        "sender_id", "from_id", "from_wxid", "sender_wxid", "wxid", "user_id",
        "发送者id", "发送方id", "微信id",
    ),
    "sender_name": (
        "sender_name", "from_name", "sender", "from", "nickname", "display_name",
        "user_name", "发送者", "发送方", "发件人", "昵称", "发送人",
    ),
    "receiver_id": (
        "receiver_id", "to_id", "to_wxid", "receiver_wxid", "接收者id", "接收方id",
    ),
    "receiver_name": ("receiver_name", "to_name", "receiver", "to", "接收者", "接收方"),
    "content": (
        "content", "content_text", "text", "message", "msg", "body",
        "消息内容", "内容", "消息", "文本",
    ),
    "message_type": ("message_type", "msg_type", "消息类型", "类型"),
    "timestamp": (
        "timestamp", "time", "create_time", "datetime", "date", "send_time",
        "msg_time", "发送时间", "消息时间", "时间", "日期",
    ),
}

_HEADER_LOOKUP = {
    alias.casefold().replace(" ", "").replace("-", "").replace("_", ""): canonical
    for canonical, aliases in _FIELD_ALIASES.items()
    for alias in aliases
}
_TEXT_LINE = re.compile(
    r"^\s*\[?(?P<timestamp>\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"[ T]\d{1,2}:\d{2}(?::\d{2})?)\]?\s+"
    r"(?P<sender>[^:：]{1,120})[:：]\s?(?P<content>.*)$"
)


@dataclass(frozen=True)
class NormalizedChatMessage:
    conversation_external_id: str
    conversation_title: str | None
    conversation_type: str
    external_message_id: str | None
    sender_ref: str
    sender_name: str
    receiver_ref: str | None
    receiver_name: str | None
    content: str
    message_type: str
    timestamp: datetime
    source_file: str
    source_row: int
    metadata: dict[str, Any]


def _normalized_header(value: object) -> str:
    return str(value or "").strip().casefold().replace(" ", "").replace("-", "").replace("_", "")


def _first(row: Mapping[str, Any], field: str) -> Any:
    for key, value in row.items():
        canonical = _HEADER_LOOKUP.get(_normalized_header(key))
        if canonical == field and value not in (None, ""):
            return value
    return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        if isinstance(value, dict):
            value = value.get("text") or value.get("content") or value.get("value")
        else:
            value = json.dumps(value, ensure_ascii=False)
    result = str(value).strip()
    return result or None


def _parse_timestamp(value: Any) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)) or (
        isinstance(value, str) and value.strip().replace(".", "", 1).isdigit()
    ):
        numeric = float(value)
        if numeric > 100_000_000_000:
            numeric /= 1000
        try:
            parsed = datetime.fromtimestamp(numeric, tz=UTC)
        except (OverflowError, OSError, ValueError):
            raise ValueError("invalid_message_timestamp") from None
        return parsed, False
    else:
        raw = _string(value)
        if raw is None:
            raise ValueError("missing_message_timestamp")
        candidate = raw.replace("Z", "+00:00")
        parsed = None
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    parsed = datetime.strptime(raw, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            raise ValueError("invalid_message_timestamp")
    assumed_utc = parsed.tzinfo is None
    if assumed_utc:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC), assumed_utc


def _conversation_type(value: Any, title: str | None) -> str:
    raw = (_string(value) or "").casefold()
    if raw in {"group", "群聊", "微信群", "1", "true"} or "group" in raw:
        return "group"
    if raw in {"direct", "private", "single", "私聊", "单聊", "0", "false"}:
        return "direct"
    if title and ("群" in title or "group" in title.casefold()):
        return "group"
    return "unknown"


def _normalize_row(
    row: Mapping[str, Any],
    *,
    source_file: str,
    source_row: int,
    inherited: Mapping[str, Any] | None = None,
) -> NormalizedChatMessage:
    merged = dict(inherited or {})
    merged.update(row)
    conversation_id = _string(_first(merged, "conversation_id"))
    conversation_title = _string(_first(merged, "conversation_title"))
    sender_id = _string(_first(merged, "sender_id"))
    sender_name = _string(_first(merged, "sender_name")) or sender_id
    if not sender_name:
        raise ValueError("missing_sender_identity")
    sender_ref = sender_id or sender_name
    content = _string(_first(merged, "content"))
    message_type = _string(_first(merged, "message_type")) or "text"
    if content is None:
        # Preserve non-text events as typed placeholders instead of silently dropping them.
        if message_type.casefold() in {"image", "2", "图片"}:
            content = "[图片]"
        elif message_type.casefold() in {"voice", "audio", "3", "语音"}:
            content = "[语音]"
        elif message_type.casefold() in {"video", "视频"}:
            content = "[视频]"
        else:
            raise ValueError("missing_message_content")
    raw_timestamp = _first(merged, "timestamp")
    timestamp, assumed_utc = _parse_timestamp(raw_timestamp)
    receiver_id = _string(_first(merged, "receiver_id"))
    receiver_name = _string(_first(merged, "receiver_name"))
    external_message_id = _string(_first(merged, "message_id"))
    unresolved_conversation = conversation_id is None and conversation_title is None
    return NormalizedChatMessage(
        conversation_external_id=(
            conversation_id
            or conversation_title
            or f"unresolved:{source_file}"
        ),
        conversation_title=conversation_title,
        conversation_type=_conversation_type(
            _first(merged, "conversation_type"), conversation_title
        ),
        external_message_id=external_message_id,
        sender_ref=sender_ref,
        sender_name=sender_name,
        receiver_ref=receiver_id or receiver_name,
        receiver_name=receiver_name,
        content=content,
        message_type=message_type,
        timestamp=timestamp,
        source_file=source_file,
        source_row=source_row,
        metadata={
            "parser": "generic-chat-export-v1",
            "raw_timestamp": _string(raw_timestamp),
            "timestamp_assumed_utc": assumed_utc,
            "source_row": source_row,
            "conversation_identity_missing": unresolved_conversation,
        },
    )


def _json_rows(payload: Any) -> Iterator[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, Mapping):
                yield item, {}
        return
    if not isinstance(payload, Mapping):
        raise ValueError("unsupported_json_chat_shape")

    for key in ("messages", "chat_messages", "records", "data"):
        items = payload.get(key)
        if isinstance(items, list):
            inherited = {
                k: payload[k]
                for k in (
                    "conversation_id", "chat_id", "session_id", "conversation_title",
                    "chat_name", "conversation_type",
                )
                if k in payload
            }
            for item in items:
                if isinstance(item, Mapping):
                    yield item, inherited
            return

    for key in ("conversations", "chats", "sessions"):
        conversations = payload.get(key)
        if isinstance(conversations, list):
            for conversation in conversations:
                if not isinstance(conversation, Mapping):
                    continue
                messages = conversation.get("messages") or conversation.get("records")
                if not isinstance(messages, list):
                    continue
                inherited = {
                    k: v for k, v in conversation.items()
                    if k not in {"messages", "records"}
                }
                for item in messages:
                    if isinstance(item, Mapping):
                        yield item, inherited
            return
    raise ValueError("unsupported_json_chat_shape")


def _parse_json(body: bytes, source_file: str) -> Iterator[NormalizedChatMessage]:
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("invalid_json_chat_export") from None
    for row_index, (row, inherited) in enumerate(_json_rows(payload), start=1):
        if row_index > MAX_RECORDS_PER_FILE:
            raise ValueError("chat_record_limit_exceeded")
        yield _normalize_row(
            row,
            source_file=source_file,
            source_row=row_index,
            inherited=inherited,
        )


def _parse_csv(body: bytes, source_file: str) -> Iterator[NormalizedChatMessage]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("chat_text_must_be_utf8") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("missing_csv_header")
    for row_index, row in enumerate(reader, start=2):
        if row_index > MAX_RECORDS_PER_FILE + 1:
            raise ValueError("chat_record_limit_exceeded")
        if not any(value for value in row.values() if value):
            continue
        yield _normalize_row(row, source_file=source_file, source_row=row_index)


def _validate_xlsx_container(body: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = [item for item in archive.infolist() if not item.is_dir()]
            if len(members) > 10_000:
                raise ValueError("xlsx_member_limit_exceeded")
            names = {item.filename for item in members}
            if "xl/workbook.xml" not in names:
                raise ValueError("invalid_xlsx_chat_export")
            total_size = 0
            for item in members:
                path = PurePosixPath(item.filename)
                mode = item.external_attr >> 16
                if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                    raise ValueError("unsafe_xlsx_member_path")
                if stat.S_ISLNK(mode):
                    raise ValueError("xlsx_symlink_not_supported")
                if item.file_size > MAX_ZIP_MEMBER_BYTES:
                    raise ValueError("xlsx_member_size_limit_exceeded")
                total_size += item.file_size
                if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                    raise ValueError("xlsx_expansion_limit_exceeded")
                if item.file_size and item.compress_size == 0:
                    raise ValueError("invalid_xlsx_compression_ratio")
                if (
                    item.compress_size
                    and item.file_size / item.compress_size > MAX_ZIP_COMPRESSION_RATIO
                ):
                    raise ValueError("xlsx_compression_ratio_limit_exceeded")
    except zipfile.BadZipFile:
        raise ValueError("invalid_xlsx_chat_export") from None


def _parse_xlsx(body: bytes, source_file: str) -> Iterator[NormalizedChatMessage]:
    _validate_xlsx_container(body)
    try:
        workbook = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
    except Exception:
        raise ValueError("invalid_xlsx_chat_export") from None
    try:
        total = 0
        for sheet in workbook.worksheets:
            rows = sheet.iter_rows(values_only=True)
            headers = next(rows, None)
            if not headers:
                continue
            for row_index, values in enumerate(rows, start=2):
                total += 1
                if total > MAX_RECORDS_PER_FILE:
                    raise ValueError("chat_record_limit_exceeded")
                row = {
                    str(key or ""): value
                    for key, value in zip(headers, values, strict=False)
                    if key
                }
                if not any(value not in (None, "") for value in row.values()):
                    continue
                record = _normalize_row(row, source_file=source_file, source_row=row_index)
                yield NormalizedChatMessage(
                    **{
                        **record.__dict__,
                        "metadata": {**record.metadata, "sheet_name": sheet.title},
                    }
                )
    finally:
        workbook.close()


def _parse_text(body: bytes, source_file: str) -> Iterator[NormalizedChatMessage]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("chat_text_must_be_utf8") from None
    pending: dict[str, Any] | None = None
    pending_index = 0
    parsed_count = 0
    for line_index, line in enumerate(text.splitlines(), start=1):
        match = _TEXT_LINE.match(line)
        if match:
            if pending is not None:
                parsed_count += 1
                yield _normalize_row(
                    pending, source_file=source_file, source_row=pending_index
                )
            pending_index = line_index
            pending = {
                "timestamp": match.group("timestamp"),
                "sender_name": match.group("sender").strip(),
                "content": match.group("content"),
            }
        elif pending is not None and line.strip():
            pending["content"] = f"{pending['content']}\n{line.rstrip()}"
        if parsed_count > MAX_RECORDS_PER_FILE:
            raise ValueError("chat_record_limit_exceeded")
    if pending is not None:
        yield _normalize_row(pending, source_file=source_file, source_row=pending_index)
    elif parsed_count == 0:
        raise ValueError("unsupported_txt_chat_shape")


def _parse_html(body: bytes, source_file: str) -> Iterator[NormalizedChatMessage]:
    if b"WeKeep" in body and (
        b"location.replace" in body or b"const manifest" in body
    ):
        raise ValueError("wekeep_archive_requires_zip")
    soup = BeautifulSoup(body, "html.parser")
    tables = soup.find_all("table")
    for table_index, table in enumerate(tables, start=1):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        headers = [cell.get_text(" ", strip=True) for cell in rows[0].find_all(["th", "td"])]
        if not headers:
            continue
        for row_index, tr in enumerate(rows[1:], start=2):
            if row_index > MAX_RECORDS_PER_FILE + 1:
                raise ValueError("chat_record_limit_exceeded")
            values = [cell.get_text(" ", strip=True) for cell in tr.find_all(["td", "th"])]
            row = dict(zip(headers, values, strict=False))
            if not any(values):
                continue
            record = _normalize_row(row, source_file=source_file, source_row=row_index)
            yield NormalizedChatMessage(
                **{
                    **record.__dict__,
                    "metadata": {**record.metadata, "table_index": table_index},
                }
            )
        return

    lines = [line.strip() for line in soup.get_text("\n").splitlines() if line.strip()]
    text_body = "\n".join(lines).encode("utf-8")
    yield from _parse_text(text_body, source_file)


def _find_wekeep_manifest(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
) -> tuple[zipfile.ZipInfo, Mapping[str, Any]] | None:
    for item in members:
        if PurePosixPath(item.filename).suffix.casefold() not in {".html", ".htm"}:
            continue
        if item.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError("zip_member_size_limit_exceeded")
        with archive.open(item) as stream:
            body = stream.read(MAX_ZIP_MEMBER_BYTES + 1)
        if len(body) > MAX_ZIP_MEMBER_BYTES:
            raise ValueError("zip_member_size_limit_exceeded")
        if b"WeKeep" not in body or b"manifest" not in body:
            continue

        soup = BeautifulSoup(body, "html.parser")
        for script in soup.find_all("script"):
            script_text = str(script.string or script.get_text())
            marker = re.search(r"\bconst\s+manifest\s*=\s*", script_text)
            if marker is None:
                continue
            try:
                manifest, _ = json.JSONDecoder().raw_decode(
                    script_text, marker.end()
                )
            except json.JSONDecodeError:
                if "WeKeep" in script_text:
                    raise ValueError("invalid_wekeep_manifest") from None
                continue
            if isinstance(manifest, Mapping) and manifest.get("application") == "WeKeep":
                return item, manifest
    return None


def _wekeep_chunk_path(index_path: str, value: Any) -> str:
    raw_path = _string(value)
    if not raw_path or "\\" in raw_path or "\x00" in raw_path:
        raise ValueError("unsafe_wekeep_chunk_path")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or ".." in path.parts or path.suffix.casefold() != ".js":
        raise ValueError("unsafe_wekeep_chunk_path")
    resolved = PurePosixPath(index_path).parent / path
    if resolved.is_absolute() or ".." in resolved.parts:
        raise ValueError("unsafe_wekeep_chunk_path")
    return resolved.as_posix()


def _parse_wekeep_chunk(body: bytes) -> list[Mapping[str, Any]]:
    try:
        script = body.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("invalid_wekeep_chunk_encoding") from None
    marker = re.match(r"\s*window\.__wxChunk\s*=\s*", script)
    if marker is None:
        raise ValueError("invalid_wekeep_chunk")
    try:
        rows, end = json.JSONDecoder().raw_decode(script, marker.end())
    except json.JSONDecodeError:
        raise ValueError("invalid_wekeep_chunk_json") from None
    if script[end:].strip() not in {"", ";"}:
        raise ValueError("invalid_wekeep_chunk_payload")
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        raise ValueError("invalid_wekeep_chunk_shape")
    return rows


def _wekeep_timestamp(row: Mapping[str, Any], time_zone: str) -> Any:
    value = row.get("create_time")
    if value not in (None, ""):
        return value
    raw = _string(row.get("time_str"))
    if raw is None:
        raise ValueError("missing_message_timestamp")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_message_timestamp") from None
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(time_zone))
        except ZoneInfoNotFoundError:
            raise ValueError("invalid_wekeep_time_zone") from None
    return parsed.isoformat()


def _parse_wekeep_archive(
    archive: zipfile.ZipFile,
    members: list[zipfile.ZipInfo],
    filename: str,
    index_item: zipfile.ZipInfo,
    manifest: Mapping[str, Any],
) -> Iterator[NormalizedChatMessage]:
    chunk_specs = manifest.get("chunks")
    if not isinstance(chunk_specs, list) or not chunk_specs:
        raise ValueError("wekeep_manifest_has_no_chunks")

    members_by_name = {item.filename: item for item in members}
    specs: list[tuple[zipfile.ZipInfo, int | None]] = []
    seen_paths: set[str] = set()
    declared_total = 0
    all_counts_declared = True
    for chunk in chunk_specs:
        if not isinstance(chunk, Mapping):
            raise ValueError("invalid_wekeep_chunk_manifest")
        chunk_path = _wekeep_chunk_path(index_item.filename, chunk.get("file"))
        if chunk_path in seen_paths:
            raise ValueError("duplicate_wekeep_chunk_path")
        seen_paths.add(chunk_path)
        chunk_item = members_by_name.get(chunk_path)
        if chunk_item is None:
            raise ValueError("wekeep_chunk_missing")
        if chunk_item.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError("zip_member_size_limit_exceeded")

        expected_count = chunk.get("count")
        if expected_count is not None:
            if (
                not isinstance(expected_count, int)
                or isinstance(expected_count, bool)
                or expected_count < 0
            ):
                raise ValueError("invalid_wekeep_chunk_count")
            declared_total += expected_count
        else:
            all_counts_declared = False
        specs.append((chunk_item, expected_count))

    manifest_count = manifest.get("count")
    if manifest_count is not None:
        if (
            not isinstance(manifest_count, int)
            or isinstance(manifest_count, bool)
            or manifest_count < 0
        ):
            raise ValueError("invalid_wekeep_message_count")
        if all_counts_declared and declared_total != manifest_count:
            raise ValueError("wekeep_manifest_count_mismatch")

    manifest_session = _string(manifest.get("session"))
    conversation_title = _string(manifest.get("name")) or manifest_session
    time_zone = _string(manifest.get("time_zone")) or "UTC"
    version = _string(manifest.get("version"))
    exported_at = _string(manifest.get("exported_at"))
    total = 0

    for chunk_item, expected_count in specs:
        with archive.open(chunk_item) as stream:
            chunk_body = stream.read(MAX_ZIP_MEMBER_BYTES + 1)
        if len(chunk_body) > MAX_ZIP_MEMBER_BYTES:
            raise ValueError("zip_member_size_limit_exceeded")
        rows = _parse_wekeep_chunk(chunk_body)
        if expected_count is not None and len(rows) != expected_count:
            raise ValueError("wekeep_chunk_count_mismatch")
        if total + len(rows) > MAX_RECORDS_PER_FILE:
            raise ValueError("chat_record_limit_exceeded")

        source_file = f"{filename}:{chunk_item.filename}"
        for row in rows:
            total += 1
            session_id = _string(row.get("session")) or manifest_session
            if not session_id:
                raise ValueError("missing_wekeep_session")
            sender_id = _string(row.get("sender_wxid")) or _string(row.get("sender"))
            sender_name = _string(row.get("sender")) or sender_id
            if not sender_id or not sender_name:
                raise ValueError("missing_sender_identity")

            kind = _string(row.get("kind"))
            type_name = _string(row.get("type_name"))
            message_type = kind or type_name or "text"
            content = _string(row.get("text"))
            if content is None:
                placeholders = {
                    "image": "[图片]",
                    "voice": "[语音]",
                    "video": "[视频]",
                    "file": "[文件]",
                    "emoji": "[表情]",
                    "system": "[系统消息]",
                }
                content = placeholders.get(
                    (kind or "").casefold(),
                    f"[{type_name or '非文本消息'}]",
                )

            local_id = _string(row.get("local_id"))
            shard = _string(row.get("shard"))
            external_message_id = _string(row.get("server_id"))
            if external_message_id is None and local_id is not None:
                external_message_id = f"{shard or '0'}:{local_id}"

            mapped = {
                "conversation_id": session_id,
                "conversation_title": conversation_title,
                "conversation_type": (
                    "group" if session_id.casefold().endswith("@chatroom") else "direct"
                ),
                "message_id": external_message_id,
                "sender_id": sender_id,
                "sender_name": sender_name,
                "content": content,
                "message_type": message_type,
                "timestamp": _wekeep_timestamp(row, time_zone),
            }
            record = _normalize_row(
                mapped,
                source_file=source_file,
                source_row=total,
            )
            wekeep_metadata = {
                "application": "WeKeep",
                "version": version,
                "exported_at": exported_at,
                "time_zone": time_zone,
                "session": session_id,
                "local_id": row.get("local_id"),
                "shard": row.get("shard"),
                "kind": kind,
                "type": row.get("type"),
                "raw_type": row.get("raw_type"),
                "type_name": type_name,
                "is_sender": row.get("is_sender"),
                "avatar": row.get("avatar"),
                "resources": row.get("resources", []),
                "resource_status": row.get("resource_status"),
                "resource_notice": row.get("resource_notice"),
                "extra": row.get("extra", {}),
            }
            yield NormalizedChatMessage(
                **{
                    **record.__dict__,
                    "metadata": {
                        **record.metadata,
                        "parser": "wekeep-export-v1",
                        "wekeep": wekeep_metadata,
                    },
                }
            )

    if manifest_count is not None and total != manifest_count:
        raise ValueError("wekeep_manifest_count_mismatch")


def parse_chat_export(
    filename: str,
    body: bytes,
    *,
    _depth: int = 0,
) -> Iterator[NormalizedChatMessage]:
    """Yield normalized messages from a generic, user-exported chat file.

    The target-person flag is intentionally absent: identity is confirmed by the
    user in a later import step, never inferred by this parser.
    """
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.casefold()
    if suffix not in SUPPORTED_CHAT_EXTENSIONS:
        raise ValueError("unsupported_chat_export_type")
    if not body:
        raise ValueError("empty_chat_export")

    if suffix == ".zip":
        if _depth >= 1:
            raise ValueError("nested_zip_not_supported")
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                if len(members) > MAX_ZIP_MEMBERS:
                    raise ValueError("zip_member_limit_exceeded")
                total_size = 0
                for item in members:
                    path = PurePosixPath(item.filename)
                    mode = item.external_attr >> 16
                    if path.is_absolute() or ".." in path.parts or "\\" in item.filename:
                        raise ValueError("unsafe_zip_member_path")
                    if stat.S_ISLNK(mode):
                        raise ValueError("zip_symlink_not_supported")
                    if item.file_size > MAX_ZIP_MEMBER_BYTES:
                        raise ValueError("zip_member_size_limit_exceeded")
                    total_size += item.file_size
                    if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
                        raise ValueError("zip_expansion_limit_exceeded")
                    if item.file_size and item.compress_size == 0:
                        raise ValueError("invalid_zip_compression_ratio")
                    if (
                        item.compress_size
                        and item.file_size / item.compress_size > MAX_ZIP_COMPRESSION_RATIO
                    ):
                        raise ValueError("zip_compression_ratio_limit_exceeded")
                wekeep_manifest = _find_wekeep_manifest(archive, members)
                if wekeep_manifest is not None:
                    index_item, manifest = wekeep_manifest
                    yield from _parse_wekeep_archive(
                        archive,
                        members,
                        filename,
                        index_item,
                        manifest,
                    )
                    return

                found = False
                for item in members:
                    member_name = PurePosixPath(item.filename).name
                    member_suffix = PurePosixPath(member_name).suffix.casefold()
                    if member_suffix not in SUPPORTED_CHAT_EXTENSIONS - {".zip"}:
                        continue
                    found = True
                    with archive.open(item) as stream:
                        member_body = stream.read(MAX_ZIP_MEMBER_BYTES + 1)
                    if len(member_body) > MAX_ZIP_MEMBER_BYTES:
                        raise ValueError("zip_member_size_limit_exceeded")
                    yield from parse_chat_export(
                        f"{filename}:{item.filename}", member_body, _depth=_depth + 1
                    )
                if not found:
                    raise ValueError("zip_contains_no_supported_chat_files")
        except zipfile.BadZipFile:
            raise ValueError("invalid_zip_chat_export") from None
        return

    if suffix == ".json":
        yield from _parse_json(body, filename)
    elif suffix == ".csv":
        yield from _parse_csv(body, filename)
    elif suffix == ".xlsx":
        yield from _parse_xlsx(body, filename)
    elif suffix in {".html", ".htm"}:
        yield from _parse_html(body, filename)
    else:
        yield from _parse_text(body, filename)


_TEXT_MESSAGE_TYPES = {
    "text",
    "plain",
    "plain_text",
    "text_message",
    "message",
    "1",
    "0",
    "文本",
    "文字",
    "文本消息",
}
_NON_TEXT_PLACEHOLDERS = {
    "[图片]",
    "[语音]",
    "[视频]",
    "[文件]",
    "[表情]",
    "[系统消息]",
    "[非文本消息]",
}


def is_text_message(record: NormalizedChatMessage) -> bool:
    """Return True only for explicitly recognizable text-message records."""
    message_type = record.message_type.strip().casefold()
    if message_type not in _TEXT_MESSAGE_TYPES:
        return False
    content = record.content.strip()
    if not content or content in _NON_TEXT_PLACEHOLDERS:
        return False
    return True


def parse_text_chat_export(
    filename: str,
    body: bytes,
) -> Iterator[NormalizedChatMessage]:
    """Yield text messages only; media placeholders and media metadata are discarded."""
    for record in parse_chat_export(filename, body):
        if is_text_message(record):
            yield record
