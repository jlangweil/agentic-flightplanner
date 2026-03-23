import psycopg
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.postgres import PostgresSaver
from app.config import settings
from app.state import BriefingState
from app.nodes import (
    planner_node,
    find_alternates_node,
    no_go_briefing_node,
    critic_node,
    human_checkpoint_node,
    final_briefing_node,
)
from app.nodes.routing import route_after_analyzer, route_after_alternates


def build_graph():
    conn = psycopg.connect(settings.database_url, autocommit=True)
    checkpointer = PostgresSaver(conn)
    checkpointer.setup()   # creates checkpoint tables if they don't exist

    graph = StateGraph(BriefingState)

    graph.add_node("planner",           planner_node)
    if settings.use_react_analyzer:
        from app.nodes.analyzer_react import analyzer_react_node as analyzer
    else:
        from app.nodes.analyzer import analyzer_node as analyzer
    graph.add_node("analyzer",          analyzer)
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
