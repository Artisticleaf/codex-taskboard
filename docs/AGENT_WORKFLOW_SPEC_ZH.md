# codex-taskboard Agent 工作流规范

## 0. taskboard-light 摘要

如果只记最小集合，请直接记 runtime 里那套固定合同：

在 continuous 模式下，你被设计为一位无需人工干预，也能进行高质量自动科研的 agent。

1. 若这条消息打断当前工作，把它并入当前计划，continue。如果本prompt中有结果回流，先读取本轮回流、当前 proposal/history 与必要 taskboard 规则，再决定动作。
2. 默认在当前长上下文完成“本地结果读取、数据处理、可靠性审计、proposal/history 写回、与必要文献对照”；若结果可疑，优先回查代码逻辑、数据契约、数据泄漏、评测污染、split 错位、run 完整性等问题。
3. 确认可靠的结果先写回 proposal，再结合 history 与关键文献判断其对当前 claim boundary 的影响；只有重要且可信的结果才升级进 history。写作采用正式科研语体，同时把内部缩写、实验代号和结果、结论“用人话”解释清楚并进行分析，不写流水账。
4. 基于上述材料设计下一步实验，写清设计理由、实现思路、可信性分析、指标区间、决策分支与停止条件；吸收较新顶刊顶会和重要文献灵感，但不要照搬，优先高信息增益、方向性的科研实验，而不是低收益调参，除非你评估需要进一步调参验证模型潜力。
5. 形成可执行实验包之后，仍然需要先进行严格的代码审计，包括但不限于“代码逻辑、数据契约、数据泄漏、评测污染、split 错位、run 完整性”。所有实验正式发车前还要做smoke test，特别是gpu上的实验默认使用 4 卡规划高吞吐分布式训练——如果不能实现较高 GPU 利用率（显存或计算核心达到90%以上占用率）则阅读训练框架的官方文档、反复尝试进行优化，直到利用率达标或者已尝试所有优化方案，再投入实验。
6. CPU-only 数据处理/审计/小修复默认在当前对话完成，除非耗时巨大否则不要新开对话，并且充分利用本机的多核多线程cpu提高效率；正式 GPU/remote/async 任务交给 taskboard。通过代码审计和smoke test之后，在当前对话中尽快投入真实实验，不用再新开对话。所有实验建议使用tmux完成，避免网络波动或agent掉线。

continuous 模式补充两句收口/转场约束：

- 默认继续在当前 proposal 内完成同上下文的数据处理、证据分析与局部改写；当确实出现新的证据对象/经验分支、准备发起 async/GPU/remote 任务、或结论足以改变主线路由并需要独立 handoff/审计封存时，再升级为新 proposal 或阶段 closeout。
- 因此当你判定当前实验方向进入收口阶段或已无信息增益时，把分析和收口理由写进 history；随后重读项目history、经过上述工作流的循环拟定新 proposal，并转进发布实验阶段，而不要停止我们的科研进程。

防呆能力的核心不是“多加 prompt”，而是保证 agent 异常中断后还能沿同一 proposal/history 继续：

- `pending_feedback` / `queued feedback` 负责缓存回流；
- `continuous mode` 负责继续唤起；
- `bind-before-launch` / `attach-pid` 负责托管本地长任务；
- `human-guidance` / `migrate-session` 负责人工接管和 session 切换。

### 0.1 后台 prompt 的最小合同

当前 runtime 不再叠加长版 `Taskboard protocol card`、`Taskboard quick memory` 和多段重复执行闭环。默认只保留：

- continuous 模式开头句；
- 仅在异常时显示的 canonical head 提示；
- 单次出现的 `proposal_file / closeout_proposal_dir / project_history_file / project_history_log_dir`；
- 单次出现的写回要求；
- 单次出现的 `Taskboard 操作方法`，明确 `submit`、`bind-before-launch`、`attach-pid` 和 tmux；
- 上面的固定“轻度科研约定”六条，以及 continuous 模式追加的两句收口/转场约束；
- 固定协议尾注：`TASKBOARD_PROTOCOL_ACK=TBP1`、`CURRENT_STEP_CLASS`、`TASKBOARD_SELF_CHECK`、`LIVE_TASK_STATUS`、`FINAL_SIGNAL`。

## 1. 目标

