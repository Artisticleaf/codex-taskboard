# codex-taskboard 用户手册

## 1. 这是什么

`codex-taskboard` 用来把后台训练、评测、watcher、辅助脚本、子 agent 统一排队，并在任务完成后把结果回传给正确的 Codex 会话。

它适合几类场景：

- 你在当前对话里提交长任务，希望结束后自动回来汇报。
- 你需要多个 GPU/CPU 任务共享同一台机器，避免互相抢资源。
- 你希望把研究过程持续推进，而不是每个阶段都人工盯着。
- 你希望 Docker 或其他用户也能把任务提交到同一个队列里。

### taskboard-light 摘要

默认推荐把它理解成一套“轻提示、强生命周期兜底”的工作板，而不是一套靠超长 prompt 驱动的制度机。

当前后台唤起 prompt 的最小合同是：

- continuous 模式下先给一句固定开场：`在 continuous 模式下，你被设计为一位无需人工干预，也能进行高质量自动科研的 agent。`
- canonical head 只在 `missing_block / missing_keys` 之类真正异常时才提示；`ok`、`read_error`、`unbound` 不再反复刷屏。
- 绑定路径只出现一次：`proposal_file`、`closeout_proposal_dir`、`project_history_file`、`project_history_log_dir`。
- `Taskboard 操作方法` 只保留 `submit`、`bind-before-launch`、`attach-pid`、tmux 托管和等待信号的最小说明。
- 固定“轻度科研约定”就是下面六条，runtime 不再额外塞一整张长版 protocol card：
  1. 若这条消息打断当前工作，把它并入当前计划，continue。如果本prompt中有结果回流，先读取本轮回流、当前 proposal/history 与必要 taskboard 规则，再决定动作。
  2. 默认在当前长上下文完成“本地结果读取、数据处理、可靠性审计、proposal/history 写回、与必要文献对照”；若结果可疑，优先回查代码逻辑、数据契约、数据泄漏、评测污染、split 错位、run 完整性等问题。
  3. 确认可靠的结果先写回 proposal，再结合 history 与关键文献判断其对当前 claim boundary 的影响；只有重要且可信的结果才升级进 history。写作采用正式科研语体，同时把内部缩写、实验代号和结果、结论“用人话”解释清楚并进行分析，不写流水账。
  4. 基于上述材料设计下一步实验，写清设计理由、实现思路、可信性分析、指标区间、决策分支与停止条件；吸收较新顶刊顶会和重要文献灵感，但不要照搬，优先高信息增益、方向性的科研实验，而不是低收益调参，除非你评估需要进一步调参验证模型潜力。
  5. 形成可执行实验包之后，仍然需要先进行严格的代码审计，包括但不限于“代码逻辑、数据契约、数据泄漏、评测污染、split 错位、run 完整性”。所有实验正式发车前还要做smoke test，特别是gpu上的实验默认使用 4 卡规划高吞吐分布式训练——如果不能实现较高 GPU 利用率（显存或计算核心达到90%以上占用率）则阅读训练框架的官方文档、反复尝试进行优化，直到利用率达标或者已尝试所有优化方案，再投入实验。
  6. CPU-only 数据处理/审计/小修复默认在当前对话完成，除非耗时巨大否则不要新开对话，并且充分利用本机的多核多线程cpu提高效率；正式 GPU/remote/async 任务交给 taskboard。通过代码审计和smoke test之后，在当前对话中尽快投入真实实验，不用再新开对话。所有实验建议使用tmux完成，避免网络波动或agent掉线。
- continuous 模式还会追加两句收口/转场约束：
  - 默认继续在当前 proposal 内完成同上下文的数据处理、证据分析与局部改写；当确实出现新的证据对象/经验分支、准备发起 async/GPU/remote 任务、或结论足以改变主线路由并需要独立 handoff/审计封存时，再升级为新 proposal 或阶段 closeout。
  - 因此当你判定当前实验方向进入收口阶段或已无信息增益时，把分析和收口理由写进 history；随后重读项目history、经过上述工作流的循环拟定新 proposal，并转进发布实验阶段，而不要停止我们的科研进程。
