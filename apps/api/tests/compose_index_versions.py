"""HTTP smoke test for the disposable development Compose stack."""

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import httpx

LOCAL_PROVIDER = "http://127.0.0.1:18365/v1"
EVIDENCE = "退货审批后七个工作日到账。"


class FixtureProvider(BaseHTTPRequestHandler):
    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != "Bearer compose-fixture":
            return self.send_error(401)
        if self.path == "/v1/embeddings" and payload.get("dimensions") == 1536:
            result = {
                "data": [
                    {"index": i, "embedding": [1.0] + [0.0] * 1535}
                    for i in range(len(payload["input"]))
                ],
                "usage": {"prompt_tokens": 10},
            }
        elif self.path == "/v1/chat/completions":
            if not any(EVIDENCE in str(m["content"]) for m in payload["messages"]):
                return self.send_error(422, "missing evidence in model context")
            if payload.get("stream"):
                chunk = {"choices": [{"index": 0, "delta": {"content": "七个工作日。"}}]}
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())
                return
            result = {
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": "七个工作日。"}}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 3, "total_tokens": 23},
            }
        else:
            return self.send_error(400)
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if not response.is_success:
        raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:400]}")
    return response.json()


def wait_ready(read_status):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        status = read_status()
        if status == "ready":
            return
        if status in {"failed", "cancelled", "dead_letter"}:
            raise RuntimeError(f"Unexpected terminal status: {status}")
        time.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for ready (last status: {status})")


def run_smoke(client, parser_version):
    tokens = request(
        client,
        "POST",
        "/auth/register",
        json={
            "email": f"index-{uuid4().hex}@example.com",
            "password": "compose-only-fixture-password",
            "display_name": "Index smoke",
        },
    )
    client.headers["Authorization"] = f"Bearer {tokens['access_token']}"
    provider = request(
        client,
        "POST",
        "/model-providers",
        json={
            "name": "Compose fixture",
            "base_url": LOCAL_PROVIDER,
            "api_key": "compose-fixture",
        },
    )
    kb = request(
        client,
        "POST",
        "/knowledge-bases",
        json={
            "name": "Smoke",
            "embedding_source": "byok",
            "embedding_provider_id": provider["id"],
            "embedding_model_name": "fixture-1536",
        },
    )
    base = f"/knowledge-bases/{kb['id']}"
    versions = base + "/index-versions"
    doc = request(
        client,
        "POST",
        base + "/documents/upload",
        files={
            "file": ("refund.md", ("# 退货规则\n\n" + EVIDENCE).encode(), "text/markdown"),
        },
    )
    wait_ready(
        lambda: next(
            d["status"] for d in request(client, "GET", base + "/documents") if d["id"] == doc["id"]
        )
    )

    def search():
        hit = request(client, "POST", base + "/search", json={"query": "退货多久到账"})["hits"][0]
        assert hit["document_id"] == doc["id"] and EVIDENCE in hit["snippet"]
        return hit["chunk_id"]

    legacy_id = search()
    generation = request(client, "GET", versions)["generation"]
    candidate = request(
        client,
        "POST",
        versions,
        json={
            "request_key": uuid4().hex,
            "expected_generation": generation,
            "config": {"parser_version": parser_version},
        },
    )
    detail = versions + "/" + candidate["id"]
    wait_ready(lambda: request(client, "GET", detail)["status"])
    assert search() == legacy_id, "Unpublished candidate was served"
    published = request(
        client,
        "POST",
        detail + "/publish",
        json={
            "expected_generation": generation,
        },
    )
    active_id = search()
    assert active_id != legacy_id, "Publish did not switch the query to the shadow chunks"

    persona = request(
        client,
        "POST",
        "/personas",
        json={
            "name": "退货客服",
            "slug": "refund",
            "system_prompt": "依据提供的资料回答",
            "provider_id": provider["id"],
            "model_name": "fixture-chat",
            "knowledge_base_ids": [kb["id"]],
        },
    )
    key = request(client, "POST", "/api-keys", json={"name": "Smoke"})
    headers = {"Authorization": f"Bearer {key['secret']}"}
    chat = {"model": persona["slug"], "messages": [{"role": "user", "content": "退款多久到账？"}]}
    response = request(client, "POST", "/v1/chat/completions", headers=headers, json=chat)
    assert response["supernova"]["citations"][0]["chunk_id"] == active_id
    stream = client.post("/v1/chat/completions", headers=headers, json={**chat, "stream": True})
    stream.raise_for_status()
    frames = [line[6:] for line in stream.text.splitlines() if line.startswith("data: ")]
    assert frames[-1] == "[DONE]"
    assert json.loads(frames[-2])["supernova"]["citations"][0]["chunk_id"] == active_id

    request(
        client,
        "POST",
        versions + "/rollback",
        json={
            "target_release_id": None,
            "expected_generation": published["generation"],
        },
    )
    assert search() == legacy_id, "Rollback must return the original v1 chunk ID"

    # The existing document reindex API must also retain exact old chunk IDs.
    document = f"{base}/documents/{doc['id']}"
    history = document + "/index-history"
    first_version = request(client, "GET", history)["active_index_version"]
    request(client, "POST", document + "/reindex")

    def read_rebuild_status():
        state = request(client, "GET", history)
        return "ready" if state["active_index_version"] > first_version else "building"

    wait_ready(read_rebuild_status)
    state = request(client, "GET", history)
    assert {r["version"] for r in state["revisions"]} >= {
        first_version,
        state["active_index_version"],
    }
    assert search() != legacy_id
    request(
        client,
        "POST",
        f"{history}/{first_version}/restore",
        json={
            "expected_active_index_version": state["active_index_version"],
            "expected_generation": state["generation"],
        },
    )
    assert search() == legacy_id, "Ordinary document rebuild did not retain original chunks"


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--serve", action="store_true")
    args.add_argument("--parser-version", default="legacy")
    args.add_argument("--api", default="http://127.0.0.1:8000")
    options = args.parse_args()
    if options.serve:
        ThreadingHTTPServer(("127.0.0.1", 18365), FixtureProvider).serve_forever()
    else:
        with httpx.Client(base_url=options.api, timeout=20, trust_env=False) as client:
            run_smoke(client, options.parser_version)
        print("Knowledge upload, shadow build, publish, citations and v1 rollback passed.")
