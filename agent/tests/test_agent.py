"""The graph with fake models (no AWS): SSE events, the output gate, routing,
the Strands tools, the viewing flow, and both kinds of memory.

    .venv/bin/pytest agent
"""

from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from agent import memory
from agent.graph import build_graph
from agent.grounding import ungrounded
from agent.nodes.router import keyword_route
from agent.prompts import CONFIRM_PHRASE, OFF_TOPIC_REPLY, SUGGESTIONS_MARKER
from agent.specialists.gate import STOP_LINE, OutputGate
from agent.specialists.tools import ToolBox, tool_names
from agent.stream import SAFE_ERROR, stream_events
from agent.tests.fakes import AgentFactory, fake_classify
from agent.tools import leads, listings, roi, vector_index


@pytest.fixture(autouse=True)
def seed_listings(monkeypatch, tmp_path):
    monkeypatch.setenv("LISTINGS_SOURCE", "seed")
    monkeypatch.setenv("INDEX_URI", str(tmp_path / "no-index.npz"))  # no Titan calls in tests
    listings.all_listings.cache_clear()
    vector_index.load.cache_clear()
    yield
    listings.all_listings.cache_clear()
    vector_index.load.cache_clear()


@pytest.fixture()
def saved_leads(monkeypatch):
    rows: list[dict] = []
    monkeypatch.setattr(leads, "WRITER", rows.append)
    return rows


ANSWER = (
    "**Burj Vista costs AED 1,850,000.**\n- 994 sqft, ready now.\n"
    f"{SUGGESTIONS_MARKER}\n- What is the yield?\n- Any 2BR nearby?\n- Book a viewing\n- extra"
)
SEARCH = ("tool", "search_listings", {"query": "Burj Vista"})


def graph(classify, script, **kw):
    factory = AgentFactory(script)
    g = build_graph(
        classify,
        factory,
        checkpointer=kw.get("saver") or InMemorySaver(),
        store=kw.get("store") or InMemoryStore(),
    )
    return g, factory


def run(g, prompt, session="s-1", visitor="v-1", property_id=None, history=None):
    final: dict = {}

    async def go():
        req = {
            "prompt": prompt,
            "property_id": property_id,
            "history": history or [],
            "session_id": session,
            "visitor_id": visitor,
        }
        return [e async for e in stream_events(req, graph=g, on_final=final.update)]

    return asyncio.run(go()), final


def text_of(events):
    return "".join(e["content"] for e in events if e["event_type"] == "chunk")


def agent_trace(final):
    return next(t for t in final["trace"] if t.get("framework") == "strands")


# ── SSE contract and the output gate ─────────────────────────────────────────


def test_event_order_narration_dropped_marker_held():
    g, _ = graph(
        fake_classify("property_search"), [("text", "Let me search. "), SEARCH, ("text", ANSWER)]
    )
    events, final = run(g, "Tell me about Burj Vista")
    kinds = [e["event_type"] for e in events]
    assert kinds[0] == "status" and kinds[-1] == "done"
    assert {"role": "assistant", "event_type": "status", "content": "Searching listings…"} in events
    assert kinds.index("suggestions") > max(i for i, k in enumerate(kinds) if k == "chunk")
    text = text_of(events)
    assert "Let me search" not in text and "<<" not in text and "SUGGESTIONS" not in text
    assert text.startswith("**Burj Vista costs AED 1,850,000.**") and text.endswith("ready now.")
    assert events[kinds.index("suggestions")]["content"] == [
        "What is the yield?",
        "Any 2BR nearby?",
        "Book a viewing",
    ]
    t = agent_trace(final)
    assert (
        t["narration_dropped"] == 1 and t["tools"] == ["search_listings"] and t["model_calls"] == 2
    )


def test_answer_streams_sentence_by_sentence():
    sent: list[dict] = []
    gate = OutputGate(sent.append, {"x": [{"price_aed": 1_850_000}]}, "")
    gate.on_tool({"toolUseId": "1", "name": "get_listing"})
    for piece in ["Burj Vista is AED 1,85", "0,000. It is ", "ready", " now. Ask"]:
        gate.on_text(piece)
    chunks = [e["content"] for e in sent if e["event_type"] == "chunk"]
    assert chunks == ["Burj Vista is AED 1,850,000.", " It is ready now."]  # " Ask" still held
    gate.on_turn_end(had_tool_use=False)
    assert gate.answer == "Burj Vista is AED 1,850,000. It is ready now. Ask"


