"""Run a synthetic RAG acceptance test against the isolated Hong Kong candidate."""
import argparse
import json
import os
import re
import secrets
import signal
import sys
import time
import unicodedata
from uuid import UUID, uuid4

import httpx

SILICON = "https://api.siliconflow.cn/v1"
DEEPSEEK = {"https://api.deepseek.com", "https://api.deepseek.com/v1"}
QWEN = "Qwen/Qwen3-Embedding-8B"
API = ("http://127.0.0.1:18182", "http://127.0.0.1:8000")


class CheckError(Exception):
    def __init__(self, code, blocked=False):
        self.code = code
        self.blocked = blocked


def check(ok, code):
    if not ok:
        raise CheckError(code)


def block(code):
    raise CheckError(code, blocked=True)


def model_ids():
    """Read only non-secret platform metadata inside the verified candidate API container."""
    from sqlalchemy import text
    from app.db.session import engine

    if engine.url.database != "hk_candidate" or engine.url.username != "hk_candidate":
        block("unexpected_candidate_database")
    with engine.connect() as conn:
        conn.exec_driver_sql("SET TRANSACTION READ ONLY")
        if tuple(conn.execute(text("select current_database(), current_user")).one()) != (
            "hk_candidate", "hk_candidate"
        ):
            block("unexpected_candidate_database")
        embed = conn.execute(text("""
            SELECT id FROM platform_embedding_models
            WHERE enabled AND verified_at IS NOT NULL AND dimensions = 1536
              AND upstream_model_name = :qwen AND rtrim(base_url,'/') = :sf
            ORDER BY id
        """), {"qwen": QWEN, "sf": SILICON}).scalars().all()
        chat = conn.execute(text("""
            SELECT m.id FROM platform_models m
            WHERE m.enabled AND m.provider_type <> 'anthropic'
              AND rtrim(m.base_url,'/') IN (:sf,:ds,:dsv1)
              AND EXISTS (SELECT 1 FROM platform_model_prices p
                          WHERE p.platform_model_id = m.id AND p.currency = 'CNY'
                          AND p.effective_from <= now()
                          AND (p.effective_until IS NULL OR p.effective_until > now()))
            ORDER BY m.id
        """), {"sf": SILICON, "ds": "https://api.deepseek.com",
               "dsv1": "https://api.deepseek.com/v1"}).scalars().all()
    return {"embedding_ids": list(map(str, embed)), "chat_ids": list(map(str, chat))}


def inputs():
    raw = os.environ.get("HK_RAG_EVAL_PROVIDER_JSON", "")
    if not raw and not sys.stdin.isatty():
        raw = sys.stdin.read(24577)
    if len(raw) > 24576:
        block("invalid_candidate_provider_config")
    try:
        cfg = json.loads(raw) if raw.strip() else {}
        raw_pins = os.environ.get("HK_RAG_EVAL_PLATFORM_JSON", "")
        if raw_pins.strip():
            pins = json.loads(raw_pins)
        elif os.environ.get("SUPERNOVA_DATABASE_URL"):
            pins = model_ids()
        else:
            pins = {"embedding_ids": [], "chat_ids": []}
        if not isinstance(cfg, dict) or not isinstance(pins, dict):
            raise ValueError
        if set(cfg) - {"embedding_api_key", "chat_api_key", "chat_base_url", "chat_model",
                       "reuse_embedding_key_for_chat"} or set(pins) != {"embedding_ids", "chat_ids"}:
            raise ValueError
        for field in ("embedding_ids", "chat_ids"):
            if not isinstance(pins[field], list):
                raise ValueError
            pins[field] = [str(UUID(i)) for i in pins[field]]
        for field in ("embedding_api_key", "chat_api_key"):
            if field in cfg and (not isinstance(cfg[field], str) or not cfg[field].strip()
                                 or cfg[field].strip() != cfg[field] or len(cfg[field]) > 4096):
                raise ValueError
        reuse = cfg.get("reuse_embedding_key_for_chat", False)
        if not isinstance(reuse, bool):
            raise ValueError
        if any(k in cfg for k in ("chat_api_key", "chat_base_url", "chat_model")) or reuse:
            host = cfg.get("chat_base_url", "")
            model = cfg.get("chat_model", "")
            if host not in DEEPSEEK | {SILICON} or not isinstance(model, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", model
            ):
                raise ValueError
            if reuse:
                if host != SILICON or not cfg.get("embedding_api_key") or "chat_api_key" in cfg:
                    raise ValueError
                cfg["chat_api_key"] = cfg["embedding_api_key"]
            if not cfg.get("chat_api_key"):
                raise ValueError
            if host != SILICON and cfg.get("embedding_api_key") == cfg["chat_api_key"]:
                raise ValueError
        if not (pins["embedding_ids"] or cfg.get("embedding_api_key")) or not (
            pins["chat_ids"] or cfg.get("chat_api_key")
        ):
            block("missing_candidate_model_access")
        return cfg, pins
    except (ValueError, TypeError, AttributeError):
        block("invalid_candidate_provider_config")


