"""Build and compile the StateGraph (spec §5).

    START → router ─┬→ property_search ─┐
                    ├→ market_intel ────┤
                    ├→ roi ─────────────┼→ writer → END
                    ├→ scheduler ───────┤
                    ├→ general ─────────┘
                    └→ off_topic → END

The router may return two specialists; LangGraph runs them in parallel and the
writer runs once after both. No checkpointer: each call is stateless; the
browser sends the history (spec §7).
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from . import config, llm
from .nodes import router as router_mod
from .nodes import specialists
from .nodes.writer import off_topic, writer
from .state import AgentState

SPECIALIST_NODES = {
    "property_search": specialists.property_search,
    "market_intel": specialists.market_intel,
    "roi": specialists.roi,
    "scheduler": specialists.scheduler,
    "general": specialists.general,
}


def build_graph(classify=None, stream_text=None):
    """`classify` / `stream_text` default to Bedrock (agent/llm.py); tests pass fakes."""
    g = StateGraph(AgentState)
    g.add_node(
        "router",
        partial(
            router_mod.router,
            classify=classify or llm.classify,
            min_confidence=config.router_min_confidence(),
        ),
    )
    for name, fn in SPECIALIST_NODES.items():
        g.add_node(name, fn)
        g.add_edge(name, "writer")
    g.add_node("writer", partial(writer, stream_text=stream_text or llm.stream_text))
    g.add_node("off_topic", off_topic)

    g.add_edge(START, "router")
    g.add_conditional_edges("router", router_mod.next_nodes, [*SPECIALIST_NODES, "off_topic"])
    g.add_edge("writer", END)
    g.add_edge("off_topic", END)
    return g.compile()
