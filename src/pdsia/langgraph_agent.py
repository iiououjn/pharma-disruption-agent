"""LangGraph wiring of the same node functions used by `agent.run_briefing`.

NOT RUN in the build sandbox (langgraph not installed there). The node logic itself is exercised by
the unit tests through `run_briefing`; only the graph wiring below is untested.

    pip install langgraph
    from pdsia.langgraph_agent import run_graph
"""
from __future__ import annotations

from typing import Any, TypedDict

from .agent import NODES
from .tools import ToolBox


class State(TypedDict, total=False):
    tb: Any
    snapshot_id: Any
    prev_snapshot_id: Any
    snap_meta: Any
    prev_meta: Any
    narrator: Any
    events: Any
    records: Any
    matches: Any
    prev_matches: Any
    by_sku: Any
    exposures: Any
    items: Any
    briefing: Any


def build_graph():
    from langgraph.graph import END, StateGraph   # imported lazily so the package works without it

    g = StateGraph(State)
    names = [n for n, _ in NODES]
    for name, fn in NODES:
        g.add_node(name, fn)
    g.set_entry_point(names[0])
    for a, b in zip(names, names[1:]):
        g.add_edge(a, b)
    g.add_edge(names[-1], END)
    return g.compile()


def run_graph(conn, snapshot_id=None, prev_snapshot_id=None, narrator=None) -> dict:
    out = build_graph().invoke({"tb": ToolBox(conn), "snapshot_id": snapshot_id,
                                "prev_snapshot_id": prev_snapshot_id, "narrator": narrator})
    return out["briefing"]