这份规范要解决的不是“如何多分发任务”，而是“如何把真正需要生命周期管理或 receipt 留痕的动作交给 taskboard，把不需要生命周期管理的动作留在当前对话里完成”。

新的权威原则不是“理论或实验二选一”，而是：

- 准备性短步骤绑定当前对话上下文。
- 一旦理论工作已经形成可执行实验，就优先把它转成真实验证，并绑定 taskboard 生命周期。

这里的“绑定 taskboard 生命周期”不是抽象说法，而是指这件事必须依赖未来某个时刻的自动回流：例如 GPU 训练结束后要回传、远程执行结束后要回传、长时间评测完成后要回传、或你必须等待外部条件变化后再继续。

如果当前动作只是准备性微步骤，不需要这种“未来再回来”的机制，它就不应该先被包装成 live task。
但如果当前已经形成一个最小真实实验、即使它本身不算很长，也应优先留下 taskboard receipt 与生命周期管理，而不是继续停留在纯理论循环。

## 2. 为什么旧流程容易变成流水线

旧问题的根源不是 agent 不会执行，而是工作流没有给 agent 一个稳定、可执行的中间状态表达。

旧口径里，agent 经常同时受到三种压力：

- prompt 强调“闭环结束前最好看到新的 live task”；
- followup 缺少“我正在继续本地短步骤”的显式状态；
- followup 也缺少“我已经提了长任务，现在只是等回流”的显式状态。

结果就是，agent 很容易把所有“下一步”都过度物化成 taskboard 任务，哪怕它其实只是：

- 再看一段日志；
- 改一小段脚本；
- 写 proposal/history；
- 跑一个 CPU-only smoke；
- 把多个短步骤连着做完。

这样会把本来可以在单轮上下文里自然完成的工作，拆成低价值的流水线，增加制度性开销。

## 3. 唤起后的固定顺序

每次 agent 被 taskboard 唤起时，都先按同一顺序走，不要靠上一轮惯性继续写：

1. 先重读已绑定 `project_history_file / proposal_file` 顶部 canonical head，再看本轮回流。
   taskboard 只会在 canonical head 异常时于 prompt 中补充 `proposal_head / history_head` 的状态、缺失键和 head hash；若头部正常，agent 仍应自己先回读文档顶部小块。
2. 先判断当前是不是还在同一 `same work package`。
3. 再决定当前动作属于 `inline_now`、`inline_batch`、`bind_before_launch` 还是 `async_task`。
4. 如果人工正在直接 steer 当前 session，先进入人工暂停 lane，让 taskboard 只排队不打断。
5. 回复末尾做一次 taskboard 自检，再结束当前轮。

这样做的目的不是增加制度，而是防止 agent 在长上下文里忘记主线，或者因为一个局部问题继续钻牛角尖。

这里的 canonical head 不是“随手写几句摘要”，而是文档顶部一个很小的机器锚点。推荐直接放在文件最前面：

```text
<!-- TASKBOARD_CANONICAL_HEAD_BEGIN CH1 role=proposal -->
BIG_MAINLINE=contrastive_retention_guardrail_reweight_followthrough
SMALL_MAINLINE=qwen3-8b realization-gap repair
CURRENT_BOUNDARY=当前只允许 repaired-owner 层级的 reviewer-facing claim
NEXT_STEP=先完成 CPU-only 审计，再决定是否进入下一条 GPU 路由
KEY_EVIDENCE=/abs/path/to/summary.json ; /abs/path/to/report.md
MILESTONE=realization-gap confirmed on qwen3-8b, canonical mainline unchanged
<!-- TASKBOARD_CANONICAL_HEAD_END -->
```

最少必填键是：

- `BIG_MAINLINE`
- `SMALL_MAINLINE`
- `CURRENT_BOUNDARY`
- `NEXT_STEP`

可选补充：

- `KEY_EVIDENCE`
- `MILESTONE`

taskboard 会检查这块头部是否存在、字段是否齐、版本是否可识别；当它发现 `missing_block / missing_keys` 这类异常时，才会把状态直接写进后台 prompt。正常 `ok` 状态不会每轮反复刷屏。这样即使文档被压缩，agent 每轮重读时仍然读到的是稳定结构，而不是另一段会继续漂移的散文。