def test_gate_blocks_an_invented_figure_and_cancels_the_agent():
    script = [
        SEARCH,
        ("text", "Burj Vista is AED 1,850,000. Its neighbour is AED 9,999,999. More text. " * 3),
    ]
    g, factory = graph(fake_classify("property_search"), script)
    events, final = run(g, "Tell me about Burj Vista")
    text = text_of(events)
    assert "AED 1,850,000" in text and "9,999,999" not in text
    assert text.endswith(STOP_LINE)
    assert "suggestions" not in [e["event_type"] for e in events]
    assert agent_trace(final)["ungrounded"] == ["AED 9,999,999"]
    assert factory.made[0].cancelled  # no more tokens spent after the block


def test_figures_from_earlier_answers_are_allowed():
    sent: list[dict] = []
    gate = OutputGate(
        sent.append, {}, "", context={"earlier_answers": "DAMAC Prive is AED 1,585,000."}
    )
    gate.on_text("You asked about DAMAC Prive at AED 1,585,000.")
    gate.on_turn_end(had_tool_use=False)
    assert not gate.stopped and "AED 1,585,000" in gate.answer


def test_no_marker_means_no_suggestions_event():
    g, _ = graph(fake_classify("general"), [("text", "Hello! How can I help?")])
    events, _ = run(g, "hi")
    assert "suggestions" not in [e["event_type"] for e in events]
    assert text_of(events) == "Hello! How can I help?"


def test_agent_failure_gives_safe_error_and_no_done():
    g, _ = graph(fake_classify("general"), [("raise", RuntimeError("bedrock down"))])
    events, _ = run(g, "hi")
    assert [e["event_type"] for e in events] == ["status", "error"]
    assert events[-1]["content"] == SAFE_ERROR


def test_off_topic_runs_no_agent():
    g, factory = graph(fake_classify("off_topic"), [("text", "never")])
    events, final = run(g, "Write me a poem")
    assert text_of(events) == OFF_TOPIC_REPLY and factory.made == []
    assert final["route"] == "off_topic"


# ── Routing ──────────────────────────────────────────────────────────────────


def test_two_intents_give_one_agent_both_toolsets():
    g, factory = graph(fake_classify("property_search", "roi"), [("text", "ok")])
    _, final = run(g, "2BR in Business Bay and their yields")
    assert final["route"] == "property_agent" and final["intents"] == ["property_search", "roi"]
    assert {"search_listings", "calculate_roi"} <= set(factory.made[0].tools)
    assert "This question needs both" in factory.made[0].system


def test_second_intent_only_for_sensible_pairs():
    g, _ = graph(fake_classify("roi", "scheduler"), [("text", "ok")])
    _, final = run(g, "If I buy a 2M apartment with a mortgage, what's my monthly payment?")
    assert final["intents"] == ["roi"] and final["route"] == "roi_agent"
    router = next(t for t in final["trace"] if t["node"] == "router")
    assert router["dropped_second_intent"] == "scheduler"


def test_low_confidence_and_errors_use_keywords():
    g, _ = graph(fake_classify("general", confidence=0.2), [("text", "ok")])
    _, final = run(g, "What's the yield on Burj Vista?")
    assert final["route"] == "roi_agent"

    async def broken(system, user):
        raise RuntimeError("throttled")

    g, _ = graph(broken, [("text", "ok")])
    _, final = run(g, "Book a viewing for Saturday")
    assert final["route"] == "viewing_agent"


def test_keyword_router():
    assert keyword_route("Can I book a viewing on Saturday?") == "scheduler"
    assert keyword_route("What's the yield on Burj Vista?") == "roi"
    assert keyword_route("How is the market in Downtown?") == "market_intel"
    assert keyword_route("Tell me about DAMAC Islands") == "property_search"
    assert keyword_route("hello there") == "general"


def test_open_listing_reaches_router_and_agent():
    calls: list = []
    g, factory = graph(
        fake_classify("property_search", calls=calls), [("tool", "get_listing", {}), ("text", "ok")]
    )
    _, final = run(g, "Is the price fair?", property_id="10")
    assert "DAMAC Islands" in calls[0] and "DAMAC Islands" in factory.made[0].prompt
    assert final["facts"]["get_listing"][0]["listing"]["id"] == "10"  # empty arg = open listing


# ── Tools ────────────────────────────────────────────────────────────────────


def tools(names, user_text="", open_listing=None):
    box = ToolBox(user_text, open_listing)
    return box, {t.tool_name: t for t in box.build(names)}


