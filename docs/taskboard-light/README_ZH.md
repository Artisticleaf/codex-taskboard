# taskboard-light

这是面向科研 agent 的最小口径说明。

## 模式与阶段

- 自动化模式：`managed` / `continuous`
- 科研阶段：`planning` / `execution` / `closeout`
- 公开 signal：`EXECUTION_READY` / `WAITING_ON_ASYNC` / `CLOSEOUT_READY` / `none`

## 约束

- 当前对话能完成的 CPU-only 工作，直接做完；不要为了 signal 把短工作拆成多轮。
- execution 用统一上下文处理：receipt 吸收、代码/数据审计、局部修复、proposal/history 写回、实验包准备与提交。
- 只有真正需要 GPU、remote、长等待或独立生命周期时，才提交 taskboard 任务。
- closeout 只有在 agent 已经明确写出“继续扩展当前 proposal 没有新的信息收益”的分析后才成立。
- closeout 后必须：总结 proposal、回写 history、写 handoff、做 binding 确认，然后由 taskboard 进入下一轮 planning。

## 任务与回流

- `managed`：只托管任务和积压回流，不自动再唤起。
- `continuous`：自动把回流并入当前 session，并在 `planning -> execution -> closeout -> planning` 之间循环。
- `WAITING_ON_ASYNC`：已有 live task 等回流；taskboard 默认每 1 小时提醒一次，仅用于确认任务没卡住。
- backlog 可用 `codex-taskboard backlog` 或 dashboard 查看和清理。

## 固定尾部协议

- `TASKBOARD_SIGNAL=EXECUTION_READY|WAITING_ON_ASYNC|CLOSEOUT_READY|none`
- `TASKBOARD_SELF_CHECK=pass|fail`
- `LIVE_TASK_STATUS=none|submitted|awaiting`
