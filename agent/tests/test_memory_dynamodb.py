"""Both memories on DynamoDB (moto, no AWS), with the exact table schema AgentStack
creates: PK/SK strings, on-demand. Proves the CDK tables fit langgraph-checkpoint-aws.

    .venv/bin/pytest agent/tests/test_memory_dynamodb.py
"""

from __future__ import annotations

import asyncio

import boto3
import pytest
from moto import mock_aws

from agent import memory
from agent.graph import build_graph
from agent.stream import stream_events
from agent.tests.fakes import AgentFactory, fake_classify
from agent.tools import listings, vector_index

SESSIONS, MEMORY = "test-sessions", "test-memory"


@pytest.fixture()
def tables(monkeypatch, tmp_path):
    monkeypatch.setenv("LISTINGS_SOURCE", "seed")
    monkeypatch.setenv("INDEX_URI", str(tmp_path / "none.npz"))
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("SESSIONS_TABLE", SESSIONS)
    monkeypatch.setenv("MEMORY_TABLE", MEMORY)
    listings.all_listings.cache_clear()
    vector_index.load.cache_clear()
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        for name in (SESSIONS, MEMORY):  # as infra/lib/agent-stack.ts defines them
            ddb.create_table(
                TableName=name,
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                ],
                BillingMode="PAY_PER_REQUEST",
            )
        yield ddb
    listings.all_listings.cache_clear()


def ask(g, prompt, session, visitor="visitor-1"):
    final: dict = {}

    async def go():
        req = {"prompt": prompt, "session_id": session, "visitor_id": visitor}
        return [e async for e in stream_events(req, graph=g, on_final=final.update)]

    events = asyncio.run(go())
    assert events[-1]["event_type"] == "done", events
    return final


def test_both_memories_on_dynamodb(tables):
    factory = AgentFactory(
        [("tool", "search_listings", {"query": "apartments"}), ("text", "Here you go.")]
    )
    g = build_graph(fake_classify("property_search"), factory)  # memory.py picks DynamoDB
    assert type(memory.checkpointer()).__name__ == "DynamoDBSaver"
    assert type(memory.store()).__name__ == "DynamoDBStore"

    ask(g, "I'm Sara, 2 bed in Business Bay under 3M", "session-aaaaaaaa")
    ask(g, "and in Downtown?", "session-aaaaaaaa")
    assert factory.made[1].messages[0]["content"][0]["text"].startswith("I'm Sara")

    # A fresh graph (a new AgentCore microVM) still sees both memories.
    g2 = build_graph(fake_classify("property_search"), factory)
    ask(g2, "anything in my budget?", "session-bbbbbbbb")
    assert factory.made[2].messages == []  # new session: no conversation
    assert "budget up to AED 3,000,000" in factory.made[2].prompt  # same visitor: profile

    items = tables.scan(TableName=SESSIONS)["Items"]
    assert items and all("ttl" in i for i in items if i["SK"]["S"] != "CHUNK")
    prof = tables.scan(TableName=MEMORY)["Items"]
    assert len(prof) == 1 and "expires_at" in prof[0]
