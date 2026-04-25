# 2026-04-25 continuous closeout / active session 修复记录

## 背景

- 本机 continuous session 在 closeout 完成并输出 `TASKBOARD_SIGNAL=none` 后，没有切到 successor/new Codex session，而是继续用旧 `continuous_session_reminder` 唤醒同一个旧 session。
- 本机不使用 taskboard 托管短实验时，dashboard 缺少独立 session 可视化，用户需要手工查 session id 才能执行 managed / continuous 管理。

## 修复

- `CLOSEOUT_READY` 进入强 closeout transition：continuous reminder / followup 收到 closeout signal 后会调度 `continuous_research_closeout_transition`，并在 session/task state 中标记 `research_phase=closeout`。
- closeout `none` 触发 successor bootstrap：transition、误留在 reminder 上的 closeout `none`、以及 protocol repair 中带 closeout phase 的 `none` 都会调用 `bootstrap_successor_session_after_closeout()`。
- closeout 状态优先于 next bounded action：dispatcher 在确保 continuous reminder 时，如果 session 已处于 closeout/pending transition，会调度 closeout transition，而不是按 stale next action 发 execution prompt。
- successor 切换后重置 session phase：bootstrap 完成 cutover 后，会按 successor 的 signal 写回新 session 的 planning/execution/closeout 状态，避免把 predecessor 的 closeout phase 迁移成新 session 的默认阶段。
- successor bootstrap 兼容本机 Codex interactive duplicate：如果 `codex exec` 先记录了一个无 assistant 输出的临时 thread、随后真正完成的 thread 才写入 rollout，taskboard 会按同一 prompt 与启动时间回扫最近 thread，优先采用带 assistant/protocol footer 的 successor，即使 tmux 被关闭导致 returncode 为 `1` 也不会误判失败。
- dashboard 增加 `Active Sessions` 小栏：展示 automation/followup/task/current-thread 合并得到的 session id、managed/continuous 状态、phase、last signal、followup type、next resume 和 cwd。
- protocol footer 解析支持显式 `effective_research_phase=closeout`，用于识别 closeout repair / closeout none 的边界场景。

## 覆盖的 edge cases

- `continuous_session_reminder -> CLOSEOUT_READY` 会转成 `continuous_research_closeout_transition`。
- transition 已缓存 closeout `none` 时，不再 resume predecessor，直接 bootstrap successor。
- reminder 误收到 closeout `none` 时，仍 bootstrap successor，不清空绑定。
- protocol repair 中回复 `effective_research_phase=closeout` + `TASKBOARD_SIGNAL=none` 时，仍 bootstrap successor。
- 普通 execution reminder 回复 `TASKBOARD_SIGNAL=none` 且没有 closeout 证据时，仍按 terminal none 清理绑定。
- session state 已是 closeout / `last_signal=CLOSEOUT_READY` 时，next bounded action 不会把旧 session 拉回 execution。
- local interactive 先命中空 duplicate thread、真正 successor thread 稍后完成时，会跳过空 thread 并恢复到带 assistant message 的新 thread。
- successor bootstrap 已拿到 `EXECUTION_READY` assistant message 但 interactive exec returncode 非 0 时，只要 session id 是新 session 且 protocol footer 有效，仍会完成 cutover。
- dashboard 在没有 task 条目的情况下仍能显示 active/bound session id。

## 验证

- `tests/test_followups.py`
- `tests/test_dashboard.py`
- `tests/test_automation_state.py`
- `tests/test_session_migration.py`
- 全量：`.venv/bin/python -m pytest -q`，结果 `193 passed`。

## 本地上线记录

- 分支：`fix/continuous-closeout-active-sessions-20260425`。
- 安装：`.venv/bin/python -m pip install -e .`。
- 服务：`codex-taskboard-api.service` 与 `codex-taskboard-dispatcher.service` 均已重新拉起并处于 `active/running`。
- 健康检查：`.venv/bin/codex-taskboard service doctor` 返回 `healthy=true`。
- 可视化检查：`.venv/bin/codex-taskboard dashboard --once --render-mode plain --limit 5` 已显示 `Active Sessions` 栏。
