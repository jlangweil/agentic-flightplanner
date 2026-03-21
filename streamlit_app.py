import streamlit as st
import threading
import queue
import uuid
from app.startup import initialize
from app.agent import dispatcher
from app.state import initial_state
from langgraph.types import Command

st.set_page_config(
    page_title="Smart Dispatcher",
    page_icon="✈",
    layout="wide",
)

initialize()

if "thread_id"       not in st.session_state:
    st.session_state.thread_id = None
if "phase"           not in st.session_state:
    st.session_state.phase = "input"
if "trace_lines"     not in st.session_state:
    st.session_state.trace_lines = []
if "assessment"      not in st.session_state:
    st.session_state.assessment = None
if "briefing"        not in st.session_state:
    st.session_state.briefing = None
if "pilot_decision"  not in st.session_state:
    st.session_state.pilot_decision = None
if "query"           not in st.session_state:
    st.session_state.query = ""

# ── Sidebar debug ──────────────────────────────────────────────────────────
st.sidebar.markdown("### Debug")
st.sidebar.write(f"Phase: `{st.session_state.phase}`")
st.sidebar.write(f"Thread: `{str(st.session_state.thread_id)[:8] if st.session_state.thread_id else 'None'}`")
st.sidebar.write(f"All session state: {dict(st.session_state)}")

# ── Header ─────────────────────────────────────────────────────────────────
st.title("✈ Smart Dispatcher")
st.caption("Agentic pre-flight briefing system")
st.divider()


# ── INPUT ──────────────────────────────────────────────────────────────────
if st.session_state.phase == "input":

    col1, col2 = st.columns([2, 1])

    with col1:
        query = st.text_area(
            "Flight query",
            value=st.session_state.query,
            placeholder=(
                "Should I fly from Morristown to Block Island today? "
                "My Cessna burns 10 GPH, cruises at 120 knots, "
                "and I have 40 gallons on board."
            ),
            height=100,
            key="query_input",
        )

    with col2:
        st.markdown("**Quick fill**")
        if st.button("KMMU → KBID"):
            st.session_state.query = (
                "Should I fly from Morristown (KMMU) to Block Island (KBID) today? "
                "My Cessna burns 10 GPH, cruises at 120 knots, "
                "and I have 40 gallons on board."
            )
            st.rerun()
        if st.button("KMMU → KTEB"):
            st.session_state.query = (
                "Should I fly from Morristown to Teterboro today? "
                "My Cessna burns 10 GPH, cruises at 120 knots, "
                "and I have 40 gallons on board."
            )
            st.rerun()

    st.markdown("**Aircraft parameters**")
    c1, c2, c3, c4 = st.columns(4)
    fuel_onboard = c1.number_input("Fuel onboard (gal)", value=40.0, step=1.0)
    fuel_burn    = c2.number_input("Fuel burn (GPH)",    value=10.0, step=0.5)
    airspeed     = c3.number_input("Cruise speed (kts)", value=120,  step=5)
    is_ifr       = c4.checkbox("IFR flight")

    st.divider()

    run_clicked = st.button(
        "Run Pre-Flight Briefing",
        type="primary",
        use_container_width=True,
    )

    if run_clicked:
        raw_query = st.session_state.get("query_input", "").strip()
        if not raw_query:
            raw_query = st.session_state.query.strip()
        if not raw_query:
            st.error("Please enter a flight query")
        else:
            enriched = raw_query
            if str(int(fuel_onboard)) not in raw_query:
                enriched += (
                    f" I have {fuel_onboard} gallons on board, "
                    f"burn rate {fuel_burn} GPH, cruise {airspeed} knots."
                )
            if is_ifr and "IFR" not in raw_query.upper():
                enriched += " This is an IFR flight."

            st.session_state.query          = enriched
            st.session_state.thread_id      = str(uuid.uuid4())
            st.session_state.phase          = "running"
            st.session_state.trace_lines    = []
            st.session_state.briefing       = None
            st.session_state.assessment     = None
            st.session_state.pilot_decision = None
            st.rerun()


