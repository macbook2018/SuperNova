from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.knowledge import retrieval


class FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class FakeSession:
    def __init__(
        self,
        *,
        knowledge_bases: list[object] | None = None,
        scalar_values: Iterator[int] | None = None,
    ) -> None:
        self.knowledge_bases = knowledge_bases or []
        self.scalar_values = scalar_values or iter(())

    def scalars(self, _statement):
        return FakeScalarResult(self.knowledge_bases)

    def scalar(self, _statement):
        return next(self.scalar_values)


def test_retrieve_knowledge_base_rejects_cross_tenant_before_database_access() -> None:
    class UnusedSession:
        def scalar(self, _statement):
            raise AssertionError("cross-tenant retrieval must stop before querying documents")

    knowledge_base = SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        enabled=True,
    )

    hits = retrieval.retrieve_knowledge_base(
        UnusedSession(),
        tenant_id=uuid4(),
        knowledge_base=knowledge_base,
        query="产品价格",
    )

    assert hits == []


def test_retrieve_knowledge_base_rejects_disabled_knowledge_base() -> None:
    class UnusedSession:
        def scalar(self, _statement):
            raise AssertionError("disabled knowledge base must not be queried")

    tenant_id = uuid4()
    knowledge_base = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        enabled=False,
    )

    hits = retrieval.retrieve_knowledge_base(
        UnusedSession(),
        tenant_id=tenant_id,
        knowledge_base=knowledge_base,
        query="产品价格",
    )

    assert hits == []


def test_retrieve_persona_context_returns_empty_when_no_knowledge_base_is_authorized() -> None:
    context, citations = retrieval.retrieve_persona_context(
        FakeSession(),
        tenant_id=uuid4(),
        persona_id=uuid4(),
        query="如何安装？",
    )

    assert context == ""
    assert citations == []


def test_retrieve_persona_context_returns_grounded_citations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    persona_id = uuid4()
    knowledge_base_id = uuid4()
    document_id = uuid4()
    chunk_id = uuid4()
    knowledge_base = SimpleNamespace(
        id=knowledge_base_id,
        tenant_id=tenant_id,
        enabled=True,
        name="产品知识库",
        retrieval_top_k=5,
        retrieval_score_threshold=0.15,
    )
    hit = {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "document_name": "安装说明.pdf",
        "source_type": "upload",
        "source_url": None,
        "page_number": 3,
        "sheet_name": None,
        "slide_number": None,
        "chunk_index": 2,
        "score": 0.91,
        "vector_rank": 1,
        "lexical_rank": 2,
        "snippet": "安装程序后输入授权码即可完成激活。",
        "content": "安装程序后输入授权码即可完成激活。",
        "metadata": {"page_number": 3},
        "rrf_score": 0.032,
    }
    session = FakeSession(
        knowledge_bases=[knowledge_base],
        scalar_values=iter([1, 1]),
    )
    calls: list[dict] = []

    def fake_retrieve(_db, **kwargs):
        calls.append(kwargs)
        return [hit]

    monkeypatch.setattr(retrieval, "retrieve_knowledge_base", fake_retrieve)

    context, citations = retrieval.retrieve_persona_context(
        session,
        tenant_id=tenant_id,
        persona_id=persona_id,
        query="如何激活软件？",
    )

    assert len(calls) == 1
    assert calls[0]["tenant_id"] == tenant_id
    assert calls[0]["knowledge_base"] is knowledge_base
    assert "安装程序后输入授权码即可完成激活。" in context
    assert "不可信的引用数据" in context
    assert len(citations) == 1
    assert citations[0]["knowledge_base_id"] == str(knowledge_base_id)
    assert citations[0]["document_id"] == str(document_id)
    assert citations[0]["chunk_id"] == str(chunk_id)
    assert citations[0]["page_number"] == 3
    assert citations[0]["snippet"] == hit["snippet"]


def test_retrieve_persona_context_fails_closed_when_documents_are_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = uuid4()
    knowledge_base = SimpleNamespace(
        id=uuid4(),
        tenant_id=tenant_id,
        enabled=True,
        name="尚未索引的知识库",
        retrieval_top_k=5,
        retrieval_score_threshold=0.15,
    )
    session = FakeSession(
        knowledge_bases=[knowledge_base],
        scalar_values=iter([2, 0]),
    )

    def unexpected_retrieval(*_args, **_kwargs):
        raise AssertionError("retrieval must not run before documents are ready")

    monkeypatch.setattr(retrieval, "retrieve_knowledge_base", unexpected_retrieval)

    with pytest.raises(RuntimeError, match="knowledge_base_documents_not_ready"):
        retrieval.retrieve_persona_context(
            session,
            tenant_id=tenant_id,
            persona_id=uuid4(),
            query="如何激活软件？",
        )