def test_search_tool_records_facts_and_says_when_relaxed():
    box, t = tools(["search_listings"])
    r = t["search_listings"](query="2 bed apartments in Business Bay under 1M")
    assert r["relaxed"] and r["exact_matches"] == 0 and "Nothing matched" in r["note"]
    assert box.facts["search_listings"] == [r] and box.seen_ids
    r = t["search_listings"](query="apartments", area="Business Bay", max_price_aed=3_000_000)
    assert r["filters"]["areas"] == ["Business Bay"] and r["filters"]["max_price"] == 3_000_000


def test_compare_tool_precomputes_the_gaps():
    _, t = tools(["compare_listings"])
    r = t["compare_listings"](listings_to_compare=["Burj Vista", "Park Heights"])
    assert len(r["listings"]) == 2 and r["comparison"]["price_difference_aed"] > 0


def test_roi_maths_burj_vista():
    n = roi.roi_numbers(1_850_000, 5.25, 994)
    assert n["annual_rent_aed"] == 97_125
    assert n["annual_service_charges_aed"] == 994 * 18
    assert n["net_annual_rent_aed"] == 97_125 - 17_892
    assert n["mortgage"]["loan_aed"] == 1_387_500
    assert n["mortgage"]["dld_fee_aed"] == 74_000
    assert n["mortgage"]["cash_needed_aed"] == 462_500 + 74_000
    assert 7_700 < n["mortgage"]["monthly_payment_aed"] < 7_720  # 1.3875M, 4.5%, 25y
    assert n["rent_at_yield_aed"] == {"4%": 74_000, "5%": 92_500, "6%": 111_000}


def test_roi_tool_off_plan_uses_area_benchmark_or_says_none():
    _, t = tools(["calculate_roi"])
    chelsea = t["calculate_roi"](listing="9")
    assert chelsea["yield_basis"].startswith("area benchmark") and chelsea["gross_yield_pct"] == 6.0
    assert "No yield estimate" in t["calculate_roi"](listing="DAMAC Islands")["note"]
    hypo = t["calculate_roi"](price_aed=2_000_000, gross_yield_pct=6, size_sqft=1000)
    assert hypo["hypothetical"] and hypo["annual_rent_aed"] == 120_000


def test_request_viewing_rejects_details_the_visitor_never_typed():
    box, t = tools(["request_viewing"], user_text="Saturday 11am please, I'm Sara")
    r = t["request_viewing"](
        listing="Burj Vista", preferred_time="Saturday 11am", name="Sara", email="sara@made.up"
    )
    assert r["status"] == "need_details" and r["missing"] == ["email or phone"]
    assert r["not_from_visitor"] == ["email"] and box.pending_viewing is None


def test_tool_sets_per_intent():
    assert tool_names(["scheduler"]) == ("request_viewing", "get_listing")
    assert tool_names(["property_search", "roi"]) == (
        "search_listings",
        "get_listing",
        "compare_listings",
        "calculate_roi",
    )


# ── Viewing flow: a lead is written only after "yes" ─────────────────────────

VIEW = (
    "tool",
    "request_viewing",
    {
        "listing": "",
        "preferred_time": "Saturday 11am",
        "name": "Sara Khan",
        "email": "sara@example.com",
    },
)


def test_viewing_flow_uses_conversation_memory(saved_leads):
    g, _ = graph(fake_classify("scheduler"), [VIEW, ("text", f"Here it is. {CONFIRM_PHRASE}")])
    _, f1 = run(g, "Viewing Saturday 11am, I'm Sara Khan, sara@example.com", property_id="10")
    assert f1["pending_viewing"]["property_id"] == "10" and saved_leads == []

    events, f2 = run(g, "yes")  # same session: the pending request comes from the checkpoint
    assert f2["route"] == "confirm_viewing" and len(saved_leads) == 1
    lead = saved_leads[0]
    assert lead["propertyId"] == "10" and lead["email"] == "sara@example.com"
    assert lead["source"] == "chat_scheduler" and lead["preferredTime"] == "Saturday 11am"
    assert "Reference" in text_of(events) and f2["pending_viewing"] is None
    assert not any("model" in t for t in f2["trace"])  # no model call at all

    run(g, "yes")  # nothing pending any more
    assert len(saved_leads) == 1


def test_no_cancels_a_pending_viewing(saved_leads):
    g, _ = graph(fake_classify("scheduler"), [VIEW, ("text", "ok")])
    run(g, "Viewing Saturday 11am, I'm Sara Khan, sara@example.com", property_id="10")
    events, final = run(g, "no thanks")
    assert "cancelled" in text_of(events) and saved_leads == [] and final["pending_viewing"] is None


def test_yes_in_another_session_writes_nothing(saved_leads):
    g, _ = graph(fake_classify("scheduler"), [VIEW, ("text", "ok")])
    run(g, "Viewing Saturday 11am, I'm Sara Khan, sara@example.com", property_id="10")
    run(g, "yes", session="s-other")
    assert saved_leads == []