## 4. 四类动作

### 4.1 `inline_now`

这是最小单位的本地动作。它同时满足下面几个条件：

- 预计 60 秒内可完成；
- 不需要 GPU；
- 只依赖当前已经落盘的本地工件；
- 做完后能立刻吸收结果；
- 不需要 future callback，不需要等 taskboard 稍后再回来。

典型例子：

- 阅读刚生成的日志尾部并解释原因；
- 改一个小 bug；
- 补一段文档或 proposal；
- 本地跑一次 CPU-only 小脚本；
- 对比两个结果文件并写结论。

处理方式：

- 直接在当前回复里完成。
- 不要为了这件事再新建 taskboard live task。

### 4.2 `inline_batch`

这是“连续几步本地短动作”的状态，不是一类更长的任务。

它的本质是：

- 你仍然留在当前对话上下文里工作；
- 下一批动作依旧属于 CPU-only、本地、短耗时、可立即吸收的微步骤；
- 但需要明确区分“仍在当前认知线程内继续推进”和“需要 taskboard 稍后再从外部叫醒我一次”。

典型例子：

- 先读日志，再改脚本，再复跑 smoke，再写 proposal；
- 先核对 benchmark，再读一段实现，再补 history，再决定是否值得上 GPU；
- 先整理 closeout，再扫 queue，再写 claim boundary。

处理方式：

- 这些动作仍然不应拆成多个 live task。
- 如果当前回复结束后仍在同一认知线程内继续推进、且不需要 scheduler 额外创建 reminder，输出：

```text
TASKBOARD_SIGNAL=LOCAL_CONTINUE_NO_WAKE
```

- 如果你明确需要 taskboard 在短延迟后再次外部重入当前会话，再输出：

```text
TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH
```

这表示：

- `LOCAL_CONTINUE_NO_WAKE` = “我还在同一认知线程里继续推进，不要额外创建 reminder，也不要把我算成 parked 候选”；
- `LOCAL_MICROSTEP_BATCH` = “请短延迟后再从外部叫醒我一次”；
- 二者都不是“忘了分发任务”。

### 4.3 `bind_before_launch`

这是一个经常被忽略、但非常重要的中间类型。

它通常是：

- 本地 CPU-only；
- 看起来不像 GPU 大任务；
- 但一旦启动，可能跨多个回复继续运行；
- 或者如果当前 agent / 网络 / 上下文异常中断，就会丢生命周期。

典型例子：

- 一个可能跑几分钟、而且你不确定当前对话会不会稳定撑完的本地评测脚本；
- 一个会持续写日志、后续还要回来检查结果的 CPU-only 审计脚本；
- 已经在本机启动、但应该被 taskboard 托管后续回流的 PID。

处理方式：

- 不要裸跑后再想起绑定。
- 要么先用 `codex-taskboard bind-before-launch` 以 CPU-only 方式提交。
- 要么先 `attach-pid`，再让 taskboard 接管后续生命周期。

### 4.4 `async_task`

这是唯一应该被物化成 taskboard live task 的动作类型。

它具有下列任一特征：

- 需要 GPU；
- 需要多卡；
- 需要远程执行；
- 运行时长明显超过本地短步骤；
- 需要等待外部事件、外部资源或未来 callback；
- 你现在无法在当前回复内吸收结果，必须让系统稍后自动回流。

典型例子：

- 正式训练；
- 多卡 benchmark；
- 长时间推理/评测；
- 远程 executor 上的运行；
- attach 外部 PID 后等待其结束。

处理方式：

- 如果当前已经形成真实验证价值，不管它是长任务还是短实验，只要当前没有等价 live task，就提交真实 task。
- 如果已经提交或确认存在等价 live task，而当前轮只是等待回流，则输出：

```text
TASKBOARD_SIGNAL=WAITING_ON_ASYNC
```

这表示：

- “该长任务已经进入 taskboard 生命周期”；
- “当前不是缺任务，而是在等结果”；
- “不要继续高频催我再新建一条等价任务”。

## 5. 选择规则

判断下一动作时，只问四个问题：

