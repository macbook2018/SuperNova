# RAG Live Acceptance — Staging

## Scope

This runbook verifies the complete retrieval path against the deployed Staging environment after a TXT document has been ingested successfully:

1. Tenant-owned knowledge base and document scope.
2. Knowledge Worker chunking and embedding using the configured platform Embedding model.
3. Query embedding through the same configured 1536-dimensional model.
4. PostgreSQL/pgvector cosine retrieval and lexical retrieval.
5. Hybrid ranking and source metadata returned to the caller.

The operator has already confirmed that a knowledge base can be created, a Persona can be created, and a TXT file can be parsed into the knowledge base. Those checks do not by themselves prove retrieval.

## Live test procedure

1. Sign in to the Staging Console as the tenant that owns the knowledge base.
2. Open the knowledge base and confirm the TXT document has status `ready`, a positive chunk count, and an active index version greater than zero. Do not test a document still queued, processing, or failed.
3. Select a question whose answer is explicitly present in the TXT file. Prefer a distinctive phrase, policy value, product feature, or named entity rather than a generic question.
4. Send an authenticated request to:

   `POST /knowledge-bases/{knowledge_base_id}/search`

   Request body:

   ```json
   {
     "query": "在这里填写 TXT 中有明确答案的问题",
     "top_k": 5
   }
   ```

5. Verify the response:
   - `knowledge_base_id` is the requested knowledge base.
   - At least one hit is returned for a deliberately answerable query.
   - `document_name` identifies the uploaded TXT file.
   - `snippet` contains the expected supporting sentence or passage.
   - `score` is a numeric similarity score and the result includes vector/lexical rank fields when each retrieval path matched.
6. Ask a second question about a fact that is not present in the TXT. It should not return a fabricated answer from the knowledge content. An empty or low-confidence result is acceptable.
7. If a Persona is linked to this knowledge base, send the same answerable question through that Persona's normal chat entry point. Verify that the answer follows the Persona instructions and is grounded in the retrieved passage. Where citations are enabled, verify the citation points to the same TXT document.
8. Repeat with a second tenant account and confirm it cannot list, search, or retrieve the first tenant's knowledge base or document.

## Pass criteria

- TXT document reaches `ready` and has indexed chunks.
- Search returns the expected TXT source and supporting snippet for an answerable query.
- An unrelated/unanswerable query does not produce a fabricated fact.
- Persona response is grounded in the linked knowledge base.
- Cross-tenant access is denied without revealing document content.

## Failure triage

- `knowledge_base_documents_not_ready`: inspect Knowledge Worker job state and logs; do not re-upload the same file repeatedly.
- `knowledge_retrieval_failed`: inspect API logs for the request ID, then verify the platform credential reference and Embedding provider availability.
- Dimension mismatch: confirm the deployed API and Knowledge Worker include the configured `dimensions` parameter and the provider returns 1536 values.
- Empty results despite a ready document: inspect chunk count, active index version, retrieval threshold, query wording, and whether the document was indexed into the selected knowledge base.
- Cross-tenant result: treat as a security incident; stop further acceptance and investigate tenant predicates before proceeding.

## Automated coverage already present

- `apps/api/tests/test_knowledge_embeddings.py`: request carries configured dimensions and rejects incorrect vector lengths.
- `apps/api/tests/test_knowledge_postgres.py`: PostgreSQL hybrid retrieval, Persona-linked knowledge-base scoping, and grounded citation integration coverage. The embedding response is deterministic/mocked in this database integration test; it is not a live external-provider test.
- `apps/api/tests/test_knowledge_retrieval.py`: tenant scoping, disabled knowledge bases, citation formatting, not-ready fail-closed behavior, and untrusted-content escaping.

A successful live Staging search and Persona chat are still required to mark end-to-end RAG acceptance complete.

## Manual Staging evidence — 2026-11-22

A live Console search was performed against the uploaded TXT knowledge document.

- Query: `多少钱`
- Source document: `新建 文本文档 (3).txt`
- Retrieved passage: `产品售价：129元`
- Displayed match score: `38%`
- Vector rank: `1`
- Lexical rank: no match displayed

Result: **vector retrieval passed**. The result points to the expected source document and the source passage contains the expected price.

Acceptance boundary:
- This observation confirms an actual query embedding + vector retrieval + source snippet return in Staging.
- It does not independently prove a lexical retrieval hit; the lexical rank was absent for this query.
- Persona end-to-end answer grounding/citation and the cross-tenant live negative test remain separate acceptance checks.