- 协议尾注保持不变：`TASKBOARD_PROTOCOL_ACK=TBP1`、`CURRENT_STEP_CLASS`、`TASKBOARD_SELF_CHECK`、`LIVE_TASK_STATUS`、`FINAL_SIGNAL`。
- 防呆主要靠 `pending_feedback`、continuous mode、`migrate-session`、`bind-before-launch` / `attach-pid` 等生命周期能力，而不是继续给 agent 叠更长的提示词。

## 2. 核心概念

### task

每个任务都有 `task_id`、`workdir`、`command`、资源需求、回传方式和状态文件。

### feedback_mode

- `auto`: 任务结束后自动回传到绑定会话。
- `manual`: 保留在 `pending_feedback`，等待后续处理。
- `off`: 不绑定对话，只作为排队执行和结果查询任务。

### proposal

proposal 是实验规划文件。绑定后，taskboard 会在回传 prompt 中提醒 agent：

- 把结果写回 proposal。
- 基于结果继续分析。
- 在同一实验链里继续沿用这个 proposal。

### closeout_proposal_dir

这是 close-out 报告和 proposal 的统一存储目录。建议后续 agent 显式传入：

```bash
--closeout-proposal-dir /home/Awei/LLM/passage/projects/P01_curiosity_grpo/closeout_proposal
```

行为要点：

- taskboard 会在同一 `session + workdir` 内自动继承它。
- prompt 会显式提醒 agent 新的 close-out 和 proposal 都优先写到这个目录。
- prompt 会显式提醒 agent 后续绑定新任务时继续传这个参数。

### continuous research mode

这是“持续推进科研”开关。

- 关闭时，`NO_FURTHER_TASKS` 仍表示自动化在这里停止。
- 开启时，`NO_FURTHER_TASKS` 不再停机，而会触发新的 followup，要求 agent：
  - 先做最小必要的收口与 proposal/history 维护。
  - 在当前上下文继续完成可吸收的本地分析、审计和规划。
  - 确实需要长任务时，再绑定并提交下一轮任务。

### prompt profile

taskboard 给后台消息维护两种 prompt profile：

- `compact`：给已经在同一 live 对话里的后台回传和 followup 使用。当前实际运行时，它已经收束成“异常 canonical head + 单次路径绑定 + Taskboard 操作方法 + 固定轻度科研约定 + 协议尾注”这一套最小合同。
- `full`：给需要完整重述治理规则、即使脱离当前对话也要自解释的 prompt 使用。

为什么不同场景默认发 `compact`：

- `resume` 是“某个后台任务回来了”，重点是让你判断它对当前 proposal 和下一步路由的影响，而不是重新读一遍整份制度。
- 标准 `followup` 只是提醒“当前还没有等价 live task，请继续闭环”，如果过长，反而会把真正要做的动作埋掉。
- continuous followup 可能在长链路里反复出现；如果每次都发 full prompt，很容易把会话推向重复总结、重复等待，甚至形成看起来像“continuous 死机”的空转。
- queued feedback batch 本质上是一批任务共享一次上下文判断，所以现在采用“共享 compact 头 + 分任务块”，避免同一批里每条结果都把长治理 prompt 复制一遍。
- runtime 已不再同时注入 `Taskboard protocol card`、`Taskboard quick memory` 和多段重复执行闭环；最小合同优先保证动作尾部、绑定路径和 tmux/submit 指令不被截断。

什么时候保留 `full`：

- 当系统需要生成完整的权威型治理块。
- 当新入口必须在缺少当前对话上下文的前提下仍能独立成立。
- 当 compact 已无法覆盖必须保留的硬边界时。

## 3. 先确认环境

```bash
cd /home/Awei/codex-taskboard
.venv/bin/codex-taskboard doctor
```

如果你正在 Codex 会话里，推荐先看当前线程上下文：

```bash
.venv/bin/codex-taskboard current-thread
```

这比手工扫 `list-threads` 更可靠。当前版本会优先信任插件/CLI 调用环境里真实的 `CODEX_SESSION_ID` 或 `CODEX_THREAD_ID`，只有环境里没有会话上下文时，才退回 taskboard 的 workdir 推断。

## 4. 常用命令

### 看队列

```bash
.venv/bin/codex-taskboard dashboard
```

plain 模式单次输出：

```bash
.venv/bin/codex-taskboard dashboard --render-mode plain --once
```

### 切换连续科研模式

