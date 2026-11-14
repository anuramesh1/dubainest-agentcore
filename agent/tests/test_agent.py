"""Graph wiring, SSE events, viewing flow and maths — with fake models, no AWS.

    .venv/bin/pytest agent
"""

from __future__ import annotations

import asyncio

import pytest

from agent.graph import build_graph
from agent.grounding import ungrounded
from agent.llm import Route
from agent.nodes.router import keyword_route
from agent.nodes.specialists import roi_numbers
from agent.prompts import CONFIRM_PHRASE, OFF_TOPIC_REPLY, SUGGESTIONS_MARKER
from agent.stream import SAFE_ERROR, stream_events
from agent.tools import leads, listings


@pytest.fixture(autouse=True)
def seed_listings(monkeypatch):
    monkeypatch.setenv("LISTINGS_SOURCE", "seed")
    listings.all_listings.cache_clear()
    yield
    listings.all_listings.cache_clear()


@pytest.fixture()
def saved_leads(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(leads, "WRITER", rows.append)
    return rows


def fake_classify(intent, second=None, confidence=0.9, calls=None):
    async def classify(system, user):
        if calls is not None:
            calls.append(user)
        return Route(intent=intent, second_intent=second, confidence=confidence), {
            "model": "fake-router",
            "input_tokens": 10,
            "output_tokens": 5,
        }

    return classify


def fake_writer(text, calls=None):
    """Streams `text` in 7-character pieces, so the marker is split across chunks."""

    async def stream_text(system, user, trace):
        if calls is not None:
            calls.append(user)
        for i in range(0, len(text), 7):
            yield text[i : i + 7]
        trace.update(model="fake-writer", input_tokens=100, output_tokens=50)

    return stream_text


ANSWER = (
    "**Burj Vista costs AED 1,850,000.**\n- 994 sqft\n"
    f"{SUGGESTIONS_MARKER}\n- What is the yield?\n- Any 2BR nearby?\n- Book a viewing\n- extra"
)


def run(graph, prompt, property_id=None, history=None):
    final: dict = {}

    async def go():
        return [
            e
            async for e in stream_events(
                {"prompt": prompt, "property_id": property_id, "history": history or []},
                graph=graph,
                on_final=final.update,
            )
        ]

    return asyncio.run(go()), final


# ── SSE contract ──────────────────────────────────────────────────────────────


def test_event_order_and_marker_never_leaks():
    g = build_graph(fake_classify("property_search"), fake_writer(ANSWER))
    events, final = run(g, "Tell me about Burj Vista")
    kinds = [e["event_type"] for e in events]
    assert kinds[0] == "status" and kinds[-1] == "done"
    assert kinds.count("suggestions") == 1 and kinds.index("suggestions") > kinds.index("chunk")
    assert all(e["role"] == "assistant" for e in events)
    text = "".join(e["content"] for e in events if e["event_type"] == "chunk")
    assert "<<" not in text and text.strip() == final["answer"]
    sugg = next(e["content"] for e in events if e["event_type"] == "suggestions")
    assert sugg == ["What is the yield?", "Any 2BR nearby?", "Book a viewing"]


def test_no_marker_means_no_suggestions_event():
    g = build_graph(fake_classify("general"), fake_writer("Hello! I can help with listings."))
    events, _ = run(g, "hi")
    assert "suggestions" not in [e["event_type"] for e in events]
    assert (
        "".join(e["content"] for e in events if e["event_type"] == "chunk")
        == "Hello! I can help with listings."
    )


def test_model_failure_gives_safe_error_and_no_done():
    async def broken(system, user, trace):
        raise RuntimeError("bedrock down")
        yield  # pragma: no cover

    events, _ = run(build_graph(fake_classify("general"), broken), "hi")
    assert events[-1] == {"role": "assistant", "event_type": "error", "content": SAFE_ERROR}
    assert "done" not in [e["event_type"] for e in events]


# ── Routing and graph shape ───────────────────────────────────────────────────


def test_off_topic_skips_writer():
    calls: list = []
    g = build_graph(fake_classify("off_topic"), fake_writer(ANSWER, calls))
    events, final = run(g, "write me a python script")
    assert calls == []
    assert [e["content"] for e in events if e["event_type"] == "chunk"] == [OFF_TOPIC_REPLY]
    assert [t["node"] for t in final["trace"]] == ["router", "off_topic"]


def test_two_intents_fan_out_then_one_writer_call():
    calls: list = []
    g = build_graph(fake_classify("property_search", "roi"), fake_writer(ANSWER, calls))
    _, final = run(g, "2BR in Business Bay and their yields")
    assert len(calls) == 1
    assert {"property_search", "roi"} <= set(final["facts"])
    models = [t for t in final["trace"] if "model" in t]
    assert len(models) == 2  # router + writer: the spec's two-call budget


def test_low_confidence_uses_keywords():
    g = build_graph(fake_classify("general", confidence=0.2), fake_writer(ANSWER))
    _, final = run(g, "what's the rental yield on a studio?")
    assert final["intents"] == ["roi"]
    assert final["trace"][0]["rule"].startswith("keywords_low_confidence")


def test_router_error_uses_keywords():
    async def broken(system, user):
        raise TimeoutError

    _, final = run(build_graph(broken, fake_writer(ANSWER)), "book a viewing please")
    assert final["intents"] == ["scheduler"]


def test_keyword_router():
    assert keyword_route("Can I book a viewing on Saturday?") == "scheduler"
    assert keyword_route("What's the yield on Burj Vista?") == "roi"
    assert keyword_route("How is the market in Downtown?") == "market_intel"
    assert keyword_route("Tell me about DAMAC Islands") == "property_search"
    assert keyword_route("hello there") == "general"


def test_open_listing_goes_to_router_and_facts():
    calls: list = []
    g = build_graph(fake_classify("property_search", calls=calls), fake_writer(ANSWER))
    _, final = run(g, "Is the price fair?", property_id="10")
    assert "DAMAC Islands" in calls[0]
    assert final["facts"]["property_search"]["listing"]["id"] == "10"


# ── Specialists ───────────────────────────────────────────────────────────────


def test_filters():
    f = listings.parse_filters("2 bed apartments in Business Bay or Downtown under 3M")
    assert f == {
        "areas": ["Business Bay", "Downtown Dubai"],
        "types": ["apartment"],
        "bedrooms": 2,
        "max_price": 3_000_000,
    }
    assert listings.parse_filters("more than 2 bedrooms")["min_bedrooms"] == 3
    assert "min_price" not in listings.parse_filters("handover from 2027 in Palm")
    assert listings.parse_filters("between 1.5 and 3 million") == {
        "min_price": 1_500_000,
        "max_price": 3_000_000,
    }
    assert listings.parse_filters("flats in the Marina")["unlisted_areas"] == ["Dubai Marina"]


def test_search_relaxes_and_says_so():
    r = listings.search(listings.parse_filters("2 bed apartments in Business Bay under 1M"))
    assert r["relaxed"] and r["listings"]


def test_roi_maths_burj_vista():
    n = roi_numbers(1_850_000, 5.25, 994)
    assert n["annual_rent_aed"] == 97_125
    assert n["annual_service_charges_aed"] == 994 * 18
    assert n["net_annual_rent_aed"] == 97_125 - 17_892
    assert n["mortgage"]["loan_aed"] == 1_387_500
    assert n["mortgage"]["dld_fee_aed"] == 74_000
    assert n["mortgage"]["cash_needed_aed"] == 462_500 + 74_000
    assert 7_700 < n["mortgage"]["monthly_payment_aed"] < 7_720  # 1.3875M, 4.5%, 25y
    assert n["rent_at_yield_aed"] == {"4%": 74_000, "5%": 92_500, "6%": 111_000}


def test_off_plan_roi_uses_area_benchmark_or_says_none():
    g = build_graph(fake_classify("roi"), fake_writer(ANSWER))
    _, final = run(g, "What's the expected ROI?", property_id="9")  # Chelsea, off-plan
    res = final["facts"]["roi"]["results"][0]
    assert res["yield_basis"].startswith("area benchmark") and res["gross_yield_pct"] == 6.0
    _, final = run(g, "What's the expected ROI?", property_id="10")  # DAMAC Islands, Dubailand
    assert "No yield estimate" in final["facts"]["roi"]["results"][0]["note"]


# ── Viewing flow: write only after an explicit yes ────────────────────────────


def test_viewing_flow(saved_leads):
    g = build_graph(fake_classify("scheduler"), fake_writer("ok"))
    _, f1 = run(g, "I'd like to book a viewing", property_id="10")
    s1 = f1["facts"]["scheduler"]
    assert s1["status"] == "need_details" and set(s1["missing"]) == {
        "date and time",
        "name",
        "email or phone",
    }
    history = [
        {"role": "user", "content": "I'd like to book a viewing"},
        {"role": "assistant", "content": "Sure, when and who?"},
    ]
    _, f2 = run(
        g,
        "Saturday at 11am, my name is Sara Khan, sara@example.com",
        property_id="10",
        history=history,
    )
    s2 = f2["facts"]["scheduler"]
    assert s2["status"] == "confirm" and saved_leads == []
    assert s2["request"]["when"] == "Saturday 11am" and s2["request"]["name"] == "Sara Khan"

    history += [
        {"role": "user", "content": "Saturday at 11am, my name is Sara Khan, sara@example.com"},
        {"role": "assistant", "content": f"Here is the request. {CONFIRM_PHRASE}"},
    ]
    _, f3 = run(g, "yes", property_id="10", history=history)
    assert f3["facts"]["scheduler"]["status"] == "saved"
    assert len(saved_leads) == 1
    lead = saved_leads[0]
    assert lead["propertyId"] == "10" and lead["email"] == "sara@example.com"
    assert lead["source"] == "chat_scheduler" and lead["preferredTime"] == "Saturday 11am"
    assert f3["trace"][0]["rule"] == "viewing_confirmation"  # no router model call


def test_yes_without_confirmation_writes_nothing(saved_leads):
    g = build_graph(fake_classify("scheduler"), fake_writer("ok"))
    history = [
        {"role": "user", "content": "Viewing Saturday 11am, I'm Sara, sara@example.com"},
        {"role": "assistant", "content": "Which property?"},
    ]
    run(g, "yes", property_id="10", history=history)
    assert saved_leads == []


# ── Grounding check ───────────────────────────────────────────────────────────


def test_grounding():
    facts = {"x": {"price_aed": 1_850_000, "rent": 97_125}}
    assert ungrounded("Price **AED 1,850,000**, rent AED 97,125.", facts) == []
    assert ungrounded("About AED 1.85M or AED 1.9M.", facts) == []
    assert ungrounded("Rent is 97,125 AED.", facts) == []
    assert ungrounded("It costs AED 2,100,000.", facts) == ["AED 2,100,000"]
    assert ungrounded("Budget AED 3M fits.", facts, user_text="my budget is 3M") == []


def test_second_intent_only_for_sensible_pairs():
    g = build_graph(fake_classify("roi", "scheduler"), fake_writer(ANSWER))
    _, final = run(g, "If I buy a 2M apartment with a mortgage, what's my monthly payment?")
    assert final["intents"] == ["roi"]
    assert final["trace"][0]["dropped_second_intent"] == "scheduler"


def test_grounding_ignores_sign():
    assert ungrounded("a negative cash flow of AED 13,313", {"cash_flow": -13_313}) == []


def test_details_in_one_message(saved_leads):
    g = build_graph(fake_classify("scheduler"), fake_writer("ok"))
    _, f = run(g, "Book a viewing of Burj Vista on Saturday 10am, I'm Sara Khan, sara@example.com")
    s = f["facts"]["scheduler"]
    assert s["status"] == "confirm", s
    assert s["request"]["name"] == "Sara Khan" and s["request"]["property"].startswith("Burj Vista")


def test_grounding_sees_the_open_listing():
    g = build_graph(fake_classify("scheduler"), fake_writer("Burj Vista is AED 1,850,000."))
    _, final = run(g, "Book a viewing", property_id="1")
    writer_trace = next(t for t in final["trace"] if t["node"] == "writer")
    assert writer_trace["ungrounded"] == []
