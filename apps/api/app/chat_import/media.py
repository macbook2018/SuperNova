from __future__ import annotations

import hashlib
import io
import json
import mimetypes
import stat
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from app.chat_import.parsers import (
    MAX_RECORDS_PER_FILE,
    MAX_ZIP_COMPRESSION_RATIO,
    MAX_ZIP_MEMBER_BYTES,
    MAX_ZIP_MEMBERS,
    MAX_ZIP_UNCOMPRESSED_BYTES,
    _find_wekeep_manifest,
)
from app.models.chat_import import ChatMessage, ChatParticipant

_MEDIA_TYPES = {"image", "voice", "video", "file", "emoji"}
_ALLOWED_STATUSES = {"available", "missing", "encrypted", "unsupported", "corrupt", "skipped"}
_ALLOWED_ROLES = {"original", "thumbnail", "preview", "avatar", "attachment"}


@dataclass(frozen=True)
class ExtractedChatMedia:
    conversation_id: UUID
    message_id: UUID | None
    participant_id: UUID | None
    asset_type: str
    role: str
    status: str
    archive_path: str | None
    original_name: str | None
    mime_type: str | None
    body: bytes | None
    sha256: str | None
    metadata: dict[str, Any]


def _safe_member_path(index_path: str, raw_path: Any) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("wekeep_media_path_missing")
    value = raw_path.strip()
    if "\\" in value or "\x00" in value:
        raise ValueError("wekeep_media_path_unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("wekeep_media_path_unsafe")
    resolved = PurePosixPath(index_path).parent / path
    if resolved.is_absolute() or ".." in resolved.parts:
        raise ValueError("wekeep_media_path_unsafe")
    resolved_value = resolved.as_posix()
    if len(resolved_value) > 512:
        raise ValueError("wekeep_media_path_unsafe")
    return resolved_value


def _validate_archive(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > MAX_ZIP_MEMBERS:
        raise ValueError("zip_member_count_limit_exceeded")

    total_size = 0
    by_name: dict[str, zipfile.ZipInfo] = {}
    for item in members:
        name = item.filename
        if not name or "\\" in name or "\x00" in name:
            raise ValueError("zip_member_path_unsafe")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("zip_member_path_unsafe")
        if name in by_name:
            raise ValueError("duplicate_zip_member")
        by_name[name] = item
        if item.is_dir():
            continue
        total_size += item.file_size
        if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError("zip_uncompressed_size_limit_exceeded")
        if item.file_size > MAX_ZIP_MEMBER_BYTES:
            raise ValueError("zip_member_size_limit_exceeded")
        if (
            item.compress_size > 0
            and item.file_size / item.compress_size > MAX_ZIP_COMPRESSION_RATIO
        ):
            raise ValueError("zip_compression_ratio_limit_exceeded")
    return by_name


def _normal_status(value: Any, fallback: str = "missing") -> str:
    status = str(value or fallback).strip().casefold()
    return status if status in _ALLOWED_STATUSES else fallback


def _resource_report(
    archive: zipfile.ZipFile,
    index_path: str,
    members: dict[str, zipfile.ZipInfo],
) -> dict[str, dict[str, Any]]:
    path = (PurePosixPath(index_path).parent / "resource-report.jsonl").as_posix()
    item = members.get(path)
    if item is None or item.is_dir() or item.file_size > 5 * 1024 * 1024:
        return {}
    try:
        with archive.open(item) as stream:
            body = stream.read(5 * 1024 * 1024 + 1)
        if len(body) > 5 * 1024 * 1024:
            return {}
        text = body.decode("utf-8-sig")
    except (UnicodeDecodeError, OSError, RuntimeError, zipfile.BadZipFile):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        message_id = str(row.get("message_id") or "").strip()
        if message_id:
            result[message_id] = row
    return result


def _read_resource(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    index_path: str,
    raw_path: Any,
    declared_status: Any,
    *,
    max_asset_bytes: int,
    remaining_bytes: int,
) -> tuple[str, str | None, bytes | None, str | None]:
    status = _normal_status(declared_status)
    if not isinstance(raw_path, str) or not raw_path.strip():
        return ("missing" if status == "available" else status), None, None, None
    try:
        path = _safe_member_path(index_path, raw_path)
    except ValueError:
        return "unsupported", None, None, None
    if status != "available":
        return status, path, None, None

    item = members.get(path)
    if item is None or item.is_dir():
        return "missing", path, None, None
    if item.flag_bits & 0x1:
        return "encrypted", path, None, None
    mode = item.external_attr >> 16
    if stat.S_ISLNK(mode):
        return "unsupported", path, None, None
    if item.file_size > max_asset_bytes:
        return "skipped", path, None, None
    if item.file_size > remaining_bytes:
        return "skipped", path, None, None

    try:
        with archive.open(item) as stream:
            body = stream.read(max_asset_bytes + 1)
    except RuntimeError:
        return "encrypted", path, None, None
    except (OSError, EOFError, zipfile.BadZipFile):
        return "corrupt", path, None, None
    if len(body) > max_asset_bytes or len(body) != item.file_size:
        return "corrupt", path, None, None
    return "available", path, body, hashlib.sha256(body).hexdigest()


def extract_wekeep_media(
    archive_body: bytes,
    messages: Iterable[ChatMessage],
    participants: list[ChatParticipant],
    *,
    import_file_id: UUID,
    max_asset_bytes: int,
    max_total_bytes: int,
) -> list[ExtractedChatMedia]:
    """Extract only declared WeKeep media members; never execute archive scripts."""
    participant_map = {
        (item.conversation_id, item.sender_ref): item
        for item in participants
    }
    results: list[ExtractedChatMedia] = []
    seen_avatars: set[tuple[UUID, str]] = set()
    extracted_bytes = 0

    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_body))
    except (OSError, zipfile.BadZipFile):
        raise ValueError("invalid_wekeep_media_archive") from None

    with archive:
        members = _validate_archive(archive)
        found = _find_wekeep_manifest(archive, list(members.values()))
        if found is None:
            raise ValueError("wekeep_manifest_not_found")
        index_item, manifest = found
        report_by_message = _resource_report(archive, index_item.filename, members)
        manifest_summary = manifest.get("resource_summary")
        if not isinstance(manifest_summary, dict):
            manifest_summary = {}

        for message in messages:
            metadata = message.metadata_json if isinstance(message.metadata_json, dict) else {}
            if metadata.get("parser") != "wekeep-export-v1":
                continue
            media_file_ref = metadata.get("media_import_file_id") or str(
                message.import_file_id
            )
            if media_file_ref != str(import_file_id):
                continue
            wekeep = metadata.get("wekeep")
            if not isinstance(wekeep, dict):
                continue

            report = report_by_message.get(str(message.external_message_id or ""), {})
            resources = wekeep.get("resources")
            if not isinstance(resources, list) or not resources:
                reported = report.get("resources")
                resources = reported if isinstance(reported, list) else []

            kind = str(wekeep.get("kind") or message.message_type or "other").casefold()
            notice = wekeep.get("resource_notice") or report.get("notice")
            roles_seen: set[str] = set()

            for resource in resources:
                if not isinstance(resource, dict):
                    continue
                asset_type = str(resource.get("kind") or kind).casefold()
                if asset_type not in _MEDIA_TYPES:
                    asset_type = "other"
                role = str(resource.get("role") or "original").casefold()
                if role not in _ALLOWED_ROLES or role == "avatar":
                    role = "original"
                roles_seen.add(role)

                declared_status = resource.get("status")
                status, path, payload, digest = _read_resource(
                    archive,
                    members,
                    index_item.filename,
                    resource.get("path"),
                    declared_status,
                    max_asset_bytes=max_asset_bytes,
                    remaining_bytes=max_total_bytes - extracted_bytes,
                )
                if payload is not None:
                    extracted_bytes += len(payload)
                archive_path = path
                raw_name = str(resource.get("name") or "").strip()
                original_name = (
                    PurePosixPath(raw_name.replace("\\", "/")).name[:255]
                    if raw_name
                    else (PurePosixPath(path).name[:255] if path else None)
                )
                mime_type = (
                    mimetypes.guess_type(archive_path or original_name or "")[0]
                    or None
                )
                report_metadata = {
                    key: report[key]
                    for key in (
                        "image_diagnostics",
                        "audio_diagnostics",
                        "network_diagnostics",
                    )
                    if key in report
                }
                results.append(
                    ExtractedChatMedia(
                        conversation_id=message.conversation_id,
                        message_id=message.id,
                        participant_id=None,
                        asset_type=asset_type,
                        role=role,
                        status=status,
                        archive_path=archive_path,
                        original_name=original_name,
                        mime_type=mime_type,
                        body=payload,
                        sha256=digest,
                        metadata={
                            "resource_quality": resource.get("quality"),
                            "resource_notice": notice,
                            "resource_report": report_metadata,
                            "manifest_resource_summary": manifest_summary,
                        },
                    )
                )

            resource_status = _normal_status(
                wekeep.get("resource_status") or report.get("status"),
                fallback="available" if resources else "missing",
            )
            if (
                kind in _MEDIA_TYPES
                and resource_status != "available"
                and "original" not in roles_seen
            ):
                results.append(
                    ExtractedChatMedia(
                        conversation_id=message.conversation_id,
                        message_id=message.id,
                        participant_id=None,
                        asset_type=kind,
                        role="original",
                        status=resource_status,
                        archive_path=None,
                        original_name=None,
                        mime_type=None,
                        body=None,
                        sha256=None,
                        metadata={
                            "resource_notice": notice,
                            "resource_report": {
                                key: report[key]
                                for key in (
                                    "image_diagnostics",
                                    "audio_diagnostics",
                                    "network_diagnostics",
                                )
                                if key in report
                            },
                        },
                    )
                )

            avatar_path = wekeep.get("avatar")
            participant = participant_map.get((message.conversation_id, message.sender_ref))
            if isinstance(avatar_path, str) and avatar_path.strip() and participant is not None:
                avatar_key = (participant.id, avatar_path)
                if avatar_key not in seen_avatars:
                    seen_avatars.add(avatar_key)
                    status, path, payload, digest = _read_resource(
                        archive,
                        members,
                        index_item.filename,
                        avatar_path,
                        "available",
                        max_asset_bytes=max_asset_bytes,
                        remaining_bytes=max_total_bytes - extracted_bytes,
                    )
                    if payload is not None:
                        extracted_bytes += len(payload)
                    mime_type = mimetypes.guess_type(path or avatar_path)[0] or None
                    results.append(
                        ExtractedChatMedia(
                            conversation_id=message.conversation_id,
                            message_id=None,
                            participant_id=participant.id,
                            asset_type="avatar",
                            role="avatar",
                            status=status,
                            archive_path=path,
                            original_name=PurePosixPath(path or avatar_path).name[:255],
                            mime_type=mime_type,
                            body=payload,
                            sha256=digest,
                            metadata={"source": "wekeep-avatar"},
                        )
                    )

            if len(results) > MAX_RECORDS_PER_FILE:
                raise ValueError("chat_media_asset_limit_exceeded")

    return results
