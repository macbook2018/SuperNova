import pytest

from app.knowledge.embeddings import EmbeddingConfig, embed_texts


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, **_kwargs):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def post(self, url, *, headers, json):
        self.calls.append((url, headers, json))
        return FakeResponse(
            {
                "data": [
                    {"index": 1, "embedding": [0.2] * 1536},
                    {"index": 0, "embedding": [0.1] * 1536},
                ],
                "usage": {"prompt_tokens": 42},
            }
        )


def test_embedding_adapter_sorts_vectors_and_records_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeClient()
    monkeypatch.setattr("app.knowledge.embeddings.httpx.Client", lambda **kwargs: client)
    heartbeats = []
    config = EmbeddingConfig(
        base_url="https://embedding.example.com/v1",
        api_key="test-key",
        provider_type="openai_compatible",
        model_name="test-embedding",
        dimensions=1536,
    )
    result = embed_texts(config, ["first", "second"], on_batch=lambda: heartbeats.append(True))

    assert result.input_tokens == 42
    assert result.usage_known is True
    assert result.request_count == 1
    assert result.vectors[0][0] == 0.1
    assert result.vectors[1][0] == 0.2
    assert heartbeats == [True]
    assert client.calls[0][0] == "https://embedding.example.com/v1/embeddings"
    assert client.calls[0][2] == {"model": "test-embedding", "input": ["first", "second"]}


def test_embedding_adapter_rejects_wrong_dimensions(monkeypatch: pytest.MonkeyPatch) -> None:
    class WrongDimensionClient(FakeClient):
        def post(self, url, *, headers, json):
            return FakeResponse(
                {
                    "data": [{"index": 0, "embedding": [0.1] * 1535}],
                    "usage": {"prompt_tokens": 1},
                }
            )

    monkeypatch.setattr(
        "app.knowledge.embeddings.httpx.Client",
        lambda **kwargs: WrongDimensionClient(),
    )
    config = EmbeddingConfig(
        base_url="https://embedding.example.com/v1",
        api_key="test-key",
        provider_type="openai_compatible",
        model_name="test-embedding",
        dimensions=1536,
    )
    with pytest.raises(RuntimeError, match="embedding_provider_dimension_mismatch"):
        embed_texts(config, ["text"])
