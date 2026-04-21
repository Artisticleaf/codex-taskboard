# taskboard-light

这不是一套新调度器，而是现有 `codex-taskboard` 的轻量使用口径：少改状态机，主要收缩 prompt 和操作心智负担。

## 运行时最小合同

当前后台唤起 prompt 不再叠加长版 protocol card、quick memory 和重复执行闭环。默认只保留下面几块：

- continuous 模式开头句：`在 continuous 模式下，你被设计为一位无需人工干预，也能进行高质量自动科研的 agent。`
- 仅在 canonical head 异常时才提示 `missing_block / missing_keys`；`ok`、`read_error`、`unbound` 不再反复刷屏。
- 单次出现的绑定路径：`proposal_file`、`closeout_proposal_dir`、`project_history_file`、`project_history_log_dir`。
- 置顶的“轻度科研约定”与单次出现的写回/转场要求：可靠结果先写 proposal，重要且可信的结果再升级进 history；默认在当前 proposal 内完成同上下文 writeback、分析与收口转场，并用人话讲清 benchmark、比较对象、变化趋势和科学含义。
- `Taskboard 操作方法`：只告诉 agent 何时继续本地 CPU-only、何时 `submit`、何时 `bind-before-launch` / `attach-pid`，以及如何配合 tmux。
- 固定的“轻度科研约定”六条；其中第 3 条已经吸收原先重复的写回要求与 continuous 收口/转场约束。
- 固定协议尾注：`TASKBOARD_PROTOCOL_ACK`、`CURRENT_STEP_CLASS`、`TASKBOARD_SELF_CHECK`、`LIVE_TASK_STATUS`、`FINAL_SIGNAL`。

## 固定“轻度科研约定”

在 continuous 模式下，你被设计为一位无需人工干预，也能进行高质量自动科研的 agent。

1. 若这条消息打断当前工作，把它并入当前计划，continue。如果本prompt中有结果回流，先读取本轮回流、当前 proposal/history 与必要 taskboard 规则，再决定动作。
2. 默认在当前长上下文完成“本地结果读取、数据处理、可靠性审计、proposal/history 写回、与必要文献对照”；若结果可疑，优先回查代码逻辑、数据契约、数据泄漏、评测污染、split 错位、run 完整性等问题。
3. 写回与转场要求：确认可靠的结果先写回 proposal，再结合 history 与关键文献判断其对当前 claim boundary 的影响；只有重要且可信的结果才升级进 history。默认继续在当前 proposal 内完成同上下文的数据处理、证据分析与局部改写；当确实出现新的证据对象/经验分支、准备发起 async/GPU/remote 任务、或结论足以改变主线路由并需要独立 handoff/审计封存时，再升级为新 proposal 或阶段 closeout。当你判定当前实验方向进入收口阶段或已无信息增益时，把关键分析和收口理由写进 history；随后重读项目 history、经过上述工作流循环拟定新 proposal，并转进发布实验阶段，而不要停止科研进程。将结果撰写回所有文档时：1、不能写成流水账，要挑重点；2、要用完整表述讲清楚本阶段具体的实现方式，不能只是用项目缩写；3、要说人话，写清结果对应哪个 benchmark、比较对象是谁、变化趋势如何、具体的科学含义是什么，以及它会怎样影响后续实验方向。
4. 基于上述材料设计下一步实验，写清设计理由、实现思路、可信性分析、指标区间、决策分支与停止条件；吸收较新顶刊顶会和重要文献灵感，但不要照搬，优先高信息增益、方向性的科研实验，而不是低收益调参，除非你评估需要进一步调参验证模型潜力。
5. 形成可执行实验包之后，仍然需要先进行严格的代码审计，包括但不限于“代码逻辑、数据契约、数据泄漏、评测污染、split 错位、run 完整性”。所有实验正式发车前还要做smoke test，特别是gpu上的实验默认使用 4 卡规划高吞吐分布式训练——如果不能实现较高 GPU 利用率（显存或计算核心达到90%以上占用率）则阅读训练框架的官方文档、反复尝试进行优化，直到利用率达标或者已尝试所有优化方案，再投入实验。
6. CPU-only 数据处理/审计/小修复默认在当前对话完成，除非耗时巨大否则不要新开对话，并且充分利用本机的多核多线程cpu提高效率；正式 GPU/remote/async 任务交给 taskboard。通过代码审计和smoke test之后，在当前对话中尽快投入真实实验，不用再新开对话。所有实验建议使用tmux完成，避免网络波动或agent掉线。

## Taskboard 操作方法

- 同上下文 CPU-only 数据处理/审计/小修复：直接在当前对话完成；若只是继续当前认知线程且不需要 taskboard 再次外部唤起，输出 `TASKBOARD_SIGNAL=LOCAL_CONTINUE_NO_WAKE`；若需要 taskboard 在短延迟后再次外部重入当前会话，再输出 `TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH`。
- 正式 GPU/remote/async 实验：使用 `codex-taskboard submit`。
- 本地会跨回复继续运行的长任务：未启动先 `codex-taskboard bind-before-launch`，已启动再 `codex-taskboard attach-pid`。
- taskboard 与 tmux 兼容：`submit` / `bind-before-launch` 默认以 tmux session 托管；如果你已在 tmux 中手动启动实验，可用 `attach-pid` 接管。
- 如果同一 `codex_session_id + proposal_path + command` 已经有 `queued/submitted/running/watching` 任务，taskboard 默认拒绝重复提交；只有在核对 authoritative 任务之后，才显式使用 `--allow-duplicate-submit`（API 为 `allow_duplicate_submit=true`）。
- 已提交长任务且当前只是等待时，输出 `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`；无新证据且无本地动作时，再退到 `TASKBOARD_SIGNAL=PARKED_IDLE`。
- 在 continuous 模式下，只有完成 proposal/history 更新并至少启动一条受托管实验后，才输出 `TASKBOARD_SIGNAL=NEW_TASKS_STARTED`。

## 为什么它更“防呆”

轻量版不靠更长的 prompt，而是靠现有生命周期能力兜底：

- `pending_feedback` / `queued feedback`
  - 会话忙、429、最近有活动时，结果先缓存，稍后再回流。
- `parked watchdog`
  - 第一次 `PARKED_IDLE` 后，先做一轮 bounded self-review；只有这轮仍无增益时，才进入更长的 backoff。
- `continuous mode`
  - agent 中断后还能继续按同一 proposal/history 唤起，而不是要求重开一套上下文。
- `bind-before-launch` / `attach-pid`
  - 本地长任务先绑定；tmux session 可以稳定承接掉线、重连和长时间运行。
- `human-guidance`
  - 人工接管时后台回流只排队，不打断当前指导。
- `migrate-session`
  - Codex 会话坏掉时，把 followup、回流和绑定整体迁到新 session。

## 推荐信号

- `LOCAL_CONTINUE_NO_WAKE`
- `LOCAL_MICROSTEP_BATCH`
- `WAITING_ON_ASYNC`
- `PARKED_IDLE`
- `MATERIALS_READY_FOR_PROPOSAL`
- `NEW_TASKS_STARTED`

补充说明：`WAITING_ON_FEEDBACK` 现在是 status/observability wait state，不是 agent 需要输出的 `FINAL_SIGNAL`。

原则只有一句：

- 能现在做完并现在吸收结果的准备性动作，就留在当前对话里做。
- 会跨回复继续跑、或值得独立 receipt 留痕的实验，就交给 taskboard。
- 正式实验优先使用 tmux 托管，不要裸跑后再补绑定。
