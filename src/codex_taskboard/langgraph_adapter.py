from __future__ import annotations

from typing import Any, TypedDict


PUBLIC_SIGNALS = {"EXECUTION_READY", "WAITING_ON_ASYNC", "CLOSEOUT_READY", "none"}
PUBLIC_PHASES = {"planning", "execution", "closeout"}


class TaskboardGraphState(TypedDict, total=False):
    """Serializable state used by the optional LangGraph adapter."""

    phase: str
    signal: str
    live_task_status: str
    automation_mode: str
    backlog_count: int
    has_live_task: bool
    closeout_done: bool
    successor_required: bool
    next_node: str
    prompt_scene: str
    action: str
    reason: str


def normalize_public_signal(signal: Any) -> str:
    text = str(signal or "").strip()
    return text if text in PUBLIC_SIGNALS else "none"


def normalize_phase(phase: Any, signal: Any = "") -> str:
    text = str(phase or "").strip().lower()
    if text in PUBLIC_PHASES:
        return text
    normalized_signal = normalize_public_signal(signal)
    if normalized_signal == "CLOSEOUT_READY":
        return "closeout"
    if normalized_signal in {"EXECUTION_READY", "WAITING_ON_ASYNC"}:
        return "execution"
    return "planning"


def build_initial_graph_state(
    *,
    phase: str = "planning",
    signal: str = "none",
    live_task_status: str = "none",
    automation_mode: str = "managed",
    backlog_count: int = 0,
    has_live_task: bool = False,
    closeout_done: bool = False,
) -> TaskboardGraphState:
    normalized_signal = normalize_public_signal(signal)
    normalized_phase = normalize_phase(phase, normalized_signal)
    return {
        "phase": normalized_phase,
        "signal": normalized_signal,
        "live_task_status": str(live_task_status or "none").strip() or "none",
        "automation_mode": str(automation_mode or "managed").strip() or "managed",
        "backlog_count": int(backlog_count or 0),
        "has_live_task": bool(has_live_task),
        "closeout_done": bool(closeout_done),
    }


def decide_next_node(state: TaskboardGraphState) -> str:
    signal = normalize_public_signal(state.get("signal", "none"))
    phase = normalize_phase(state.get("phase", ""), signal)
    has_live_task = bool(state.get("has_live_task", False))
    backlog_count = int(state.get("backlog_count", 0) or 0)
    closeout_done = bool(state.get("closeout_done", False))

    if phase == "closeout" and signal == "none" and closeout_done:
        return "successor_bootstrap"
    if signal == "CLOSEOUT_READY" or phase == "closeout":
        return "closeout"
    if signal == "WAITING_ON_ASYNC" or has_live_task:
        return "wait_async"
    if backlog_count > 0:
        return "reflow_batch"
    if signal == "EXECUTION_READY" or phase == "execution":
        return "execution"
    return "planning"


def route_state(state: TaskboardGraphState) -> TaskboardGraphState:
    next_node = decide_next_node(state)
    return {**state, "next_node": next_node}


def planning_node(state: TaskboardGraphState) -> TaskboardGraphState:
    return {
        **state,
        "phase": "planning",
        "prompt_scene": "planning",
        "action": "render_planning_prompt",
        "reason": "proposal/history/handoff should be absorbed before execution",
    }


def execution_node(state: TaskboardGraphState) -> TaskboardGraphState:
    return {
        **state,
        "phase": "execution",
        "prompt_scene": "execution",
        "action": "render_execution_prompt",
        "reason": "agent has local actionable work or an execution-ready packet",
    }


def wait_async_node(state: TaskboardGraphState) -> TaskboardGraphState:
    return {
        **state,
        "phase": "execution",
        "prompt_scene": "resume",
        "action": "monitor_async_task",
        "reason": "live task or WAITING_ON_ASYNC signal requires watchdog/reflow",
    }


def reflow_batch_node(state: TaskboardGraphState) -> TaskboardGraphState:
    return {
        **state,
        "phase": "execution",
        "prompt_scene": "reflow-batch",
        "action": "merge_backlog_receipts",
        "reason": "queued receipts should be merged into one execution context",
    }


def closeout_node(state: TaskboardGraphState) -> TaskboardGraphState:
    return {
        **state,
        "phase": "closeout",
        "prompt_scene": "closeout",
        "action": "render_closeout_prompt",
        "reason": "closeout gate takes priority over stale execution actions",
    }


def successor_bootstrap_node(state: TaskboardGraphState) -> TaskboardGraphState:
    return {
        **state,
        "phase": "planning",
        "successor_required": True,
        "prompt_scene": "successor-bootstrap",
        "action": "bootstrap_successor_session",
        "reason": "closeout finished; predecessor should not be resumed",
    }


def _missing_langgraph_error() -> RuntimeError:
    return RuntimeError(
        "LangGraph is not installed. Install optional dependencies with "
        "`pip install codex-taskboard[langgraph]` or `pip install langgraph langchain-core`."
    )


def build_taskboard_langgraph() -> Any:
    try:
        from langgraph.graph import END, StateGraph
    except Exception as exc:  # pragma: no cover - exercised only without optional deps
        raise _missing_langgraph_error() from exc

    graph = StateGraph(TaskboardGraphState)
    graph.add_node("route", route_state)
    graph.add_node("planning", planning_node)
    graph.add_node("execution", execution_node)
    graph.add_node("wait_async", wait_async_node)
    graph.add_node("reflow_batch", reflow_batch_node)
    graph.add_node("closeout", closeout_node)
    graph.add_node("successor_bootstrap", successor_bootstrap_node)
    graph.set_entry_point("route")
    graph.add_conditional_edges(
        "route",
        lambda state: str(state.get("next_node", "planning")),
        {
            "planning": "planning",
            "execution": "execution",
            "wait_async": "wait_async",
            "reflow_batch": "reflow_batch",
            "closeout": "closeout",
            "successor_bootstrap": "successor_bootstrap",
        },
    )
    for node_name in ("planning", "execution", "wait_async", "reflow_batch", "closeout", "successor_bootstrap"):
        graph.add_edge(node_name, END)
    return graph.compile()


def run_taskboard_langgraph(state: TaskboardGraphState) -> TaskboardGraphState:
    graph = build_taskboard_langgraph()
    return graph.invoke(state)


def graph_mermaid() -> str:
    return "\n".join(
        [
            "flowchart TD",
            "  route{route by phase/signal/backlog/live task}",
            "  route -->|planning/default| planning[planning prompt]",
            "  route -->|EXECUTION_READY| execution[execution prompt]",
            "  route -->|WAITING_ON_ASYNC/live task| wait_async[async watchdog]",
            "  route -->|backlog > 0| reflow_batch[reflow batch prompt]",
            "  route -->|CLOSEOUT_READY/closeout| closeout[closeout transition]",
            "  route -->|closeout none done| successor_bootstrap[successor bootstrap]",
        ]
    )


def snapshot_from_taskboard(
    *,
    phase: str = "",
    signal: str = "",
    live_task_status: str = "none",
    automation_mode: str = "managed",
    backlog_count: int = 0,
    has_live_task: bool = False,
    closeout_done: bool = False,
) -> TaskboardGraphState:
    return build_initial_graph_state(
        phase=phase,
        signal=signal,
        live_task_status=live_task_status,
        automation_mode=automation_mode,
        backlog_count=backlog_count,
        has_live_task=has_live_task,
        closeout_done=closeout_done,
    )

