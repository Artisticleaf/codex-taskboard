from __future__ import annotations

from codex_taskboard.langgraph_adapter import (
    decide_next_node,
    graph_mermaid,
    run_taskboard_langgraph,
    snapshot_from_taskboard,
)


def test_route_waiting_on_async_to_resume_scene() -> None:
    state = snapshot_from_taskboard(phase="execution", signal="WAITING_ON_ASYNC", has_live_task=True)

    assert decide_next_node(state) == "wait_async"
    result = run_taskboard_langgraph(state)

    assert result["next_node"] == "wait_async"
    assert result["prompt_scene"] == "resume"
    assert result["action"] == "monitor_async_task"


def test_route_closeout_done_to_successor_bootstrap() -> None:
    state = snapshot_from_taskboard(phase="closeout", signal="none", closeout_done=True)

    assert decide_next_node(state) == "successor_bootstrap"
    result = run_taskboard_langgraph(state)

    assert result["successor_required"] is True
    assert result["prompt_scene"] == "successor-bootstrap"
    assert result["action"] == "bootstrap_successor_session"


def test_route_backlog_to_reflow_batch() -> None:
    state = snapshot_from_taskboard(phase="execution", signal="none", backlog_count=2)

    result = run_taskboard_langgraph(state)

    assert result["next_node"] == "reflow_batch"
    assert result["prompt_scene"] == "reflow-batch"


def test_graph_mermaid_documents_core_nodes() -> None:
    text = graph_mermaid()

    assert "successor_bootstrap" in text
    assert "WAITING_ON_ASYNC" in text
    assert "CLOSEOUT_READY" in text

