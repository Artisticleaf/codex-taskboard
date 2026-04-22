# Agent Workflow Spec

## 1. 状态机

### 1.1 自动化模式

- `managed`
  - taskboard 只托管任务、记录 backlog、允许人工稍后处理回流。
  - 不自动再唤起同一 session。
- `continuous`
  - taskboard 自动把同一链路的回流并入当前 session。
  - 生命周期循环为 `planning -> execution -> closeout -> planning`。

### 1.2 科研阶段

- `planning`
  - 读取 history / handoff / closeout / 关键日志 / proposal。
  - 如有必要，补读旧文献和 2024 年后的最新顶刊顶会文献。
  - 形成最新 proposal，并把首个可执行验证包准备到可分发状态。
- `execution`
  - 在统一上下文中完成 receipt 吸收、代码/数据审计、局部修复、proposal/history 写回、实验包准备与提交。
  - 能在当前上下文完成的 CPU-only 工作，不拆成额外 scene。
- `closeout`
  - 只有在 execution 已经证明当前 proposal 继续扩展没有新的信息收益时才能进入。
  - 必须完成总结、history 回写、handoff、binding 确认，并由 taskboard 拉起下一轮 planning。

## 2. 公开 signal

agent 面向 taskboard 的公开信号只保留：

- `TASKBOARD_SIGNAL=EXECUTION_READY`
- `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`
- `TASKBOARD_SIGNAL=CLOSEOUT_READY`
- `TASKBOARD_SIGNAL=none`

兼容说明：旧信号仍会被 parser 尽量归一化，但 taskboard 不再要求 agent 主动输出它们。

## 3. execution 的统一上下文

execution prompt 会显式要求 agent 把下列动作尽量收敛在一轮内完成：

1. 吸收 receipt / summary / report / log / artifact。
2. 审计异常结果，优先怀疑实现、数据契约、split、配置与 run 完整性。
3. 做必要的局部修复、脚本/spec/config 物化与 smoke 前置。
4. 把可靠结论、失败边界、关键诊断和 next bounded action 滚动写回 proposal/history。
5. 只有当实验包已经可执行且确实需要 GPU / remote / 长等待时，才提交 taskboard 任务。

## 4. closeout 约束

agent 不得把 closeout 当作偷懒出口。发出 `CLOSEOUT_READY` 前，必须已经在 execution 中明确写出：

- 当前 proposal 已完成哪些工作。
- 当前结果在哪些 benchmark / 数据集上说明了什么。
- 继续扩展为什么不会再带来新的信息收益。
- 当前主线应如何继承这些结果。

进入 closeout 后，必须完成：

1. proposal 全量结果总结，要求“说人话”。
2. history 回写，附时间戳与关键文件路径。
3. handoff 文档，说明背景、现状、主线、待解决问题、必读文件与建议文献方向。
4. handoff / binding 确认，避免把下一位 agent 绑错 proposal/history。

## 5. managed / continuous 的行为差异

- `managed`
  - taskboard 仍可托管任务、记录回流、积压 backlog。
  - 但不会自动唤起 agent 吸收回流。
- `continuous`
  - 回流会继续推动当前链路自动前进。
  - 如果当前 signal 为 `WAITING_ON_ASYNC`，taskboard 默认每 1 小时提醒一次，确认 live task 仍在产出。
  - 如果 agent 没有输出合法尾部协议，会进入 `protocol-repair` 极短纠错 prompt。

## 6. 固定尾部协议

回复末尾固定输出：

- `TASKBOARD_SIGNAL=EXECUTION_READY|WAITING_ON_ASYNC|CLOSEOUT_READY|none`
- `TASKBOARD_SELF_CHECK=pass|fail`
- `LIVE_TASK_STATUS=none|submitted|awaiting`
