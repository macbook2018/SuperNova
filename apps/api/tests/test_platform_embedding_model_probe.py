from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api import embedding_models
from app.knowledge.embeddings import EmbeddingBatch
from app.schemas.knowledge import PlatformEmbeddingModelCreate


def _model(**overrides):
    values = {
        "id": uuid4(),
        "credential_ref": "siliconflow-embedding-primary",
        "base_url": "https://embedding.example.cn/v1",
        "provider_type": "openai_compatible",
        "upstream_model_name": "provider-embedding-model",
        "dimensions": 1536,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_platform_embedding_probe_uses_server_credential_and_checks_1536(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        embedding_models,
        "resolve_platform_api_key",
        lambda ref: calls.append(("credential", ref)) or "server-secret",
    )
    monkeypatch.setattr(
        embedding_models,
        "validate_public_service_url",
        lambda url: calls.append(("url", url)),
    )
    monkeypatch.setattr(
        embedding_models,
        "embed_texts",
        lambda config, texts: (
            calls.append(("request", config.model_name, config.api_key, texts))
            or EmbeddingBatch(
                vectors=[[0.01] * 1536],
                input_tokens=7,
                usage_known=True,
                request_count=1,
            )
        ),
    )

    model = _model()
    result = embedding_models._probe_embedding_model(model)

    assert result == {
        "success": True,
        "model_id": str(model.id),
        "model_name": "provider-embedding-model",
        "dimensions": 1536,
        "expected_dimensions": 1536,
        "input_tokens": 7,
    }
    assert ("credential", "siliconflow-embedding-primary") in calls
    assert ("request", "provider-embedding-model", "server-secret", [
        "SuperNova Embedding connectivity and 1536-dimensional vector validation."
    ]) in calls


def test_platform_embedding_probe_rejects_non_1536_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(embedding_models, "resolve_platform_api_key", lambda _ref: "server-secret")
    monkeypatch.setattr(embedding_models, "validate_public_service_url", lambda _url: None)
    monkeypatch.setattr(
        embedding_models,
        "embed_texts",
        lambda *_args, **_kwargs: EmbeddingBatch(
            vectors=[[0.01] * 1024],
            input_tokens=1,
            usage_known=True,
            request_count=1,
        ),
    )

    with pytest.raises(RuntimeError, match="embedding_provider_dimension_mismatch"):
        embedding_models._probe_embedding_model(_model())



def test_platform_embedding_schema_requires_1536_and_rejects_chat_only_provider() -> None:
    base = {
        "code": "domestic-embedding",
        "display_name": "国内 Embedding",
        "provider_type": "openai_compatible",
        "upstream_model_name": "provider-embedding-model",
        "base_url": "https://embedding.example.cn/v1",
        "credential_ref": "embedding-primary",
        "enabled": False,
    }
    assert PlatformEmbeddingModelCreate(**base).dimensions == 1536

    with pytest.raises(ValueError):
        PlatformEmbeddingModelCreate(**{**base, "dimensions": 1024})

    with pytest.raises(ValidationError):
        PlatformEmbeddingModelCreate(**{**base, "provider_type": "deepseek"})