```bash
.venv/bin/codex-taskboard continuous-mode status
.venv/bin/codex-taskboard continuous-mode on --session-id <codex_session_id>
.venv/bin/codex-taskboard continuous-mode off --session-id <codex_session_id>
.venv/bin/codex-taskboard continuous-mode toggle --session-id <codex_session_id>
.venv/bin/codex-taskboard continuous-mode bind --session-id <new_codex_session_id>
.venv/bin/codex-taskboard continuous-mode clear-session --session-id <old_codex_session_id>
```

如果你用的是 JSON 输出，建议至少看这些字段：

- `effective_wait_state`：当前 session 真实等待态，例如 `WAITING_ON_ASYNC`、`WAITING_ON_FEEDBACK`、`PARKED_IDLE`
- `automation_recommendation`：taskboard 当前建议的动作，例如 `continue_local_microstep`、`wait_for_live_task`、`absorb_completed_receipt`、`materialize_successor_proposal`、`finish_proposal_dispatch`、`wait_for_external_evidence`、`dispatch_parked_watchdog`
- `parked_watchdog_due`：如果当前处于 `PARKED_IDLE`，这里会告诉你 parked watchdog 是否已经到期；首次 parked 还会先挂一次短延迟 recheck
- `parked_wait_age_seconds` / `parked_watchdog_interval_seconds`：当前 parked 已持续多久，以及 watchdog 的提醒窗口有多长
- `proposal_bootstrap_ready` / `proposal_bootstrap_reason`：最近时间日志里的 `Next bounded action` 是否已经进入 successor bootstrap 语义，例如“新 family 设计 / proposal 骨架 / route replanning / 最小 pilot gate”
- `proposal_dispatch_ready`：当前 session 的最新机器可读信号是否已经变成 `MATERIALS_READY_FOR_PROPOSAL`
- `running_task_count`：当前 session 口径下真实仍在运行或排队中的 live task 数
- `proposal_bound_running_task_count`：当前 proposal 路径下真实仍在运行或排队中的 live task 数
- `awaiting_feedback_task_count`：已完成/失败但尚未被当前 session 吸收的回流任务数
- `human_guidance_active`：是否仍处于人工暂停态
- 兼容字段：`live_task_count` / `proposal_bound_live_task_count` / `pending_feedback_live_task_count` 仍会返回旧名字，但新脚本应迁移到 `running_*` / `awaiting_feedback_*`

判断“自动科研是否可继续推进”时，不要只看 `enabled=true`，还要一起看上面这组字段。
如果看到 `automation_recommendation=continue_local_microstep`，说明 controller 认为最近时间日志里仍有可继承的 bounded local next step；此时应优先在同一上下文吃干这批本地证据，而不是先新建 proposal 或 async task。
如果看到 `automation_recommendation=materialize_successor_proposal`，说明 controller 认为 parked 已经不该继续空等，最近的 `Next bounded action` 已明确转成 successor bootstrap；此时应优先把新 family / 新 proposal 的 CPU-only 材料落盘。
如果看到 `automation_recommendation=finish_proposal_dispatch`，说明 session 已明确返回 `MATERIALS_READY_FOR_PROPOSAL`；此时下一步不是再 parked，而是完成 proposal/history 写回并至少提交一条 live task。
如果看到 `effective_wait_state=WAITING_ON_FEEDBACK`，说明计算已经结束，但 receipt 仍待当前 session 吸收；这不是 live compute 仍在运行。
如果看到 `effective_wait_state=PARKED_IDLE`，不要默认它会永久 parked：taskboard 会先安排一次短延迟自检回唤；若 `parked_watchdog_due=true`，则说明更长窗口也已经到期，需要再次提醒 agent 做一次 bounded local self-review。

### 提交一个自动回传任务

```bash
.venv/bin/codex-taskboard submit \
  --task-id smoke-auto \
  --workdir /home/Awei/project \
  --command 'python run.py' \
  --feedback-mode auto
```

如果同一 `codex_session_id + proposal_path + command` 已经存在 `queued/submitted/running/watching` 任务，taskboard 会直接拒绝这次提交并返回 `Duplicate submit guard`。默认动作应该是先用 `codex-taskboard status --json` 核对 authoritative task，再检查 proposal/history；只有在确认需要保留重复任务时，才显式追加 `--allow-duplicate-submit`。

### 提交一个 agentless job

