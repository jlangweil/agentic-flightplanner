import os
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from app.state import BriefingState
from app.nodes import (
    planner_node,
    analyzer_node,
    find_alternates_node,
    no_go_briefing_node,
    critic_node,
    human_checkpoint_node,
    final_briefing_node,
)
from app.nodes.routing import route_after_analyzer, route_after_alternates
from app.config import settings
import sqlite3


def build_graph():
    db_path = (
        settings.database_url
        .replace("sqlite:///", "")
        .replace("./", "")
    )
    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)

    graph = StateGraph(BriefingState)

    graph.add_node("planner",           planner_node)
    graph.add_node("analyzer",          analyzer_node)
    graph.add_node("find_alternates",   find_alternates_node)
    graph.add_node("no_go_briefing",    no_go_briefing_node)
    graph.add_node("critic",            critic_node)
    graph.add_node("human_checkpoint",  human_checkpoint_node)
    graph.add_node("final_briefing",    final_briefing_node)

    graph.set_entry_point("planner")
    graph.add_edge("planner",          "analyzer")

    graph.add_conditional_edges(
        "analyzer",
        route_after_analyzer,
        {
            "find_alternates": "find_alternates",
            "no_go_briefing":  "no_go_briefing",
            "critic":          "critic",
        }
    )

    graph.add_conditional_edges(
        "find_alternates",
        route_after_alternates,
        {
            "no_go_briefing": "no_go_briefing",
            "critic":         "critic",
        }
    )

    graph.add_edge("critic",           "human_checkpoint")
    graph.add_edge("human_checkpoint", "final_briefing")
    graph.add_edge("no_go_briefing",   END)
    graph.add_edge("final_briefing",   END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["human_checkpoint"],
    )


dispatcher = build_graph()

print(f"  [Agent] Compiled graph interrupt_before: {dispatcher.config_specs}")