1. 它需要 GPU、远程执行或明显超过本地短步骤时长吗？
2. 它做完之后，结果能在当前回复里立即被我吸收吗？
3. 它是否依赖未来 callback、外部等待或稍后回流？
4. 它是否只是当前上下文里连着做的 2-4 个本地短步骤之一？
5. 它虽然是本地 CPU-only，但会不会跨多个回复继续运行，或者一旦中断就丢生命周期？

决策表：

- 如果 1 否、2 是、3 否、4 否：`inline_now`
- 如果 1 否、2 是、3 否、4 是：`inline_batch`
- 如果 1 否、3 否，但 5 是：`bind_before_launch`
- 只要 1 是或 3 是：`async_task`

简化成“说人话”的标准：

- 能现在做完并现在解释清楚的，就现在做。
- 会跨回复继续跑、而且不能接受“跑到一半 agent 崩了却没绑定”的，就先绑定。
- 必须以后再回来接的，才交给 taskboard。
- 如果最近时间日志已经把唯一下一跳明确写成 `launch spec / runner spec / config materialization / proposal 骨架` 这类本地工件，本轮默认直接把工件物化出来；不要只把动作名重复一遍再返回 `LOCAL_CONTINUE_NO_WAKE` / `LOCAL_MICROSTEP_BATCH`。
- 如果最近时间日志已经把当前状态推进到“CPU-only 材料已齐、下一步只差显式绑定/提交 live task”，不要把这种 dispatch-ready 状态误判成 `conflict`；应转成 `MATERIALS_READY_FOR_PROPOSAL` / dispatch followup。

## 6. 新信号的精确定义

### 6.1 `TASKBOARD_SIGNAL=LOCAL_CONTINUE_NO_WAKE`

含义：

- 当前轮结束时，agent 仍在同一认知线程内推进；
- 不需要 taskboard 额外创建 local reminder；
- 不应累计 `repeated_local_action_without_new_evidence`；
- 不应因为“没有新外部证据”而诱导 `parked/idle`。

系统行为：

- taskboard 不创建额外 reminder；
- continuous controller 会把它当作 inline continue，而不是停机或 parked 候选；
- followup / feedback 路径不会把这类 signal 计入 local fastpath park counter。

不要在这些情况下使用它：

- 你其实需要 taskboard 稍后再从外部重入当前会话；
- 你已经进入 async/live-task 等待态；
- 你其实已经没有 bounded local next step。

### 6.2 `TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH`

含义：

- 当前轮结束时，agent 明确请求 taskboard 在短延迟后再次外部重入当前会话。

系统行为：

- taskboard 会给当前会话安排一个短延迟 followup/reminder；
- 这个 signal 严格表示“external re-wake me later”，不是“我还在 inline continue 且无需 scheduler 介入”；
- 它也不是停机按钮。

不要在这些情况下使用它：

- 你只是继续同一认知线程内的 inline 推进；
- 你真正需要的是 GPU/remote/长任务；
- 你只是想拖延判断。

### 6.3 `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`

含义：

- 我已经提交或确认了等价 async / live task；
- 当前轮只是等待这个任务的生命周期自动回流。

系统行为：

- 如果 taskboard 已看到更新的等价 live task，就停止当前通用 followup，不再反复催促；
- 如果还没看到更新的 async task，会改成较慢 watchdog，而不是高频追问；
- 会把当前 session 标记为 `awaiting_async`。

不要在这些情况下使用它：

- 你还没有真正提交或确认等价 async / live task；
- 你只是猜测“稍后也许会有人去跑”；
- 你其实还能继续做本地短步骤。

### 6.4 live wait 统一归 `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`

含义：

- 当前 proposal / session 下如果已经有真实 running / queued live task，而你此轮只是等待回流，也统一输出 `WAITING_ON_ASYNC`；
- 不再额外使用单独的 live-wait FINAL_SIGNAL。

系统行为：

- taskboard 会把这类状态视作同一个 `awaiting_async` wait state；
- continuous mode 不会再因为“必须给一个 signal”而把它推回 `LOCAL_MICROSTEP_BATCH`；
- completed/failed 但仍在 `pending_feedback/manual` 的 receipt 不再混进这个状态。

不要在这些情况下使用它：

- 当前并没有真实 running / queued live task，也没有等价 async task；
- 你其实只是等待 receipt 被当前 session 吸收；
- 你明明还能直接吸收刚落盘的新 evidence。

### 6.5 `WAITING_ON_FEEDBACK`