```bash
.venv/bin/codex-taskboard submit-job \
  --task-id smoke-job \
  --workdir /home/Awei/project \
  --command 'python train.py' \
  --gpu-slots 4 \
  --feedback-mode off
```

### 绑定 proposal

```bash
.venv/bin/codex-taskboard submit-job \
  --task-id qwen-main \
  --workdir /home/Awei/LLM/passage/projects/P01_curiosity_grpo \
  --command 'python train.py' \
  --proposal /home/Awei/LLM/passage/projects/P01_curiosity_grpo/experiments/PLAN.md \
  --closeout-proposal-dir /home/Awei/LLM/passage/projects/P01_curiosity_grpo/closeout_proposal \
  --feedback-mode auto
```

如果你明确不想继承上一个 proposal：

```bash
.venv/bin/codex-taskboard submit-job \
  --task-id clean-helper \
  --workdir /home/Awei/LLM/passage/projects/P01_curiosity_grpo \
  --command 'python helper.py' \
  --no-inherit-proposal \
  --feedback-mode off
```

### 绑定一个现有 PID

```bash
.venv/bin/codex-taskboard attach-pid \
  --pid 12345 \
  --workdir /home/Awei/project \
  --task-id external-train \
  --feedback-mode auto
```

`bind-before-launch` / `attach-pid` 同样遵守 duplicate-submit guard；如果你已经在 tmux 里手工启动了一个与当前主链完全同构的实验，先核对 live 状态，再决定是否真的要追加 `--allow-duplicate-submit` 去强行接管第二份副本。

## 5. Dashboard 快捷键

在 `codex-taskboard dashboard` 中：

- `q`: 退出
- `SPACE`: 暂停刷新
- `j` / `k`: 上下移动
- `PgUp` / `PgDn`: 翻页
- `g` / `G`: 顶部 / 底部
- `s`: 切换排序
- `f`: 切换过滤
- `[` / `]`: 切换 agent 过滤
- `p`: 切换 process panel
- `+` / `-`: 调整 priority
- `c`: 切换 continuous research mode

界面头部会显示当前模式，例如 `[continuous on]` 或 `[continuous off]`。

## 6. Proposal 继承规则

proposal 继承只依赖当前真实上下文，不建议人工猜。

规则如下：

- 显式传 `--proposal` 时，以显式值为准。
- 显式传 `--closeout-proposal-dir` 时，以显式值为准。
- 显式传 `--no-inherit-proposal` 时，会清空 proposal 继承。
- 否则 taskboard 会优先从环境或历史中，在相同 `session + workdir` 里自动继承。
- 同一实验主链建议一直沿用同一个 proposal 文件。
- 同一实验主链也建议一直沿用同一个 `closeout_proposal_dir`。
- 辅助任务也可以绑定同一 proposal，但通常属于 `sidecar`，负责把局部结果并回主 proposal。
- close-out 报告与 proposal 建议统一放在 `closeout_proposal_dir` 中，避免项目链路散落在多个目录。

## 7. 文档写作约束

taskboard 对 close-out 和 proposal 的 prompt 现在会明确要求：

- 使用中文书写。
- 用语尽量面向论文写作、正式技术说明和后续论文撰写，而不是只写内部 shorthand。
- 新的 close-out 文档和新的 proposal 优先写入已绑定的 `closeout_proposal_dir`。

## 8. 信号规则

### 同一认知线程继续推进

- `LOCAL_CONTINUE_NO_WAKE`: agent 仍在同一认知线程内推进；taskboard 不应额外创建 local reminder，也不应累计 `repeated_local_action_without_new_evidence`。
- `LOCAL_MICROSTEP_BATCH`: agent 明确请求 taskboard 在短延迟后再次外部重入当前会话；它不是停机，而是“请稍后再从外部叫醒我一次”。

### 等待态拆分

- `WAITING_ON_ASYNC`: 已提交或确认了等价 async / live task，当前只是等待生命周期自动回流。
- `WAITING_ON_FEEDBACK`: 新的 continuous/status 等待态，表示计算已结束，但 receipt 尚待当前 session 吸收；completed/failed + `pending_feedback/manual` 不再被误报成 live。
- `PARKED_IDLE`: 当前 proposal 已 parked、没有新 evidence、没有 running live task，且 canonical head 未变化。

### 连续模式关闭

- `NO_FURTHER_TASKS`、`STOP_AUTOMATION`、`END_EXPERIMENT`: 停止自动推进。

