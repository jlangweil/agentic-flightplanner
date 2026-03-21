import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from app.agent import dispatcher
import uuid

config = {"configurable": {"thread_id": str(uuid.uuid4())}}

print("--- First invoke ---")
from app.state import initial_state
state = initial_state("Fly from KMMU to KBID, 40 gal, 10 GPH, 120 kts")
result = dispatcher.invoke(state, config=config)

graph_state = dispatcher.get_state(config)
print(f"Next nodes: {graph_state.next}")
print(f"go_no_go: {graph_state.values.get('go_no_go')}")
print(f"briefing is None: {graph_state.values.get('briefing') is None}")