def api(client, method, path, **kwargs):
    resp = client.request(method, path, **kwargs)
    if not resp.is_success:
        raise CheckError("candidate_http_" + str(resp.status_code))
    if not resp.content:
        return None
    return resp.json()


def wait(read, limit=150):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        status = read()
        if status == "ready":
            return
        if status in {"failed", "cancelled", "dead_letter", "obsolete", "superseded"}:
            raise CheckError("candidate_index_terminal_failure")
        time.sleep(1)
    raise CheckError("candidate_index_wait_timeout")


def account(client):
    token = api(client, "POST", "/auth/register", json={
        "email": "rag-eval-" + uuid4().hex + "@example.com",
        "password": secrets.token_urlsafe(34),
        "display_name": "候选验收",
    })
    client.headers["Authorization"] = "Bearer " + token["access_token"]
    return token["refresh_token"]


def selection(catalog, pins, is_embedding=False):
    return next((m["id"] for m in catalog if m["id"] in pins and (
        not is_embedding or (m["model_name"] == QWEN and m["dimensions"] == 1536)
    )), None)


def provision(client, own, cfg, pins):
    embed = selection(api(client, "GET", "/platform/embedding-models"), pins["embedding_ids"], True)
    chat = selection(api(client, "GET", "/platform/models"), pins["chat_ids"])
    if not (embed or cfg.get("embedding_api_key")) or not (chat or cfg.get("chat_api_key")):
        block("missing_candidate_usable_model_pair")

    def byok(name, url, key):
        item = api(client, "POST", "/model-providers", json={
            "name": name, "base_url": url, "api_key": "disabled-placeholder", "enabled": False,
        })
        own["providers"].append(item["id"])
        # Obtain an owned ID before transmitting a dedicated key. Unexpected response loss
        # during PATCH still leaves the exact ID available for clearing in finally.
        result = api(client, "PATCH", "/model-providers/" + item["id"],
                     json={"api_key": key, "enabled": True})
        check(result["has_api_key"] and result["base_url"] == url, "candidate_provider_mismatch")
        return item["id"]

    kb = {"embedding_source": "platform", "platform_embedding_model_id": embed}
    persona = {"model_source": "platform", "platform_model_id": chat}
    if not embed:
        provider = byok("验收向量", SILICON, cfg["embedding_api_key"])
        kb = {"embedding_source": "byok", "embedding_provider_id": provider,
              "embedding_model_name": QWEN}
    if not chat:
        provider = byok("验收对话", cfg["chat_base_url"], cfg["chat_api_key"])
        persona = {"model_source": "byok", "provider_id": provider, "model_name": cfg["chat_model"]}
    return kb, persona


def benign_error_code(response):
    try:
        body = response.json()
        detail = body.get("detail", {})
        return detail.get("code") if isinstance(detail, dict) else None
    except (ValueError, AttributeError):
        return None


def ensure_no_foreign_access(other, base, detail, document):
    for method, path, values in (
        ("POST", base + "/search", {"json": {"query": "验收样本"}}),
        ("GET", detail, {}),
        ("GET", document + "/download", {}),
    ):
        response = other.request(method, path, **values)
        check(response.status_code == 404 and benign_error_code(response) == "knowledge_base_not_found",
              "candidate_tenant_isolation_failed")