### 连续模式开启

- `NO_FURTHER_TASKS`、`STOP_AUTOMATION`、`END_EXPERIMENT`: 都不会直接停机，而会进入收口转场 override。
- 系统会发出新的转场 prompt，要求先完成 proposal/history 收口、按新工作流拟定新 proposal，并至少提交一条受托管实验。
- 只有新 proposal 已形成、且至少一条下一阶段 live task 已进入 taskboard 生命周期后，才应返回 `NEW_TASKS_STARTED`。

## 9. 推荐工作流

### 日常科研主链

1. 在当前 Codex 对话中确认 `current-thread`。
2. 提交主训练任务，并显式绑定 proposal。
3. 同时显式传入 `--closeout-proposal-dir /home/Awei/LLM/passage/projects/P01_curiosity_grpo/closeout_proposal`。
4. 提交评测、watcher、数据整理等 sidecar 任务。
5. 开启 continuous mode。
6. 在 dashboard 里观察队列和 `pending_feedback`。

### 单纯排队执行

1. 使用 `submit-job --feedback-mode off`。
2. 用 `status`、`status-result` 或 API 获取结果。
3. 不要求绑定 agent。

## 10. 常见误区

- 不要再通过 `list-threads` 的最近更新时间来猜当前 session。
- 不要把 `NO_FURTHER_TASKS` 当成连续模式下的全局停机信号。
- 不要为不同具体数据集直接堆一套独立参数表；如果要做特化，应优先按任务类型、评价指标、难度层级或 failure family 设计。
- 不要让辅助任务各自维护一份 proposal；主 proposal 应尽量唯一。
- 不要让 close-out 文档和 proposal 分散在多个目录里；同一实验链建议共享同一个 `closeout_proposal_dir`。
- 不要直接用 `python src/codex_taskboard/cli.py submit` 提交即时 sidecar；优先使用已安装的 `codex-taskboard submit`。在 tmux / 多解释器环境下，前者可能让短任务出现 `stale_state:supervisor_missing`，从而看起来像是 continuous 没接住。

## 11. 常见排查

### `current-thread` 失败

说明当前 shell 没拿到 `CODEX_SESSION_ID` 或 `CODEX_THREAD_ID`。这时不要手工猜 session，优先回到正确的 Codex 调用上下文。

### 任务不自动回传

先检查：

- `feedback_mode` 是否是 `auto` 或 `manual`
- `codex_session_id` 是否存在
- 会话是否繁忙，任务是否被保留在 `pending_feedback`
- dashboard 中是否显示 `pending_feedback`

补充说明：

- 现在 followup 调度优先依赖 `codex_session_id`，不再要求必须有非空 `agent_name`。
- 如果旧任务曾显示 `pending_feedback=true`、`followup_status=scheduled`，但磁盘上没有对应 followup 实体，dispatcher 会自动尝试恢复这条 queued-feedback 回传。
- `followup.log` 现在会与对应 reminder JSON 同步记录 `scheduled/deferred/suppressed/resumed`、`reason`、`effective_wait_state`、`running_task_count`、`awaiting_feedback_task_count`；如果看到 reminder JSON 在更新而 `followup.log` 没有对应事件，应视为观测层异常。
- 对更早的历史任务，必要时可以执行：

```bash
.venv/bin/codex-taskboard followup-reconcile
```

### `status --json` / `status-result` 里新增的运行态字段怎么看

新版本除了 `status` 本身，还会返回更细的运行态字段：

- `lifecycle_state`
- `runtime_state`
- `dispatch_diagnostics`
- `platform_recovery`
- `automation_recommendation`

建议解释顺序：

1. 先看 `status` / `lifecycle_state`
2. 再看 `runtime_state`
3. 如果是卡住、重试或平台异常，再看 `dispatch_diagnostics` 和 `platform_recovery`

特别是：

- 若 `platform_recovery.state != none`，应优先按平台级瞬时错误处理，而不是直接判成任务逻辑失败。
- 自动科研工作流里，这类状态更接近“等待平台恢复”，不应污染 proposal/history 的 scientific readout。

### 明明已经说 `NO_FURTHER_TASKS`，为什么还继续

因为 continuous mode 开着。用下面命令确认：

```bash
.venv/bin/codex-taskboard continuous-mode status
```