含义：

- 运行已经结束，但 receipt 尚待当前 session 吸收；
- 这通常对应 completed/failed + `pending_feedback=true` / `feedback_mode=manual`。

系统行为：

- continuous/status/observability 会把它显示成独立 wait state；
- `running_task_count` 与 `awaiting_feedback_task_count` 会分别统计，不再把 receipt absorption 混进 live compute；
- 这不是 agent 需要输出的 `FINAL_SIGNAL`，而是 taskboard 内部/观测层状态。

### 6.6 `TASKBOARD_SIGNAL=PARKED_IDLE`

含义：

- 当前 proposal 已 parked；
- 没有新的外部 evidence；
- 没有 running live task；
- canonical head 与主线路由都没有变化。

系统行为：

- taskboard 会把当前 session 标成 `parked_idle`；
- 会先进入短期静默等待；若 parked 超过 watchdog 窗口，会再次提醒 agent 做一次 bounded local self-review，而不是永久沉默；
- 这次 watchdog 唤起优先检查“当前是否还有 1-3 个同一 work package 内可完成的本地短步骤”；只有确认没有时，才继续 parked。

不要在这些情况下使用它：

- 你其实刚拿到新 summary / report / artifact 但还没分析；
- 当前仍有 running live task；
- 你只是想跳过本轮本该完成的数据吸收和 route ranking。

### 6.7 `TASKBOARD_SIGNAL=MATERIALS_READY_FOR_PROPOSAL`

含义：

- 当前轮已经完成 successor hypothesis / proposal 的 CPU-only 材料整理；
- 下一步不应重新 parked，也不应继续停留在普通 local continue；
- taskboard 应切到 proposal materialization / dispatch 的专用 followup。

系统行为：

- taskboard 会把当前 session 推进到 `proposal_materialization` 语义；
- 后续 prompt 会要求 agent 优先完成 proposal/history 写回、显式绑定 proposal，并至少提交一条 live task；
- 只有完成这些动作后，才应返回 `TASKBOARD_SIGNAL=NEW_TASKS_STARTED`。

不要在这些情况下使用它：

- 你只是产生了一个模糊想法，还没有形成可写回 proposal/history 的 successor hypothesis packet；
- 你还没有明确当前应当是“修正大主线”还是“新开小主线”；
- 你其实还停留在 evidence absorption 阶段。

### 6.8 停机信号

- `TASKBOARD_SIGNAL=NO_FURTHER_TASKS`
- `TASKBOARD_SIGNAL=STOP_AUTOMATION`
- `TASKBOARD_SIGNAL=END_EXPERIMENT`

continuous mode 关闭时：这些信号都表示停止自动推进。

continuous mode 开启时：这三个信号都会先进入“收口 -> 新 proposal -> 新任务”转场 override，而不是直接停机；只有完成转场并返回 `TASKBOARD_SIGNAL=NEW_TASKS_STARTED` 后，这条 continuous followup 才会结束。

### 6.9 旧 prompt 协议迁移

旧 prompt 常把 `LOCAL_MICROSTEP_BATCH` 同时当作：

- “我还在 inline continue”；
- “请 taskboard 稍后再从外部叫醒我一次”；
- “我暂时先别被 parked”。

现在要拆成双信号：

- 只是继续同一认知线程，不需要 reminder：`LOCAL_CONTINUE_NO_WAKE`
- 需要 taskboard 短延迟后再次外部重入：`LOCAL_MICROSTEP_BATCH`

如果你的旧 prompt 曾写“继续一小批本地短步骤就输出 `LOCAL_MICROSTEP_BATCH`”，现在应改成“先判断自己要的是 inline continue 还是 external re-wake，再分别输出上述两个信号”。

## 7. taskboard 状态如何理解

实现层现在显式区分几类 session 流状态：

- `inline_continue`
  - 对应 `LOCAL_CONTINUE_NO_WAKE`；
  - 说明当前 session 仍在同一认知线程内推进；
  - taskboard 不应额外创建 reminder，也不应把它计入 parked 候选。
- `local_rewake_requested`
  - 对应 `LOCAL_MICROSTEP_BATCH`；
  - 说明当前 session 明确请求 taskboard 在短延迟后再次外部重入；
  - 这不是“卡住”，而是“请稍后再叫醒我一次”。