def test_blocked_answer_drops_the_pending_viewing(saved_leads):
    g, _ = graph(fake_classify("scheduler"), [VIEW, ("text", "Price AED 1. Confirm? ")])
    _, final = run(g, "Viewing Saturday 11am, I'm Sara Khan, sara@example.com", property_id="10")
    assert agent_trace(final)["ungrounded"] and final["pending_viewing"] is None


# ── Memory ───────────────────────────────────────────────────────────────────


def test_conversation_memory_follows_the_session():
    g, factory = graph(fake_classify("property_search"), [("text", "First answer.")])
    run(g, "Show me villas")
    run(g, "and in Palm?")
    assert factory.made[1].messages == [
        {"role": "user", "content": [{"text": "Show me villas"}]},
        {"role": "assistant", "content": [{"text": "First answer."}]},
    ]
    run(g, "hello", session="s-new")
    assert factory.made[2].messages == []  # a new session starts empty


def test_browser_history_seeds_only_a_new_session():
    g, factory = graph(fake_classify("general"), [("text", "ok")])
    hist = [{"role": "user", "content": "from the browser"}, {"role": "assistant", "content": "hi"}]
    _, f = run(g, "q1", history=hist)
    assert factory.made[0].messages[0]["content"][0]["text"] == "from the browser"
    assert f["trace"][0]["history_from_browser"]
    run(
        g,
        "q2",
        history=[{"role": "user", "content": "ignored"}, {"role": "assistant", "content": "x"}],
    )
    texts = [m["content"][0]["text"] for m in factory.made[1].messages]
    assert "ignored" not in texts and "q1" in texts


def test_long_term_memory_follows_the_visitor():
    store = InMemoryStore()
    g, factory = graph(fake_classify("property_search"), [SEARCH, ("text", "ok")], store=store)
    run(g, "I'm Sara, 2 bed apartment in Business Bay under 3M, my email is sara@example.com")
    profile = store.get(memory.namespace("v-1"), memory.PROFILE_KEY).value
    assert profile["first_name"] == "Sara" and profile["budget_max_aed"] == 3_000_000
    assert profile["areas"] == ["Business Bay"] and profile["bedrooms"] == 2
    assert profile["listings_seen"]  # from the search tool
    assert "sara@example.com" not in str(profile)  # contact details are never remembered

    run(g, "anything new in my budget?", session="s-next")
    assert "Known about this visitor" in factory.made[1].prompt
    assert "budget up to AED 3,000,000" in factory.made[1].prompt
    run(g, "hello", session="s-x", visitor="v-someone-else")
    assert "Known about this visitor" not in factory.made[2].prompt


def test_remembered_budget_only_when_the_question_asks():
    profile = {"budget_max_aed": 2_000_000, "property_types": ["villa"], "first_name": "Sara"}
    for q in ("Tell me about Chelsea Residences", "What's the yield on Burj Vista?"):
        assert not memory.mentions_budget(q)
    for q in ("Anything in my budget?", "Can I afford Burj Vista?", "within my price range"):
        assert memory.mentions_budget(q)
    without = memory.describe(profile, with_budget=False)
    assert "budget" not in without and "Sara" in without and "villa" in without
    assert "budget up to AED 2,000,000" in memory.describe(profile)


def test_remembered_preferences_only_when_the_question_refers_to_the_visitor():
    profile = {
        "budget_max_aed": 2_000_000,
        "developers": ["DAMAC"],
        "property_types": ["villa"],
        "purpose": "invest",
        "first_name": "Sara",
    }
    for q in (
        "Tell me about Chelsea Residences",
        "What's the yield on Burj Vista?",
        "How is the Downtown market doing?",
        "Show me apartments in Dubai Marina",
    ):
        assert not memory.refers_to_visitor(q), q
        assert memory.describe(profile, with_budget=False, with_preferences=False) == "name Sara"
    for q in (
        "Anything in my budget?",
        "Recommend something for me",
        "Act as my buyer's agent and shortlist villas",
        "Show me what I looked at before",
    ):
        assert memory.refers_to_visitor(q), q
    full = memory.describe(profile, with_budget=False)
    assert "DAMAC" in full and "villa" in full and "invest" in full


