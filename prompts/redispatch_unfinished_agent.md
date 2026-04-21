# 未完成任务重分发（轻量版）

只处理你自己之前启动、但现在还没有被 taskboard 正确接管的任务。不要接管其他 agent 的任务。

目标只有三件事：

1. 找到你自己负责、但还没完成或还没绑定的长任务。
2. 把这些任务重新放回 `codex-taskboard` 的正常生命周期。
3. 避免重复提交已经有活跃版本的同类任务。

最小规则：

- 不要继续手工裸跑长任务。
- 本地会跨回复继续运行的 CPU-only 进程：优先 `bind-before-launch`；已经启动的进程：`attach-pid`。
- 子代理任务：用 `submit-subagent`。
- 逻辑上同一个任务复用 `task_key`，但新的提交使用新的 `task_id`。
- 如果 taskboard 里已经有活跃版本，不要再重复创建。
- 如果是 429、会话中断或 taskboard 误操作导致回流断开，目标是把同一 proposal/history 绑定恢复回来，而不是重写一套新计划。

建议顺序：

```bash
codex-taskboard dashboard --once
codex-taskboard status --json
codex-taskboard ps-training --show-cwd
```

然后按情况处理：

- 已有外部 PID：`attach-pid`
- 尚未启动但需要托管：`bind-before-launch` 或 `submit --hold`
- Codex 子代理：`submit-subagent`

重分发完成后，请汇报：

- 哪些任务已经重新接管
- 每个任务的 `task_id` / `task_key` / `execution_mode` / `status`
- 对应的 proposal/history 绑定

如果确认当前没有你负责的未完成任务，请明确回复这一点。