def test_run(client, other, own, cfg, pins, report):
    own["refresh"] = account(client)
    kb_source, chat_source = provision(client, own, cfg, pins)
    report.update(embedding_source=kb_source["embedding_source"], chat_source=chat_source["model_source"])
    kb = api(client, "POST", "/knowledge-bases", json={
        **kb_source, "name": "香港验收", "embedding_dimensions": 1536,
    })
    base = "/knowledge-bases/" + kb["id"]
    own["kb"] = base
    code = "SN-" + "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ") for _ in range(6))
    amount = str(1000 + secrets.randbelow(9000))
    heading = "香港验收价目"
    fact = f"产品 {code} 的活动价为人民币 {amount} 元。"
    query = f"根据价目，产品 {code} 的活动价是多少元？请用阿拉伯数字回答。"
    data = ("# " + heading + "\n\n" + fact + "\n").encode()
    doc = api(client, "POST", base + "/documents/upload",
              files={"file": ("sample.md", data, "text/markdown")})
    document = base + "/documents/" + doc["id"]
    own["document"] = document
    wait(lambda: next((x["status"] for x in api(client, "GET", base + "/documents")
                       if x["id"] == doc["id"]), "waiting"))

    def lookup():
        hits = api(client, "POST", base + "/search", json={"query": query})["hits"]
        check(hits and hits[0]["document_id"] == doc["id"] and fact in hits[0]["snippet"],
              "candidate_evidence_missing")
        return hits[0]["chunk_id"]

    legacy_id = lookup()
    versions = base + "/index-versions"
    generation = api(client, "GET", versions)["generation"]
    release = api(client, "POST", versions, json={
        "request_key": uuid4().hex, "expected_generation": generation,
        "config": {"parser_version": "structured_v1"},
    })
    detail = versions + "/" + release["id"]
    wait(lambda: api(client, "GET", detail)["status"])
    check(lookup() == legacy_id, "unpublished_index_visible")
    switched = api(client, "POST", detail + "/publish", json={"expected_generation": generation})
    active_id = lookup()
    check(active_id != legacy_id and switched["active_release_id"] == release["id"],
          "candidate_publish_failed")
    report["structured_publish"] = True

    own["other_refresh"] = account(other)
    ensure_no_foreign_access(other, base, detail, document)
    report["tenant_denial"] = True
    persona = api(client, "POST", "/personas", json={
        **chat_source, "name": "验收客服", "slug": "rag-eval", "temperature": 0.1,
        "max_tokens": 512, "system_prompt": "按知识库给出的价格回答，仅用阿拉伯数字写出金额。",
        "knowledge_base_ids": [kb["id"]],
    })
    own["persona"] = "/personas/" + persona["id"]
    token = api(client, "POST", "/api-keys", json={"name": "香港验收", "expires_in_days": 1})
    own["api_key"] = "/api-keys/" + token["api_key"]["id"]
    body = {"model": persona["slug"], "messages": [{"role": "user", "content": query}]}
    headers = {"Authorization": "Bearer " + token["secret"]}
    request_ids = []
    for stream in (False, True):
        request_id = str(uuid4())
        request_ids.append(request_id)
        resp = client.post("/v1/chat/completions", json={**body, "stream": stream},
                           headers={**headers, "X-Request-ID": request_id})
        if not resp.is_success:
            if resp.status_code == 403 and resp.json().get("error", {}).get("code") == "subscription_required":
                block("candidate_subscription_required")
            raise CheckError("chat_http_" + str(resp.status_code))
        check(resp.headers.get("X-Request-ID") == request_id, "gateway_request_id_mismatch")
        if stream:
            check(resp.headers.get("Content-Type", "").startswith("text/event-stream"), "sse_type_invalid")
            frames = [line[5:].strip() for line in resp.iter_lines() if line.startswith("data:")]
            check(frames and frames.pop() == "[DONE]", "sse_incomplete")
            chunks = [json.loads(frame) for frame in frames]
            check(chunks and not any("error" in frame for frame in chunks), "sse_error")
            answer = "".join(choice.get("delta", {}).get("content") or ""
                             for frame in chunks for choice in frame.get("choices", []))
            sources = chunks[-1].get("supernova", {}).get("citations", [])
        else:
            result = resp.json()
            answer = result["choices"][0]["message"]["content"] or ""
            sources = result.get("supernova", {}).get("citations", [])
        normalized = unicodedata.normalize("NFKC", answer).replace(code, "")
        normalized = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", normalized)
        amounts = re.findall(r"(?<![0-9.])([0-9]+(?:\.[0-9]+)?)(?![0-9])", normalized)
        check(bool(amounts) and all(re.fullmatch(amount + r"(?:\.0+)?", n) for n in amounts),
              "answer_price_mismatch")
        check(any(c.get("chunk_id") == active_id and c.get("document_id") == doc["id"]
                  and c.get("index_release_id") == release["id"] and c.get("heading_path") == [heading]
                  and c.get("source_spans") and fact in c.get("snippet", "") for c in sources),
              "structured_citation_mismatch")
        report["sse_answer_and_citation" if stream else "answer_and_citation"] = True
    restored = api(client, "POST", versions + "/rollback", json={
        "target_release_id": None, "expected_generation": switched["generation"],
    })
    check(restored["active_release_id"] is None and lookup() == legacy_id, "rollback_chunk_id_mismatch")
    report["rollback_original_chunk"] = True
    use = api(client, "GET", base + "/usage")
    check(use["embedding_requests"] > 0 and use["retrieval_requests"] > 0, "retrieval_usage_missing")
    records = api(client, "GET", "/usage/records")
    matched = [r for r in records if r["request_id"] in request_ids and r["status"] == "succeeded"]
    check(len({r["request_id"] for r in matched}) == 2, "chat_usage_missing")
    # Legacy counters and per-operation observations overlap. Report each independently.
    report["embedding_operations"] = [
        {field: row[field] for field in ("operation", "status", "calls", "known_input_tokens",
                                        "known_output_tokens", "unknown_calls")}
        for row in use["operation_usage"]
    ]
    report.update(legacy_embedding_requests=use["embedding_requests"],
                  legacy_embedding_tokens=use["embedding_tokens"],
                  successful_chats=2, chat_tokens_reported=sum(r["total_tokens"] or 0 for r in matched),
                  chat_unknown_usage_count=sum(r["total_tokens"] is None for r in matched))


