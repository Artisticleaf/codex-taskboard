# codex-taskboard Agent 分发 README

## 1. 目标

这份文档给会通过 `codex-taskboard` 分发任务、接收回传、继续推进科研链路的 agent 使用。

当前关于“准备性短步骤留在当前对话、可执行实验尽快绑定 taskboard 生命周期”的权威规范，以 [`docs/AGENT_WORKFLOW_SPEC_ZH.md`](/home/Awei/codex-taskboard/docs/AGENT_WORKFLOW_SPEC_ZH.md) 为准。
旧的“默认把下一步都物化成 live task”的口径已经退役。

你需要记住一件事：

taskboard 现在不只是一个“任务执行器”，它同时也是研究链路的上下文维护器。proposal、收口文档、followup、pending feedback、session 锁，都是同一套闭环的一部分。

## 1.1 最新版后台 prompt 合同

当前 runtime 不再同时发长版 `Taskboard protocol card`、quick memory 和多段重复执行闭环。agent 实际看到的是一套更短、更稳定的合同：

- prompt 顶部先放：continuous 模式开头句、固定“轻度科研约定”、以及合并后的“写回与转场要求”；
- 仅在异常时显示 canonical head 提示；
- 单次出现 `proposal_file`、`closeout_proposal_dir`、`project_history_file`、`project_history_log_dir`；
- 单次出现 `Taskboard 操作方法`：本地 CPU-only 留在当前对话；正式实验用 `submit`；跨回复本地长任务用 `bind-before-launch` / `attach-pid`；默认推荐 tmux；
- 固定协议尾注：`TASKBOARD_PROTOCOL_ACK=TBP1`、`CURRENT_STEP_CLASS`、`TASKBOARD_SELF_CHECK`、`LIVE_TASK_STATUS`、`FINAL_SIGNAL`。

说人话：

- 现在的关键不是再记一张更长的 protocol card。
- 而是始终沿着同一个 proposal/history，在当前长上下文先把 CPU-only 证据吃干，再把真正值得生命周期管理的实验交给 taskboard。

## 2. 绑定会话的硬规则

- 不要再通过 `list-threads` 人工猜当前 session。
- 优先使用当前调用上下文自动继承的 session。
- 需要显式查看时，用：

```bash
codex-taskboard current-thread
```

- 如果当前环境拿不到 `CODEX_SESSION_ID` / `CODEX_THREAD_ID`，应回到正确的调用上下文，而不是从全局线程列表里挑一个“看起来像”的 id。
- 当前版本在解析 session 时，会优先信任调用环境里的真实 session；同 workdir 下 taskboard 的历史活跃任务只作为缺省兜底，不再覆盖插件当前 live 会话。

## 3. Proposal 绑定规则

### 主链任务

主训练、主分析、主 proposal 更新任务应绑定 proposal，并视为该 proposal 的 owner。

主链任务需要做的事：

- 把结果和分析写回 proposal。
- 在 proposal 中写明下一步实验计划。
- 继续分发后续任务时，保持同一个 proposal。
- 显式传入同一个 `closeout_proposal_dir`，让 close-out 与 proposal 始终落在同一目录。

### 辅助任务

watcher、评测、日志归档、数据预处理、资源巡检等辅助任务通常属于 sidecar。

sidecar 规则：

- 可以沿用同一个 proposal。
- 不单独维护一份平行 proposal。
- 如果 sidecar 的结果影响主方向，应把结论并回主 proposal。

### 谁来绑定 proposal

建议策略：

- 主链任务负责“拥有” proposal。
- 同 session、同 workdir 下的辅助任务默认继承 proposal。
- 同 session、同 workdir 下的辅助任务默认继承 `closeout_proposal_dir`。
- 只有当辅助任务和主链完全无关时，才显式 `--no-inherit-proposal`。
- 如果某个 sidecar 演变成新的主方向，它应先产出新的 proposal，再开始新的链路。

## 4. 信号规则

### 本地短步骤续跑

- `TASKBOARD_SIGNAL=LOCAL_CONTINUE_NO_WAKE`
- 表示当前轮仍在同一认知线程内推进，不需要 taskboard 额外创建 reminder，也不应累计 local park counter。
- `TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH`
- 表示当前轮明确请求 taskboard 短延迟后再次外部重入当前会话。
- 二者都不是“没有下一步”，区别只在于是否需要 scheduler 再叫醒你一次。

### 已提交长任务或确认当前已有 live task，等待回流

- `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`
- 表示等价 async / live task 已经提交或确认存在，当前只是等待它的生命周期自动回流。
- 只要当前 proposal / session 下已经有真实 running / queued live task，也统一继续使用这个信号。
- 这时不应再被 followup 误导去新建等价 live task。

### live wait 与 receipt absorption

- `WAITING_ON_FEEDBACK`
- 表示运行已经结束，但 receipt 尚待当前 session 吸收；completed/failed + `pending_feedback/manual` 不再被误报成 live。

### proposal 已 parked，等待外部证据

- `TASKBOARD_SIGNAL=PARKED_IDLE`
- 表示当前 proposal 已 parked，且没有新 evidence、没有 running live task、canonical head 未变化。
- continuous mode 下这不是永久静默：taskboard 会先短期安静，超过 parked watchdog 窗口后会再次提醒。
- 收到这种 watchdog reminder 时，先重读 proposal/history 顶部 canonical head、最近时间日志和已落盘工件，完成一次 bounded local self-review；只有确认仍然没有可执行动作时，才再次回到 parked。

### 连续模式关闭

- `TASKBOARD_SIGNAL=NO_FURTHER_TASKS`、`TASKBOARD_SIGNAL=STOP_AUTOMATION`、`TASKBOARD_SIGNAL=END_EXPERIMENT`：都表示自动推进可以停止。

