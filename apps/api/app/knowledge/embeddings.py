from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing.credentials import resolve_platform_api_key
from app.core.config import get_settings
from app.core.secret_encryption import decrypt_provider_api_key
from app.knowledge.network import validate_public_service_url
from app.knowledge.observations import observe_embedding_attempt
from app.models.catalog import ModelProvider
from app.models.knowledge import PlatformEmbeddingModel


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str
    api_key: str = field(repr=False)
    provider_type: str
    model_name: str
    dimensions: int


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    input_tokens: int
    usage_known: bool
    request_count: int


def _embeddings_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    path = urlparse(base).path.rstrip("/")
    if path.endswith("/embeddings"):
        return base
    if path.endswith("/v1"):
        return base + "/embeddings"
    return base + "/v1/embeddings"


def _resolve_config(
    db: Session,
    *,
    tenant_id: UUID,
    source: str,
    provider_id: UUID | None,
    byok_model_name: str | None,
    byok_dimensions: int,
) -> EmbeddingConfig:
    if source == "platform":
        if provider_id is None:
            raise ValueError("embedding_model_not_configured")
        model = db.get(PlatformEmbeddingModel, provider_id, populate_existing=True)
        if model is None or not model.enabled or model.verified_at is None:
            raise ValueError("platform_embedding_model_unavailable")
        api_key = resolve_platform_api_key(model.credential_ref)
        config = EmbeddingConfig(
            base_url=model.base_url,
            api_key=api_key,
            provider_type=model.provider_type,
            model_name=model.upstream_model_name,
            dimensions=model.dimensions,
        )
    elif source == "byok":
        if provider_id is None or not byok_model_name:
            raise ValueError("byok_embedding_provider_not_configured")
        provider = db.scalar(
            select(ModelProvider).where(
                ModelProvider.id == provider_id,
                ModelProvider.tenant_id == tenant_id,
                ModelProvider.enabled.is_(True),
            ).execution_options(populate_existing=True)
        )
        if provider is None or not provider.api_key_ciphertext:
            raise ValueError("byok_embedding_provider_unavailable")
        if provider.provider_type == "anthropic":
            raise ValueError("embedding_provider_not_supported")
        try:
            api_key = decrypt_provider_api_key(provider.api_key_ciphertext)
        except RuntimeError:
            raise ValueError("embedding_credentials_unavailable") from None
        config = EmbeddingConfig(
            base_url=provider.base_url,
            api_key=api_key,
            provider_type=provider.provider_type,
            model_name=byok_model_name,
            dimensions=byok_dimensions,
        )
    else:
        raise ValueError("embedding_source_invalid")

    validate_public_service_url(config.base_url)
    if config.dimensions != 1536:
        raise ValueError("embedding_dimensions_must_be_1536")
    return config


def _provider_id(knowledge_base) -> UUID | None:
    if knowledge_base.embedding_source == "platform":
        return knowledge_base.platform_embedding_model_id
    return knowledge_base.embedding_provider_id


def resolve_embedding_config(db: Session, knowledge_base) -> EmbeddingConfig:
    return _resolve_config(
        db,
        tenant_id=knowledge_base.tenant_id,
        source=knowledge_base.embedding_source,
        provider_id=_provider_id(knowledge_base),
        byok_model_name=knowledge_base.embedding_model_name,
        byok_dimensions=knowledge_base.embedding_dimensions,
    )


def _embedding_profile(source: str, provider_id: UUID, config: EmbeddingConfig) -> dict:
    return {
        "source": source,
        "provider_id": str(provider_id),
        "provider_type": config.provider_type,
        "model_name": config.model_name,
        "dimensions": config.dimensions,
        # Detect an endpoint change without persisting URLs or authentication parameters.
        "endpoint_sha256": hashlib.sha256(_embeddings_url(config.base_url).encode()).hexdigest(),
    }


def freeze_embedding_profile(db: Session, knowledge_base) -> dict:
    config = resolve_embedding_config(db, knowledge_base)
    return _embedding_profile(knowledge_base.embedding_source, _provider_id(knowledge_base), config)


def embedding_fingerprint(profile: dict) -> str:
    canonical = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def embedding_config_fingerprint(knowledge_base, resolved_config: EmbeddingConfig) -> str:
    profile = _embedding_profile(
        knowledge_base.embedding_source, _provider_id(knowledge_base), resolved_config
    )
    return embedding_fingerprint(profile)


def resolve_snapshot_embedding_config(
    db: Session, knowledge_base, profile: dict, expected_fingerprint: str
) -> EmbeddingConfig:
    if not isinstance(profile, dict) or embedding_fingerprint(profile) != expected_fingerprint:
        raise ValueError("index_embedding_profile_invalid")
    try:
        source = profile["source"]
        provider_id = UUID(profile["provider_id"])
        model_name = profile["model_name"]
        dimensions = profile["dimensions"]
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError("index_embedding_profile_invalid") from None
    config = _resolve_config(
        db,
        tenant_id=knowledge_base.tenant_id,
        source=source,
        provider_id=provider_id,
        byok_model_name=model_name,
        byok_dimensions=dimensions,
    )
    if _embedding_profile(source, provider_id, config) != profile:
        raise ValueError("index_embedding_profile_changed")
    return config


def _response_vectors(payload: dict, batch_size: int, dimensions: int) -> list[list[float]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or len(data) != batch_size:
        raise RuntimeError("embedding_provider_response_count_mismatch")
    by_index = {}
    for item in data:
        index = item.get("index") if isinstance(item, dict) else None
        if type(index) is not int or not 0 <= index < batch_size or index in by_index:
            raise RuntimeError("embedding_provider_invalid_index")
        vector = item.get("embedding")
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise RuntimeError("embedding_provider_dimension_mismatch")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in vector
        ):
            raise RuntimeError("embedding_provider_invalid_vector")
        by_index[index] = [float(value) for value in vector]
    return [by_index[index] for index in range(batch_size)]


def embed_texts(
    config: EmbeddingConfig,
    texts: list[str],
    on_batch: Callable[[], None] | None = None,
) -> EmbeddingBatch:
    if not texts:
        return EmbeddingBatch(vectors=[], input_tokens=0, usage_known=True, request_count=0)
    settings = get_settings()
    endpoint = _embeddings_url(config.base_url)
    vectors: list[list[float]] = []
    total_tokens = 0
    usage_known = True
    request_count = 0
    timeout = httpx.Timeout(settings.embedding_timeout_seconds, connect=10.0)

    with httpx.Client(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for offset in range(0, len(texts), 64):
            batch = texts[offset : offset + 64]
            with observe_embedding_attempt(config.model_name) as attempt:
                response = client.post(
                    endpoint,
                    headers={
                        "Authorization": "Bearer " + config.api_key,
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": config.model_name,
                        "input": batch,
                        "dimensions": config.dimensions,
                    },
                )
                request_count += 1
                if not 200 <= response.status_code < 300:
                    raise RuntimeError(f"embedding_provider_http_{response.status_code}")
                try:
                    payload = response.json()
                except ValueError:
                    raise RuntimeError("embedding_provider_invalid_json") from None
                usage = payload.get("usage") if isinstance(payload, dict) else None
                tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
                if type(tokens) is int and tokens >= 0:
                    attempt.input_tokens = tokens
                    total_tokens += tokens
                else:
                    usage_known = False
                vectors.extend(_response_vectors(payload, len(batch), config.dimensions))
            if on_batch is not None:
                on_batch()

    return EmbeddingBatch(
        vectors=vectors,
        input_tokens=total_tokens,
        usage_known=usage_known,
        request_count=request_count,
    )