def cleanup(client, other, own):
    problems = []
    def attempt(label, fn):
        for _ in range(2):
            try:
                fn()
                return
            except Exception:
                pass
        problems.append(label)

    for provider in own["providers"]:
        def clear():
            item = api(client, "PATCH", "/model-providers/" + provider,
                       json={"clear_api_key": True, "enabled": False})
            check(not item["has_api_key"], "provider_key_clear_failed")
        attempt("provider_secret", clear)
    for resource in ("api_key", "persona", "document"):
        if resource in own:
            attempt(resource, lambda: api(client, "DELETE", own[resource]))
    if "kb" in own:
        def remove_kb():
            if "document" in own:
                wait(lambda: "ready" if not api(client, "GET", own["kb"] + "/documents") else "wait", 60)
            api(client, "DELETE", own["kb"])
        attempt("knowledge_base", remove_kb)
    for provider in own["providers"]:
        attempt("provider", lambda: api(client, "DELETE", "/model-providers/" + provider))
    for sess, key in ((client, "refresh"), (other, "other_refresh")):
        if own.get(key):
            attempt("logout", lambda: api(sess, "POST", "/auth/logout", json={"refresh_token": own[key]}))
    return sorted(set(problems))


def run(args, report):
    if args.platform_probe:
        print(json.dumps(model_ids()))
        return
    with httpx.Client(base_url=args.api, trust_env=False, follow_redirects=False, timeout=110) as client:
        ready = api(client, "GET", "/health/ready")
        check(ready.get("status") == "ready", "candidate_not_ready")
        report.update(api_ready=True)
        cfg, pins = inputs()
        if args.preflight:
            report.update(status="ready", scope="configuration_only")
            return
        own = {"providers": []}
        with httpx.Client(base_url=args.api, trust_env=False, follow_redirects=False, timeout=110) as other:
            try:
                test_run(client, other, own, cfg, pins, report)
            finally:
                problems = cleanup(client, other, own)
                if problems:
                    report["cleanup_failures"] = problems
                    raise CheckError("test_resource_cleanup_failed")
        report["status"] = "passed"


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--platform-probe", action="store_true")
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--accept", action="store_true")
    parser.add_argument("--api", choices=API, default=API[0])
    args = parser.parse_args()
    report = {}
    def interrupt(_signal, _frame):
        raise CheckError("interrupted")
    signal.signal(signal.SIGTERM, interrupt)
    try:
        run(args, report)
    except CheckError as exc:
        report.update(status="blocked" if exc.blocked else "failed", reason=exc.code)
    except (Exception, KeyboardInterrupt) as exc:
        reason = "candidate_transport_failed" if isinstance(exc, httpx.RequestError) else "acceptance_unexpected"
        report.update(status="failed", reason=reason)
    if report:
        print(json.dumps(report, sort_keys=True), flush=True)
    return {"failed": 1, "blocked": 2}.get(report.get("status"), 0)


if __name__ == "__main__":
    raise SystemExit(main())
