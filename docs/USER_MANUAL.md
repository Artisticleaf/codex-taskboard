# codex-taskboard User Manual

## 核心概念

- 自动化模式：`managed` / `continuous`
- 科研阶段：`planning` / `execution` / `closeout`
- 公开 signal：`EXECUTION_READY` / `WAITING_ON_ASYNC` / `CLOSEOUT_READY` / `none`

## 常用命令

```bash
.venv/bin/codex-taskboard automation-mode status --session-id <codex_session_id>
.venv/bin/codex-taskboard automation-mode managed --session-id <codex_session_id>
.venv/bin/codex-taskboard automation-mode continuous --session-id <codex_session_id>
.venv/bin/codex-taskboard enter-stage planning --proposal /path/to/PROPOSAL.md --project-history-file /path/to/HISTORY.md
.venv/bin/codex-taskboard enter-stage execution --session-id <codex_session_id>
.venv/bin/codex-taskboard enter-stage closeout --session-id <codex_session_id> --handoff-file /path/to/HANDOFF.md
.venv/bin/codex-taskboard backlog status --session-id <codex_session_id>
.venv/bin/codex-taskboard backlog show --session-id <codex_session_id>
.venv/bin/codex-taskboard backlog clear --session-id <codex_session_id>
.venv/bin/codex-taskboard api-url
.venv/bin/codex-taskboard prompt-preview --scene planning
.venv/bin/codex-taskboard prompt-preview --scene execution
.venv/bin/codex-taskboard prompt-preview --scene closeout
```

## 行为说明

- `managed`
  - 只托管任务与积压回流，不自动再次唤起 agent。
- `continuous`
  - 自动把回流并入同一 session，并推动 `planning -> execution -> closeout -> planning`。
- `WAITING_ON_ASYNC`
  - 表示已有 live task 等回流；taskboard 默认每 1 小时提醒一次，用于确认任务没卡住。
- backlog
  - 表示已经到盘但暂未重新并入 session 的回流积压；可在 dashboard 或 `backlog` 命令中查看和清理。

## prompt 场景

- `planning`
  - 读 history / handoff / 文献，刷新 proposal，准备首个执行包。
- `execution`
  - 在统一上下文内做 receipt 吸收、审计、修复、writeback、提交实验。
  - execution 需要收束到两个出口之一：真实实验提交，或进入 closeout；不要无限拆成更小的下一步。
- `closeout`
  - 证明剩余本地动作已不足以改变结论边界、关键风险判断或实验就绪度后，进行总结、history 回写、handoff 与绑定确认。
- `protocol-repair`
  - 当 agent 忘记固定尾部协议时发送的极短纠错 prompt。

## 固定尾部协议

- `TASKBOARD_SIGNAL=EXECUTION_READY|WAITING_ON_ASYNC|CLOSEOUT_READY|none`
- `TASKBOARD_SELF_CHECK=pass|fail`
- `LIVE_TASK_STATUS=none|submitted|awaiting`
