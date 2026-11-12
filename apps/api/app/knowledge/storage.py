from __future__ import annotations

from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings


@lru_cache
def _client():
    settings = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=30,
        ),
    )


def ensure_bucket() -> None:
    settings = get_settings()
    client = _client()
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in {"404", "NoSuchBucket", "NotFound"}:
            raise
        client.create_bucket(Bucket=settings.s3_bucket)


def put_object(object_key: str, body: bytes, content_type: str) -> None:
    settings = get_settings()
    ensure_bucket()
    _client().put_object(
        Bucket=settings.s3_bucket,
        Key=object_key,
        Body=body,
        ContentType=content_type,
        Metadata={"storage-version": "1"},
    )


def get_object(object_key: str, max_bytes: int) -> bytes:
    settings = get_settings()
    response = _client().get_object(Bucket=settings.s3_bucket, Key=object_key)
    length = int(response.get("ContentLength") or 0)
    if length > max_bytes:
        response["Body"].close()
        raise ValueError("stored document exceeds configured size limit")
    body = response["Body"].read(max_bytes + 1)
    response["Body"].close()
    if len(body) > max_bytes:
        raise ValueError("stored document exceeds configured size limit")
    return body


def delete_object(object_key: str) -> None:
    settings = get_settings()
    _client().delete_object(Bucket=settings.s3_bucket, Key=object_key)