- `awaiting_async`
  - 说明当前 session 已经把长任务交出去；
  - taskboard 会等待回流，或用较慢 watchdog 观察。
- `waiting_on_feedback`
  - 说明运行已经结束，但 receipt 尚待当前 session 吸收；
  - status 会显示 `awaiting_feedback_task_count`，而不会再把它算进 `running_task_count`。
- `parked_idle`
  - 说明当前 proposal 已 parked，且没有新 evidence、没有 running live task；
  - taskboard 会先静默等待，避免重复 no-op bookkeeping；
  - 若 parked watchdog 到期，会再次提醒 agent 做一次 bounded local self-review，而不是永久沉默。
- `proposal_materialization`
  - 说明 agent 已经通过 `MATERIALS_READY_FOR_PROPOSAL` 表示 successor 材料已齐；
  - 当前轮的最高优先级是固化 proposal/history、显式绑定 proposal，并至少提交一条 live task。
- `human_guidance_paused`
  - 说明当前 session 正在由人工直接指导；
  - taskboard 暂时不往这个对话里注入新的回流；
  - 已有结果继续排队，等人工解除 pause 后再统一吸收。

这些状态的存在，就是为了避免 agent 被制度性催促重新混淆“inline continue / external re-wake / live wait / receipt absorption”。

## 8. 人工干预暂停 lane

当人工准备在 VSCode 的 Codex 插件里直接指导当前 agent 时，不应靠“希望 taskboard 别来打断”来碰运气，而应显式打开人工暂停：

- CLI：`codex-taskboard human-guidance on --session-id <codex_session_id> --lease-seconds 900`
- dashboard：按 `h`

pause 期间：

- 既有 `queued_feedback` 不丢，只排队；
- followup / continuous reminder / resume 会 defer；
- agent 可以在当前人类指导线程里连续工作，不会被后台消息强制插入。

恢复时：

- CLI：`codex-taskboard human-guidance off --session-id <codex_session_id>`
- 若 session 已迁移，先 `bind` 新 session，再 `clear-session` 旧 session。

## 9. 长上下文遗忘防护

agent 在长上下文里最容易忘的不是实验内容，而是工作流边界。

因此系统现在的固定防护，不再是“多塞几张规则卡”，而是一个不会重复膨胀的最小 prompt 合同：

- canonical head 仅在异常时提示，避免 agent 误以为文档头总有问题；
- 绑定路径和写回要求只出现一次，避免 `proposal_file / project_history_file` 在同轮里重复刷屏；
- `Taskboard 操作方法` 只保留最小动作路由：何时本地 CPU-only、何时 `submit`、何时 `bind-before-launch` / `attach-pid`、何时切到等待信号；
- 固定“轻度科研约定”六条保持不变，不再让 agent 在长上下文里自己压缩“如何使用 taskboard”的手册；
- 协议尾注保持强约束，使 taskboard 能在信号遗漏或上下文漂移时恢复 session 状态。

如果当前 prompt 与你更早的记忆冲突，以这份最小合同为准。

你只需要记住一个判别句：

- “这件事是否需要 future callback？”

如果答案是否，就先留在当前对话里做。

另外，现在每轮回复末尾都应补一组自检行：

- `TASKBOARD_PROTOCOL_ACK=TBP1`
- `CURRENT_STEP_CLASS=inline_now|inline_batch|async_task|milestone_closeout|stop`
- `TASKBOARD_SELF_CHECK=pass|fail`
- `LIVE_TASK_STATUS=none|submitted|awaiting`
- `FINAL_SIGNAL=LOCAL_CONTINUE_NO_WAKE|LOCAL_MICROSTEP_BATCH|ANALYZING_NEW_EVIDENCE|MATERIALS_READY_FOR_PROPOSAL|WAITING_ON_ASYNC|PARKED_IDLE|NO_FURTHER_TASKS|STOP_AUTOMATION|END_EXPERIMENT|NEW_TASKS_STARTED|none`

这组尾注是为了让 taskboard 在 Codex API 异常、上下文漂移或 assistant 忘写独立信号时，仍能恢复“你刚才判断自己处于什么状态”。

