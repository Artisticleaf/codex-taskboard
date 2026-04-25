# codex-taskboard 轻度科研约定

runtime 默认从 `prompts/taskboard_runtime_prompt_zh.toml` 读取自动唤起 prompt 文案；也可以用 `CODEX_TASKBOARD_PROMPT_FILE` 或 `~/.config/codex-taskboard/taskboard_runtime_prompt_zh.toml` 覆盖。

## 状态机

- 外部自动化模式只分两类：`managed` 与 `continuous`。
- 科研阶段只分三类：`planning`、`execution`、`closeout`。
- agent 对 taskboard 的公开信号只保留：`EXECUTION_READY`、`WAITING_ON_ASYNC`、`CLOSEOUT_READY`、`none`。
- `managed` 只托管任务和回流 backlog，不自动再唤起；`continuous` 会循环 `planning -> execution -> closeout`，并在 closeout 完成后强制开启一个新的 Codex session 进入下一轮 planning。
- `protocol-repair` 保留为唯一纠错支线：agent 没有按尾部协议回复时，taskboard 发送极短修复 prompt，而不是重新注入长治理文本。

## 轻度科研约定

1. 先读 `proposal_file`、`project_history_file` 和本轮新增结果，把当前主线、已有边界和最新变化接起来之后再决定下一步；如果问题已经进入新的方法方向，就补读这个方向最关键的旧文献与近年的代表性新工作，再让新设想从我们自己的结果里长出来。
2. 当前上下文里能完成的本机短工作尽量一次做深做完：本机短 CPU/GPU smoke 与正式实验、结果读取、代码和数据审计、必要修复、数据处理、proposal/history 写回、实验准备都尽量在这一轮解决，不要把本来几分钟内能完成的事情人为拆散。
3. 只要结果异常好、异常差、日志异常、关键数字反常，或者和已有 history、文献、官方推荐参数冲突，就先检查实现、数据契约、数据划分、评测污染、配置和运行完整性；没有审清之前，不把它当成可靠科研结论。
4. 本机短 CPU/GPU 实验默认直接在当前上下文终端中运行，结果落盘后立刻处理、分析并写回 proposal；只有确实需要远程资源、长时间等待、跨上下文托管或人工排队时，才提交 taskboard，并在提交前把 smoke、参数、效率、显存和资源占用检查清楚。
5. 没有人工干预时，先比较几条可选路径，再主动执行当前信息增益最高的一步，并说明为什么这样选；凡是能顺手提高结论可信度、减少后续歧义、或者直接推进下一步实验的分析、修复和写回，也尽量一起做掉。

## Taskboard 操作简介

- 当前对话能完成的本机短 CPU/GPU 工作，直接做完；不要为了 signal 或 taskboard 提交把短工作拆成多轮。
- 真正需要 remote、长时间等待、跨上下文托管或独立生命周期时，才用 `codex-taskboard submit`；本地跨回复长任务中，未启动且无需 GPU 的进程可用 `bind-before-launch`，已启动的 CPU/GPU 进程用 `attach-pid` 接管。
- `TASKBOARD_SIGNAL=WAITING_ON_ASYNC` 表示已有 live task 等回流；taskboard 默认按 1 小时节奏提醒对应 agent 回来确认一次，只为确认实验没卡住且仍有日志/结果产出。
- backlog/回流积压可用 dashboard 或 `codex-taskboard backlog` 查看与清理；`dashboard` 也会显示当前 session 的 `automation-mode` 与 backlog 计数。
- closeout / planning 转场前先做 handoff 确认：核对上一轮 proposal、closeout 文档、handoff 文档、`project_history_file` 与 `proposal_path` 绑定，避免错绑；closeout 完成后的回流默认转发到新的 planning session，不主动打断正在工作的 successor session。

## 协议尾部

回复末尾固定输出：

- `TASKBOARD_SIGNAL=EXECUTION_READY|WAITING_ON_ASYNC|CLOSEOUT_READY|none`
- `TASKBOARD_SELF_CHECK=pass|fail`
- `LIVE_TASK_STATUS=none|submitted|awaiting`
