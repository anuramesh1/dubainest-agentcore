"""Run the Bedrock evals and apply the gates in spec §10.

    .venv/bin/python -m evals               # routing + grounding (+ cost) + memory
    .venv/bin/python -m evals routing
    .venv/bin/python -m evals grounding -v  # print every answer
    .venv/bin/python -m evals memory -v

Listings come from data/mock_properties.py, memory is in-process and lead writes
go to a list, so the only AWS calls are Bedrock model calls (about 130; a few
cents in total).
Results are saved to evals/results/<suite>-<time>.json (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from agent import config, llm
from agent.graph import build_graph
from agent.nodes.router import router
from agent.stream import stream_events
from agent.tools import leads

from .cases import GROUNDING, MEMORY, ROUTING, Case

RESULTS = Path(__file__).parent / "results"
ROUTING_GATE = 0.90
CALLS_GATE = 0.95  # share of questions answered within MAX_CALLS model calls
MAX_CALLS = 4  # router 1 + Strands agent 2-3 (spec §10)
# us-east-1 on-demand, per 1M tokens (spec §8): (input, output)
PRICES = {"nova-micro": (0.035, 0.14), "haiku-4-5": (1.0, 5.0), "sonnet": (3.0, 15.0)}
CONCURRENCY = 4


def _price(model: str) -> tuple[float, float]:
    return next((v for k, v in PRICES.items() if k in model), (1.0, 5.0))


def _cost(trace: list[dict[str, Any]]) -> float:
    total = 0.0
    for t in trace:
        if "model" in t:
            pin, pout = _price(t["model"])
            total += t.get("input_tokens", 0) * pin / 1e6 + t.get("output_tokens", 0) * pout / 1e6
    return total


def _model_calls(trace: list[dict[str, Any]]) -> int:
    # A Strands agent's entry counts every call of its loop; the router's counts one.
    return sum(t.get("model_calls", 1) for t in trace if "model" in t)


def _agent(trace: list[dict[str, Any]]) -> dict[str, Any]:
    return next((t for t in trace if t.get("framework") == "strands"), {})


async def _bounded(items, fn):
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(x):
        async with sem:
            return await fn(x)

    return await asyncio.gather(*(one(x) for x in items))


# ── routing ───────────────────────────────────────────────────────────────────


async def _route(case: Case) -> dict[str, Any]:
    state = {"prompt": case.prompt, "property_id": case.property_id, "history": case.history}
    out = await router(state, classify=llm.classify, min_confidence=config.router_min_confidence())
    ok = any(i in case.expect for i in out["intents"])  # type: ignore[operator]
    return {
        "prompt": case.prompt,
        "expect": case.expect,
        "got": out["intents"],
        "ok": ok,
        "trace": out["trace"],
    }


def run_routing(verbose: bool) -> tuple[bool, dict[str, Any]]:
    rows = asyncio.run(_bounded(ROUTING, _route))
    acc = sum(r["ok"] for r in rows) / len(rows)
    for r in rows:
        if verbose or not r["ok"]:
            mark = "ok  " if r["ok"] else "MISS"
            print(
                f"  {mark} {r['prompt'][:60]:60} expect={'/'.join(r['expect'])} got={'+'.join(r['got'])}"
            )
    passed = acc >= ROUTING_GATE
    print(
        f"routing: {acc:.0%} correct on {len(rows)} prompts (gate {ROUTING_GATE:.0%}) → "
        f"{'PASS' if passed else 'FAIL'}"
    )
    return passed, {"accuracy": acc, "rows": rows}


# ── grounding + cost ──────────────────────────────────────────────────────────


async def _answer(
    case: Case, graph, session: str | None = None, visitor: str | None = None
) -> dict[str, Any]:
    final: dict[str, Any] = {}
    kinds = []
    t0 = time.perf_counter()
    async for ev in stream_events(
        {
            "prompt": case.prompt,
            "property_id": case.property_id,
            "history": case.history,
            "session_id": session or f"eval-{uuid.uuid4()}",
            "visitor_id": visitor or f"eval-{uuid.uuid4()}",
        },
        graph=graph,
        on_final=final.update,
    ):
        kinds.append(ev["event_type"])
    trace = final.get("trace", [])
    agent = _agent(trace)
    return {
        "prompt": case.prompt,
        "property_id": case.property_id,
        "intents": final.get("intents"),
        "route": final.get("route"),
        "tools": agent.get("tools", []),
        "answer": final.get("answer", ""),
        "suggestions": final.get("suggestions", []),
        "ungrounded": agent.get("ungrounded", []),
        "narration_dropped": agent.get("narration_dropped", 0),
        "facts": final.get("facts", {}),
        "pending_viewing": final.get("pending_viewing"),
        "profile": final.get("profile", {}),
        "model_calls": _model_calls(trace),
        "tokens_in": sum(t.get("input_tokens", 0) for t in trace),
        "tokens_out": sum(t.get("output_tokens", 0) for t in trace),
        "cost_usd": _cost(trace),
        "seconds": round(time.perf_counter() - t0, 1),
        "events_ok": kinds[0] == "status" and kinds[-1] == "done" and "error" not in kinds,
        "trace": trace,
    }


def run_grounding(verbose: bool) -> tuple[bool, dict[str, Any]]:
    saved: list[dict] = []
    leads.WRITER = saved.append  # never write to the real Leads table
    graph = build_graph()
    rows = asyncio.run(_bounded(GROUNDING, lambda c: _answer(c, graph)))

    bad = [r for r in rows if r["ungrounded"] or not r["events_ok"]]
    for r in rows:
        if verbose or r in bad:
            print(f"\n── {r['prompt']}  [{r['route']}: {', '.join(r['tools'])}]")
            print(r["answer"])
            if r["ungrounded"]:
                print(f"  !! ungrounded: {r['ungrounded']}")
    grounded = not bad
    print(
        f"grounding: {len(rows) - len(bad)}/{len(rows)} answers use only AED figures from tool "
        f"results (output gate blocked {sum(bool(r['ungrounded']) for r in rows)}) "
        f"→ {'PASS' if grounded else 'FAIL'}"
    )

    within = sum(r["model_calls"] <= MAX_CALLS for r in rows) / len(rows)
    cost_ok = within >= CALLS_GATE
    n = len(rows)
    avg = {
        k: sum(r[k] for r in rows) / n for k in ("tokens_in", "tokens_out", "cost_usd", "seconds")
    }
    print(
        f"cost: {within:.0%} of questions used ≤ {MAX_CALLS} model calls (gate {CALLS_GATE:.0%}) → "
        f"{'PASS' if cost_ok else 'FAIL'}; avg {avg['tokens_in']:.0f} in / {avg['tokens_out']:.0f} "
        f"out tokens, ${avg['cost_usd']:.4f} per question, {avg['seconds']:.1f}s"
    )
    print(f"viewing requests captured (not written to AWS): {len(saved)}")
    calls = sorted(r["model_calls"] for r in rows)
    print(f"model calls per question: min {calls[0]}, median {calls[n // 2]}, max {calls[-1]}")
    return grounded and cost_ok, {"within_max_calls": within, "averages": avg, "rows": rows}


# ── memory ────────────────────────────────────────────────────────────────────


def run_memory(verbose: bool) -> tuple[bool, dict[str, Any]]:
    """One visitor, two chat sessions, no browser history: conversation memory carries
    follow-ups and the viewing request; long-term memory carries the budget."""
    saved: list[dict] = []
    leads.WRITER = saved.append
    graph = build_graph()
    visitor = f"eval-visitor-{uuid.uuid4().hex[:8]}"
    sessions = {"A": f"eval-{uuid.uuid4()}", "B": f"eval-{uuid.uuid4()}"}

    async def conversation() -> list[dict[str, Any]]:
        out = []
        for step in MEMORY:
            r = await _answer(Case(step["prompt"]), graph, sessions[step["session"]], visitor)
            r["check"] = step["check"]
            r["leads_saved"] = len(saved)
            out.append(r)
        return out

    rows = asyncio.run(conversation())
    ok_all = True
    for r in rows:
        ok, why = r["check"](r)
        r["ok"], r["why"] = ok, why
        ok_all &= ok and not r["ungrounded"] and r["events_ok"]
        mark = "ok  " if ok and not r["ungrounded"] else "FAIL"
        print(f"  {mark} {r['prompt'][:58]:58} [{r['route']}] {why}")
        if verbose:
            print("       " + r["answer"].replace("\n", "\n       ")[:600])
    print(
        f"memory: {sum(r['ok'] for r in rows)}/{len(rows)} steps → {'PASS' if ok_all else 'FAIL'}"
    )
    for r in rows:
        r.pop("check")
    return ok_all, {"rows": rows}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "suite", nargs="?", choices=["all", "routing", "grounding", "memory"], default="all"
    )
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    print(
        f"router={config.router_model_id()}  agents={config.writer_model_id()} (Strands)  "
        f"listings={config.listings_source()}"
    )
    RESULTS.mkdir(exist_ok=True)
    ok = True
    suites = (("routing", run_routing), ("grounding", run_grounding), ("memory", run_memory))
    for name, fn in suites:
        if a.suite in ("all", name):
            passed, report = fn(a.verbose)
            ok &= passed
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            (RESULTS / f"{name}-{stamp}.json").write_text(json.dumps(report, indent=1, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