# ── RUNNING ────────────────────────────────────────────────────────────────
elif st.session_state.phase == "running":

    st.info("Agent is running pre-flight checks...")
    trace_placeholder = st.empty()

    output_queue = queue.Queue()

    # Capture in main thread before spawning
    thread_id = st.session_state.thread_id
    query     = st.session_state.query

    def run_agent(thread_id: str, query: str):
        import sys
        import io

        class QueueWriter(io.TextIOBase):
            def write(self, text):
                if text.strip():
                    output_queue.put(text.strip())
                return len(text)
            def flush(self):
                pass

        old_stdout = sys.stdout
        sys.stdout = QueueWriter()
        try:
            config = {"configurable": {"thread_id": thread_id}}
            state  = initial_state(query)
            dispatcher.invoke(state, config=config)
        except Exception as e:
            import traceback
            output_queue.put(f"ERROR: {e}")
            output_queue.put(traceback.format_exc())
        finally:
            sys.stdout = old_stdout
            output_queue.put("__DONE__")

    thread = threading.Thread(
        target=run_agent,
        args=(thread_id, query),
        daemon=True,
    )
    thread.start()

    lines = []
    while True:
        try:
            line = output_queue.get(timeout=0.2)
            if line == "__DONE__":
                break
            lines.append(line)
            trace_placeholder.code("\n".join(lines), language=None)
        except queue.Empty:
            if not thread.is_alive():
                break

    st.session_state.trace_lines = lines

    config      = {"configurable": {"thread_id": thread_id}}
    graph_state = dispatcher.get_state(config)

    st.sidebar.write(f"Next nodes after run: {graph_state.next}")

    if "human_checkpoint" in graph_state.next:
        v = graph_state.values
        parts = [
            f"**Route:** {v.get('departure_icao')} → {v.get('destination_icao')}",
            f"**Verdict:** `{v.get('go_no_go', 'UNKNOWN')}`",
            "",
            "**Risk Assessment:**",
            f"```\n{v.get('risk_assessment', 'N/A')}\n```",
            "",
            "**Fuel Analysis:**",
            f"```\n{v.get('fuel_analysis', 'N/A')}\n```",
            "",
            "**Critic Review:**",
            f"```\n{v.get('critic_feedback', 'N/A')}\n```",
        ]
        if v.get("alternates"):
            parts += [
                "",
                "**Alternates:**",
                f"```\n{v.get('alternates')}\n```",
            ]
        st.session_state.assessment = "\n".join(parts)
        st.session_state.phase      = "awaiting"
    else:
        st.session_state.briefing = graph_state.values.get("briefing")
        st.session_state.phase    = "done"

    st.rerun()


# ── AWAITING PILOT CONFIRMATION ────────────────────────────────────────────
elif st.session_state.phase == "awaiting":

    with st.expander("Agent reasoning trace", expanded=False):
        st.code("\n".join(st.session_state.trace_lines or []), language=None)

    st.divider()
    st.subheader("Assessment Summary")
    st.markdown(st.session_state.assessment or "No assessment available")

    st.divider()
    st.subheader("Your Decision")
    st.markdown(
        "Review the assessment above. "
        "Click **GO** to generate the full briefing, "
        "or **NO-GO** to abort."
    )

    col1, col2, col3 = st.columns([1, 1, 4])
    if col1.button("✅ GO", type="primary", use_container_width=True):
        st.session_state.pilot_decision = "GO"
        st.session_state.phase          = "resuming"
        st.rerun()
    if col2.button("❌ NO-GO", type="secondary", use_container_width=True):
        st.session_state.pilot_decision = "NO-GO"
        st.session_state.phase          = "resuming"
        st.rerun()


# ── RESUMING ───────────────────────────────────────────────────────────────
elif st.session_state.phase == "resuming":

    decision  = st.session_state.pilot_decision
    thread_id = st.session_state.thread_id

    st.info(f"Pilot decision: **{decision}** — generating final briefing...")

    config = {"configurable": {"thread_id": thread_id}}

    try:
        dispatcher.invoke(Command(resume=decision), config=config)
        final_state = dispatcher.get_state(config)

        st.sidebar.write(f"Final next: {final_state.next}")
        st.sidebar.write(f"Briefing None: {final_state.values.get('briefing') is None}")

        briefing = final_state.values.get("briefing")

        if not briefing:
            v = final_state.values
            briefing = (
                f"PRE-FLIGHT BRIEFING\n"
                f"{'='*40}\n"
                f"Route:   {v.get('departure_icao')} → "
                f"{v.get('destination_icao')}\n"
                f"Verdict: {v.get('go_no_go', decision)}\n"
                f"{'='*40}\n\n"
                f"{v.get('risk_assessment', '')}\n\n"
                f"{v.get('fuel_analysis', '')}\n\n"
                f"{v.get('critic_feedback', '')}"
            )

        st.session_state.briefing = briefing
        st.session_state.phase    = "done"

    except Exception as e:
        import traceback
        st.error(f"Error resuming agent: {e}")
        st.error(traceback.format_exc())
        st.session_state.phase = "input"

    st.rerun()


# ── DONE ───────────────────────────────────────────────────────────────────
elif st.session_state.phase == "done":

    decision = st.session_state.get("pilot_decision", "")
    if decision == "GO":
        st.success("✅ GO — Full pre-flight briefing generated")
    elif decision == "NO-GO":
        st.error("❌ NO-GO — Flight aborted by pilot")
    else:
        st.info("Flight assessment complete")

    with st.expander("Agent reasoning trace", expanded=False):
        st.code("\n".join(st.session_state.trace_lines or []), language=None)

    st.divider()
    st.subheader("Pre-Flight Briefing")

    briefing = st.session_state.get("briefing") or ""

    if briefing:
        st.code(briefing, language=None)
        st.download_button(
            label="Download Briefing",
            data=briefing,
            file_name=f"briefing_{str(st.session_state.get('thread_id', 'unknown'))[:8]}.txt",
            mime="text/plain",
        )
    else:
        st.warning("No briefing text available.")

    st.divider()
    if st.button("New Briefing", type="primary"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()