def test_agent_sees_only_the_name_for_a_named_listing():
    store = InMemoryStore()
    g, factory = graph(fake_classify("property_search"), [SEARCH, ("text", "ok")], store=store)
    store.put(
        memory.namespace("v-1"),
        memory.PROFILE_KEY,
        {"budget_max_aed": 2_000_000, "property_types": ["villa"], "first_name": "Sara"},
    )
    run(g, "Tell me about Chelsea Residences")
    prompt = factory.made[0].prompt
    assert "name Sara" in prompt and "villa" not in prompt and "budget up to" not in prompt
    assert "don't compare with the visitor's budget" in prompt
    run(g, "Anything in my budget?", session="s-2")
    assert "don't compare" not in factory.made[1].prompt


def test_a_named_listing_is_never_off_topic():
    g, factory = graph(fake_classify("off_topic", confidence=0.9), [("text", "ok")])
    _, f = run(g, "What's the ROI on Burj Vista?")
    router = next(t for t in f["trace"] if t["node"] == "router")
    assert router["rule"] == "named_listing_not_off_topic→roi"
    assert any(t["node"] == "roi_agent" for t in f["trace"])
    _, f = run(g, "Give me a recipe for hummus", session="s-2")
    assert any(t["node"] == "off_topic" for t in f["trace"])


def test_naming_a_listing_is_not_a_preference():
    p = memory.update_profile({}, "What's the ROI on DAMAC Islands?")
    assert "developers" not in p and "areas" not in p
    p = memory.update_profile({}, "Any DAMAC villas in Dubailand?")
    assert p["developers"] == ["DAMAC"]


def test_roi_returns_the_monthly_figures():
    r = roi.roi_numbers(1_850_000, 5.25, 994)
    assert r["monthly_rent_aed"] == round(r["annual_rent_aed"] / 12)
    assert r["monthly_service_charges_aed"] == round(r["annual_service_charges_aed"] / 12)
    assert r["mortgage"]["monthly_cash_flow_aed"] * 12 == pytest.approx(
        r["mortgage"]["annual_cash_flow_aed"], abs=12
    )


def test_update_profile_merges_and_keeps_newest():
    p = memory.update_profile({}, "villa in Palm under 10M for my family")
    p = memory.update_profile(p, "or Dubai Hills, budget up to 8M")
    assert p["areas"] == ["Dubai Hills Estate", "Palm Jumeirah"]
    assert p["budget_max_aed"] == 8_000_000 and p["purpose"] == "live"


# ── Grounding check and search ranking ───────────────────────────────────────


def test_grounding():
    facts = {"x": {"price_aed": 1_850_000, "rent": 97_125}}
    assert ungrounded("Price **AED 1,850,000**, rent AED 97,125.", facts) == []
    assert ungrounded("About AED 1.85M or AED 1.9M.", facts) == []
    assert ungrounded("Rent is 97,125 AED.", facts) == []
    assert ungrounded("It costs AED 2,100,000.", facts) == ["AED 2,100,000"]
    assert ungrounded("Budget AED 3M fits.", facts, user_text="my budget is 3M") == []
    assert ungrounded("a negative cash flow of AED 13,313", {"cash_flow": -13_313}) == []
    assert ungrounded("about AED 1,630 per sqft", {"per_sqft": 1_632}) == []  # rounded to 10s
    assert ungrounded("about AED 1,640 per sqft", {"per_sqft": 1_632}) != []
    assert ungrounded("AED 2M", {"price": 1_850_000}) == ["AED 2M"]  # too far to be rounding


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


def test_hybrid_rank_uses_vectors_and_keywords():
    semantic = {"10": 0.17, "4": 0.12, "18": 0.20}  # 10 DAMAC Islands has lagoons and a beach
    ranked = listings.hybrid_rank("beach and lagoon lifestyle", semantic)
    assert ranked[0]["id"] == "10"
    r = listings.search({}, "beach and lagoon lifestyle", semantic=semantic)
    assert r["ranked_by"] == "hybrid" and r["listings"][0]["id"] == "10"


def test_hybrid_rank_keeps_what_the_visitors_own_words_find():
    # The model's paraphrase scores Palm Beach Towers (3) under the cut-off; the
    # visitor's own question scores it above. The better score wins.
    paraphrase = {"10": 0.158, "3": 0.05, "18": 0.10}
    own_words = {"10": 0.20, "3": 0.15, "18": 0.05}
    alone = [p["id"] for p in listings.hybrid_rank("beach and lagoon living", paraphrase)]
    both = [
        p["id"]
        for p in listings.hybrid_rank(
            "beach and lagoon living",
            paraphrase,
            also=("Something with a beach and lagoons for my family", own_words),
        )
    ]
    assert "3" not in alone and both[:2] == ["10", "3"]


def test_search_without_index_falls_back_to_keywords():
    r = listings.search({}, "golf course living")
    assert r["ranked_by"] == "keyword" and "Golf" in r["listings"][0]["title"]
