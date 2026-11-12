from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import urlparse
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing.credentials import resolve_platform_api_key
from app.core.config import get_settings
from app.core.secret_encryption import decrypt_provider_api_key
from app.knowledge.network import validate_public_service_url
from app.models.catalog import ModelProvider
from app.models.knowledge import PlatformEmbeddingModel


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str
    api_key: str
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


def resolve_embedding_config(db: Session, knowledge_base) -> EmbeddingConfig:
    if knowledge_base.embedding_source == "platform":
        if knowledge_base.platform_embedding_model_id is None:
            raise ValueError("embedding_model_not_configured")
        model = db.get(PlatformEmbeddingModel, knowledge_base.platform_embedding_model_id)
        if model is None or not model.enabled:
            raise ValueError("platform_embedding_model_unavailable")
        api_key = resolve_platform_api_key(model.credential_ref)
        config = EmbeddingConfig(
            base_url=model.base_url,
            api_key=api_key,
            provider_type=model.provider_type,
            model_name=model.upstream_model_name,
            dimensions=model.dimensions,
        )
    elif knowledge_base.embedding_source == "byok":
        if knowledge_base.embedding_provider_id is None or not knowledge_base.embedding_model_name:
            raise ValueError("byok_embedding_provider_not_configured")
        provider = db.scalar(
            select(ModelProvider).where(
                ModelProvider.id == knowledge_base.embedding_provider_id,
                ModelProvider.tenant_id == knowledge_base.tenant_id,
                ModelProvider.enabled.is_(True),
            )
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
            model_name=knowledge_base.embedding_model_name,
            dimensions=knowledge_base.embedding_dimensions,
        )
    else:
        raise ValueError("embedding_source_invalid")

    validate_public_service_url(config.base_url)
    if config.dimensions != 1536:
        raise ValueError("embedding_dimensions_must_be_1536")
    return config


def embed_texts(
    config: EmbeddingConfig,
    texts: list[str],
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
            response = client.post(
                endpoint,
                headers={
                    "Authorization": "Bearer " + config.api_key,
                    "Content-Type": "application/json",
                },
                json={"model": config.model_name, "input": batch},
            )
            request_count += 1
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f"embedding_provider_http_{response.status_code}")
            try:
                payload = response.json()
            except ValueError:
                raise RuntimeError("embedding_provider_invalid_json") from None
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list) or len(data) != len(batch):
                raise RuntimeError("embedding_provider_response_count_mismatch")
            data = sorted(
                data,
                key=lambda item: item.get("index", 0) if isinstance(item, dict) else -1,
            )
            for item in data:
                vector = item.get("embedding") if isinstance(item, dict) else None
                if not isinstance(vector, list) or len(vector) != config.dimensions:
                    raise RuntimeError("embedding_provider_dimension_mismatch")
                if any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in vector
                ):
                    raise RuntimeError("embedding_provider_invalid_vector")
                vectors.append([float(value) for value in vector])
            usage = payload.get("usage") if isinstance(payload, dict) else None
            tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens >= 0:
                total_tokens += tokens
            else:
                usage_known = False

    return EmbeddingBatch(
        vectors=vectors,
        input_tokens=total_tokens,
        usage_known=usage_known,
        request_count=request_count,
    )
