"""Build and compile the StateGraph (spec §5): LangGraph orchestrates, Strands agents answer.

    START → load_memory → router ─┬→ property_agent ─┐
                                  ├→ market_agent ───┤
                                  ├→ roi_agent ──────┤
                                  ├→ viewing_agent ──┼→ remember → END
                                  ├→ general_agent ──┤
                                  ├→ confirm_viewing ┤   (no model)
                                  └→ off_topic ──────┘   (no model)

Each *_agent node runs one Strands agent with its tools, streaming through the
output gate (agent/specialists/). Two intents give one agent both toolsets.
Compiled with a checkpointer (conversation memory per chat session) and a
store (long-term memory per visitor): DynamoDB on AWS, in-process otherwise.
"""

from __future__ import annotations

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.base import BaseStore

from . import config, llm, memory
from .nodes import router as router_mod
from .nodes.fixed import confirm_viewing, off_topic
from .nodes.memory_nodes import load_memory, remember
from .specialists import agents
from .state import AgentState

AGENT_NODES = tuple(dict.fromkeys(router_mod.AGENT_NODES.values()))


def build_graph(
    classify=None,
    make_agent=None,
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
):
    """`classify` (router) and `make_agent` (Strands) default to Bedrock; tests pass
    fakes. `checkpointer` / `store` default to agent/memory.py's choice."""
    g = StateGraph(AgentState)
    g.add_node("load_memory", load_memory)
    g.add_node(
        "router",
        partial(
            router_mod.router,
            classify=classify or llm.classify,
            min_confidence=config.router_min_confidence(),
        ),
    )
    for name in AGENT_NODES:
        g.add_node(
            name, partial(agents.run_specialist, make=make_agent or agents.make_agent, node=name)
        )
        g.add_edge(name, "remember")
    g.add_node("confirm_viewing", confirm_viewing)
    g.add_node("off_topic", off_topic)
    g.add_node("remember", remember)

    g.add_edge(START, "load_memory")
    g.add_edge("load_memory", "router")
    g.add_conditional_edges(
        "router", router_mod.next_node, [*AGENT_NODES, "confirm_viewing", "off_topic"]
    )
    g.add_edge("confirm_viewing", "remember")
    g.add_edge("off_topic", "remember")
    g.add_edge("remember", END)
    return g.compile(
        checkpointer=checkpointer if checkpointer is not None else memory.checkpointer(),
        store=store if store is not None else memory.store(),
    )