如果 taskboard 发现你没有给出合法尾部，它不会继续静默漂移，而会发送一个很短的 `protocol_self_check_repair` followup。这个 followup 不要求你重开整轮分析，只要求你：

1. 用 1-2 句人话确认当前唯一最高优先级动作。
2. 重新补齐正确的协议尾部。

## 10. 反模式

下面这些做法现在都属于反模式：

- 为了显得“有推进”，把读日志、改小脚本、写文档、CPU smoke 都拆成 live task。
- 明明还在做本地短步骤，却因为 followup 压力被迫新建一个低价值任务。
- 明明已经提交了正式 GPU 训练，却不敢说 `WAITING_ON_ASYNC`，反而再提交一条等价任务。
- 明明准备启动一个可能跨回复继续运行的本地 CPU-only 进程，却先裸跑，等出结果或中断之后才想起要不要绑定 taskboard。
- 在 continuous mode 下把 `NO_FURTHER_TASKS` 当成全局停机，而不是阶段性收口。
- 在 parked continuity 且没有新 evidence / live task 时，继续复读 `LOCAL_MICROSTEP_BATCH` 制造“伪推进”。
- 在 successor bootstrap 材料已经齐全时，仍继续重复 `PARKED_IDLE`，把 proposal 写回和任务分发长期拖延成 parked 死锁。
- 把同一 CPU-only claim-boundary / closeout 工作包拆成 allowed/forbidden/effectiveness/guardrail 等一串 successor proposal，即使没有新证据对象、没有 route 变化，也没有新的 async 需求。
- 把“连续完成 2-5 个 microsteps”误当成 KPI，而不是把它当作同一 work package 内的经验批量。
- 为了执行一次“收口”机械增加同义 closeout 文档或额外 proposal，制造制度性开销。

## 11. 推荐执行顺序

每次决定下一步时，按下面顺序走：

1. 先看当前结果是否已经足够更新 proposal / history / closeout。
2. 先判断当前是否仍属同一 `same work package`；如果是，优先继续在当前 proposal/history 中推进，而不是先新建 successor proposal。
3. 判断下一动作属于 `inline_now`、`inline_batch`、`bind_before_launch` 还是 `async_task`。
4. 如果属于本地短步骤，直接做；默认允许在同一上下文连续完成 2-5 个 microsteps，但这只是经验批量，不是 KPI。真正目标是把同一 work package 中当前能立即完成并能吸收结果的本地动作尽量一次做完，不要把读取 summary、forensics、短日志写回或 proposal 局部改写拆成多个 task。
5. 如果连续两轮只是文档切分而没有新增实证工件或 route 变化，先回读 history / authoritative proposal，把已经拆散的判断合并写回，再决定下一步。
6. proposal 的高门槛只约束“新 proposal / 新 async / route-level 物化”，不阻断当前 proposal 内的数据处理、证据分析、route note、planning note 或局部改写；若当前 proposal/history/时间日志已经足以承载一次阶段吸收，不要为了执行一次“收口”机械新建同义 closeout 文档。
7. 如果属于 `bind_before_launch`，先绑到 taskboard，再启动或继续。
   推荐命令：

```bash
codex-taskboard bind-before-launch \
  --task-id local-audit \
  --workdir /path/to/project \
  --command 'python audit.py'
```

8. 如果属于长任务，提交真实 async task。
9. 如果长任务已提交、当前只是等待，输出 `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`。
10. 如果只是继续同一认知线程且不需要 taskboard 再次外部唤起，输出 `TASKBOARD_SIGNAL=LOCAL_CONTINUE_NO_WAKE`；如果明确需要 taskboard 在短延迟后再次外部重入当前会话，再输出 `TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH`。
11. 回复末尾补协议尾注，自检一次 taskboard 操作是否正确。
12. 只有在真的应停机时，才输出停机信号。

## 12. 对当前项目资源约束的含义

当前平台的主要耗时并不在 CPU-only 审计和 smoke，而在正式 GPU 多卡训练。

因此新的 workflow 默认倾向于：

- 放宽本地短步骤在当前上下文里的连续执行；
- 减少为了“制度完整”而多建的 CPU-only task；
- 把 queue 和治理成本优先留给真正昂贵的 GPU / 多卡 / 远程长任务。

这不是降低治理强度，而是把治理成本放到真正值得管理的地方。
