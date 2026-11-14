"""Run the Bedrock evals and apply the gates in spec §10.

    .venv/bin/python -m evals               # routing + grounding (+ cost from the same runs)
    .venv/bin/python -m evals routing
    .venv/bin/python -m evals grounding -v  # print every answer

Listings come from data/mock_properties.py and lead writes go to a list, so the
only AWS calls are Bedrock model calls (about 80; well under $0.10).
Results are saved to evals/results/<suite>-<time>.json (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from agent import config, llm
from agent.graph import build_graph
from agent.nodes.router import router
from agent.stream import stream_events
from agent.tools import leads

from .cases import GROUNDING, ROUTING, Case

RESULTS = Path(__file__).parent / "results"
ROUTING_GATE = 0.90
CALLS_GATE = 0.95  # share of questions answered with ≤ 2 model calls
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
    ok = any(i in case.expect for i in out["intents"])
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


async def _answer(case: Case, graph) -> dict[str, Any]:
    final: dict[str, Any] = {}
    kinds = []
    t0 = time.perf_counter()
    async for ev in stream_events(
        {"prompt": case.prompt, "property_id": case.property_id, "history": case.history},
        graph=graph,
        on_final=final.update,
    ):
        kinds.append(ev["event_type"])
    trace = final.get("trace", [])
    writer = next((t for t in trace if t.get("node") == "writer"), {})
    return {
        "prompt": case.prompt,
        "property_id": case.property_id,
        "intents": final.get("intents"),
        "answer": final.get("answer", ""),
        "suggestions": final.get("suggestions", []),
        "ungrounded": writer.get("ungrounded", []),
        "model_calls": sum(1 for t in trace if "model" in t),
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
            print(f"\n── {r['prompt']}  [{'+'.join(r['intents'] or [])}]")
            print(r["answer"])
            if r["ungrounded"]:
                print(f"  !! ungrounded: {r['ungrounded']}")
    grounded = not bad
    print(
        f"grounding: {len(rows) - len(bad)}/{len(rows)} answers use only AED figures from facts "
        f"→ {'PASS' if grounded else 'FAIL'}"
    )

    within = sum(r["model_calls"] <= 2 for r in rows) / len(rows)
    cost_ok = within >= CALLS_GATE
    n = len(rows)
    avg = {
        k: sum(r[k] for r in rows) / n for k in ("tokens_in", "tokens_out", "cost_usd", "seconds")
    }
    print(
        f"cost: {within:.0%} of questions used ≤ 2 model calls (gate {CALLS_GATE:.0%}) → "
        f"{'PASS' if cost_ok else 'FAIL'}; avg {avg['tokens_in']:.0f} in / {avg['tokens_out']:.0f} "
        f"out tokens, ${avg['cost_usd']:.4f} per question, {avg['seconds']:.1f}s"
    )
    print(f"viewing requests captured (not written to AWS): {len(saved)}")
    return grounded and cost_ok, {"within_two_calls": within, "averages": avg, "rows": rows}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("suite", nargs="?", choices=["all", "routing", "grounding"], default="all")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args()
    print(
        f"router={config.router_model_id()}  writer={config.writer_model_id()}  "
        f"listings={config.listings_source()}"
    )
    RESULTS.mkdir(exist_ok=True)
    ok = True
    for name, fn in (("routing", run_routing), ("grounding", run_grounding)):
        if a.suite in ("all", name):
            passed, report = fn(a.verbose)
            ok &= passed
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            (RESULTS / f"{name}-{stamp}.json").write_text(json.dumps(report, indent=1, default=str))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