### 连续模式开启

- `TASKBOARD_SIGNAL=NO_FURTHER_TASKS`、`TASKBOARD_SIGNAL=STOP_AUTOMATION`、`TASKBOARD_SIGNAL=END_EXPERIMENT`：都不再是直接停机，而是进入“收口 -> 新 proposal -> 新任务”的转场 override。
- taskboard 会改发新的 prompt，要求你继续：
  - 写收口文档
  - 遍历 proposal 和收口文档
  - 查最新顶刊顶会论文
  - 分析与已有工作的关系
  - 新建 proposal
  - 清理无关旧任务
  - 启动新一轮实验
- 只有 proposal/history 已更新、且至少一条下一阶段受托管实验已提交后，才应返回 `TASKBOARD_SIGNAL=NEW_TASKS_STARTED`。

## 5. 连续模式下的研究要求

continuous mode 的默认目标不是“机械新开 proposal”，而是先把当前证据吃干。优先顺序应是：

1. 先走 same-context local fast path：吸收 receipt / summary / report / artifact，完成 `receipt absorption -> data extraction -> why explanation -> next bounded action`。
2. 如果当前仍属同一 CPU-only work package，默认允许在同一上下文连续完成 2-5 个本地微步骤；但“2-5 个”只是经验批量，不是 KPI。真正目标是把同一 work package 中当前能立即完成并能吸收结果的本地动作尽量一次做完，不要把读取日志、item-level forensics、短日志写回或 proposal 局部改写拆成多个 live task。
3. proposal 的高门槛只约束“新 proposal / 新 async / route-level 物化”，不阻断当前 proposal 内的数据处理、证据分析、route note、planning note 或局部改写。
4. 若当前 proposal/history/时间日志已经足以承载一次阶段吸收，就不要为了执行一次“收口”机械新建同义 closeout 文档；只有 route-level 边界变化、需要 async 绑定，或当前证据尚未被清楚吸收时，才补最小必要的收口写回。
5. 只有当当前 work package 已经吃干、没有 bounded local action、且 route-level hypothesis 确实发生变化时，才进入阶段转场。
6. 真正进入转场后，再按 `最小必要 closeout -> history -> 新 proposal -> 新 live task -> queue hygiene` 的顺序推进。
7. 查文献只在进入新方法 family、需要论文级外部对照/novelty 判断、或准备形成长期新方向时进行；不要把它变成每次 parked/closeout 的默认动作。

## 6. 研究设计约束

为了避免走向“经验主义堆参数”，请遵守：

- 不要按具体数据集名字硬编码一套单独路由。
- 如果需要差异化设计，优先按任务类别、评价指标、错误类型、难度层级、模态或 failure family 建模。
- 不要把大量算力花在细碎调参上，除非已经接近严格意义上的 SOTA，或者当前最关键瓶颈就是参数设定。

## 7. 回传与 followup 的新行为

taskboard 现在会：

- 对同一 session 的自动 resume 加锁。
- 在会话忙、429、最近有活动时，把结果留在 `pending_feedback` 队列里延后重试。
- 同一 session 的同批完成 task 聚合成一次 consolidated prompt。
- followup nudge 也走同一套锁和空闲判断。

因此：

- 不要再把“忙时重试”理解成一次模糊 followup。
- 你的任务结果不会因为线程忙而被直接顶掉。

### 后台 prompt profile 约定

当前后台回传默认区分两类 prompt：

- `compact`：用于 `resume`、标准 `followup`、continuous followup 和 queued feedback batch。它保留 proposal 绑定、claim boundary、写回要求、安全说明和“必须落成真实动作”的闭环约束，但不再重复整套长期治理条款。
- `full`：只保留给需要完整治理包、离开当前会话上下文也必须独立成立的 prompt。

为什么这些后台场景默认用 `compact`：

- 这些消息本质上都是“对当前对话的补充上下文”，不是新的主任务说明书。
- continuous 模式和 queued feedback 会重复触发；如果每次都塞 full prompt，最容易出现提示膨胀、动作尾部被挤掉、agent 反复总结却不落任务的空转。
- batch 回传应共享一套治理头，而不是每条完成任务各自携带一份完整长 prompt。

因此，如果你未来扩展新的后台提醒，默认应先判断它是不是 live 对话内的补充上下文；如果是，优先发 `compact`，除非确实需要完整治理包才升级到 `full`。

## 8. 提交示例

### 主链任务

```bash
codex-taskboard submit-job \
  --task-id p01-main-train \
  --workdir /home/Awei/LLM/passage/projects/P01_curiosity_grpo \
  --command 'python train.py' \
  --proposal /home/Awei/LLM/passage/projects/P01_curiosity_grpo/experiments/PLAN-QWEN3-14B-AUTOPILOT-LIVING-20260329.md \
  --feedback-mode auto
```

### sidecar 评测任务

```bash
codex-taskboard submit-job \
  --task-id p01-eval \
  --workdir /home/Awei/LLM/passage/projects/P01_curiosity_grpo \
  --command 'python eval.py' \
  --feedback-mode auto
```

如果它和主链同 session、同 workdir，proposal 会自动继承。

## 9. 一句话原则

同一个实验链尽量只有一个主 proposal；辅助任务服务于 proposal，而不是和 proposal 竞争叙事权。

补充要求：

- close-out 与 proposal 使用中文书写。
- 用词尽量面向论文写作、正式技术说明和后续可复用叙述，不要只写内部 shorthand。
- 当前这条链推荐显式传入：
  - `/home/Awei/LLM/passage/projects/P01_curiosity_grpo/closeout_proposal`
