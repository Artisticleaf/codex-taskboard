# codex-taskboard Agent 分发 README

## 1. 目标

这份文档给会通过 `codex-taskboard` 分发任务、接收回传、继续推进科研链路的 agent 使用。

当前关于“准备性短步骤留在当前对话、可执行实验尽快绑定 taskboard 生命周期”的权威规范，以 [`docs/AGENT_WORKFLOW_SPEC_ZH.md`](/home/Awei/codex-taskboard/docs/AGENT_WORKFLOW_SPEC_ZH.md) 为准。
旧的“默认把下一步都物化成 live task”的口径已经退役。

你需要记住一件事：

taskboard 现在不只是一个“任务执行器”，它同时也是研究链路的上下文维护器。proposal、收口文档、followup、pending feedback、session 锁，都是同一套闭环的一部分。

## 1.1 最新版后台 prompt 合同

当前 runtime 已经改成外部状态更少、scene 更明确的版本。agent 实际看到的是一套更短、更稳定的合同：

- 自动化模式只分 `managed` 与 `continuous`；
- continuous 里的科研阶段只分 `planning`、`execution`、`closeout`；
- prompt 顶部保留：continuous 开场句、5 条“轻度科研约定”、绑定的 `proposal_file` / `project_history_file` / `closeout_proposal_dir`；
- scene 本身只强调当前阶段的目标和决策顺序，不再重复堆叠多段“闭环 prompt”；
- 协议尾部只保留三行：`TASKBOARD_SIGNAL`、`TASKBOARD_SELF_CHECK`、`LIVE_TASK_STATUS`。

说人话：

- 现在的关键不是再记一张更长的 protocol card；
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

当前 agent 对 taskboard 的公开信号只保留 4 个：

### `TASKBOARD_SIGNAL=EXECUTION_READY`

- planning 已经完成，或者 execution 还存在明确的本地可执行动作；
- taskboard 不应把这理解成“可以停一下”，而应继续围绕当前 proposal 推进；
- CPU-only 的结果读取、审计、修复、写回、实验包准备，默认都留在这一条上下文里完成。

### `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`

- 当前已经有 live task / 托管实验在运行，或者本轮唯一合理动作就是等待它回流；
- taskboard 会按 1 小时节奏提醒对应 agent 回来确认一次，只是为了确保实验没有卡住、仍然有日志或结果产出；
- 这时不应再重复提交等价实验。

### `TASKBOARD_SIGNAL=CLOSEOUT_READY`

- 只有在 execution 中已经重读 proposal/history 与本轮证据，并明确写出“继续扩展当前 proposal 已无新的信息收益”的分析后，才允许返回这个信号；
- 该信号不会直接停机，而是进入 closeout prompt，要求 agent 完成收口分析、history 回写、handoff 和绑定确认。

### `TASKBOARD_SIGNAL=none`

- 表示 closeout 已完成；
- taskboard 会根据 handoff 和当前绑定继续引导下一轮 planning；
- 这不是“什么都不做”，而是“当前 proposal 生命周期已经正式交棒”。

## 5. 连续模式下的研究要求

continuous mode 的默认目标不是“机械切阶段”，而是先把当前 proposal 内能做深做实的动作完成。优先顺序应是：

1. 先吸收 receipt / summary / report / artifact，把关键数字、异常点和科学含义提炼清楚。
2. 只要结果异常、日志异常、关键数字反常，或者和已有 history、文献、官方推荐参数冲突，就先审实现、数据、配置、split 与运行完整性。
3. 当前上下文里能完成的 CPU-only 工作尽量一次做完：代码/数据审计、局部修复、数据处理、proposal/history 写回、实验包准备不要人为拆散。
4. 只有当实验包已经可执行、可审计，而且确实需要 GPU、远程或长等待时，才提交 taskboard 任务。
5. 只有在 execution 中已经明确写出“继续当前 proposal 已无新的信息收益”的分析后，才进入 closeout。
6. closeout 的目标不是停机，而是把结果、history 和 handoff 加工成下一轮 planning 可以直接继承的起点。

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
