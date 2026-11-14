"""agent/main.py over HTTP, as AgentCore calls it: POST /invocations streams the
UI's SSE events; GET /ping is healthy. Fake models, no AWS.

    .venv/bin/pytest agent/tests/test_main.py
"""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from starlette.testclient import TestClient

import agent.stream
from agent.graph import build_graph
from agent.main import BAD_REQUEST, app
from agent.prompts import SUGGESTIONS_MARKER
from agent.tests.fakes import AgentFactory, fake_classify
from agent.tools import listings, vector_index

ANSWER = f"**Burj Vista is AED 1,850,000.**\n{SUGGESTIONS_MARKER}\n- What is its yield?"


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LISTINGS_SOURCE", "seed")
    monkeypatch.setenv("INDEX_URI", str(tmp_path / "none.npz"))
    listings.all_listings.cache_clear()
    vector_index.load.cache_clear()

    g = build_graph(
        fake_classify("property_search"),
        AgentFactory([("tool", "get_listing", {"listing": "Burj Vista"}), ("text", ANSWER)]),
        checkpointer=InMemorySaver(),
        store=InMemoryStore(),
    )
    monkeypatch.setattr(agent.stream, "default_graph", lambda: g)
    yield TestClient(app)
    listings.all_listings.cache_clear()


def events(r) -> list[dict]:
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def test_ping(client):
    assert client.get("/ping").json()["status"] == "Healthy"


def test_invocation_streams_the_ui_contract(client):
    r = client.post("/invocations", json={"prompt": "Tell me about Burj Vista", "history": []})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events(r)
    kinds = [e["event_type"] for e in evs]
    assert kinds[0] == "status" and kinds[-1] == "done" and "suggestions" in kinds
    assert all(set(e) == {"role", "event_type", "content"} for e in evs)
    text = "".join(e["content"] for e in evs if e["event_type"] == "chunk")
    assert text.startswith("**Burj Vista") and "<<" not in text


@pytest.mark.parametrize("body", [{}, {"prompt": "  "}, {"prompt": 42}])
def test_missing_prompt_is_a_safe_error(client, body):
    evs = events(client.post("/invocations", json=body))
    assert [e["event_type"] for e in evs] == ["error"] and evs[0]["content"] == BAD_REQUEST
