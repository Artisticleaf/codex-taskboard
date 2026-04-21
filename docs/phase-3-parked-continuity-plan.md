# Phase-3 / Phase-4 提案：新服务已切换，后续以 prompt-first 去除制度性开销

## 1. 当前结论

本提案先回答两个已经确认的问题：

1. 新版 `codex-taskboard` 代码已经落盘，而且核心 continuity 逻辑已在源码中。
2. 直到 2026-04-11 深夜，线上 dispatcher 仍然在跑旧进程；这确实会让 agent 继续收到旧 prompt。
3. 现在服务已被重新切到新 runtime；如果后续仍出现“拆分为原子动作”的结构性开销，主因已经不再是旧服务未替换，而是当前 prompt 设计本身仍偏重、偏重复、偏防御性。

也就是说，当前问题已经从“部署未生效”转成“部署已生效，但 prompt 仍需继续瘦身和正向化”。

## 2. 服务替换证据

### 2.1 代码与运行态时间线

- `src/codex_taskboard/cli.py` 修改时间：`2026-04-11 23:33:38 +0800`
- API 服务主进程：`PID 1257797`，启动于 `2026-04-11 23:57:43 +0800`
- dispatcher 服务主进程：`PID 1260071`，启动于 `2026-04-12 00:00:30 +0800`

因此，当前 API / dispatcher 主进程启动时间都晚于本轮源码改动时间，已经不再是旧二进制对应的长驻进程。

### 2.2 旧运行态为何没有自动切走

旧 dispatcher 的 systemd 单元设置了：

- `KillMode=process`

这意味着仅主进程收到停止信号时，已经派生出去的子进程不会被一起清掉。此前旧 dispatcher 正在 `deactivating (stop-sigterm)`，同时仍挂着一条旧 `codex exec resume` 子进程，因此看起来像“代码已经改了，但线上 prompt 仍没变化”。

### 2.3 旧 prompt 仍在线的直接证据

旧 dispatcher 仍挂着一条 resume 子进程，其 prompt 文本里包含：

- `不要先为了证明推进而扩动作。`

而当前源码标准 followup 路径中，这句旧文案已经不存在。

### 2.4 当前新源码中的连续推进支点

当前 `src/codex_taskboard/cli.py` 已包含：

- `recent_local_evidence_sweep_hint(...)`
- `collect_local_evidence`
- `session_continuation_hint(...)` 优先看本地 evidence sweep
- `automation_recommendation_for_session(...)` 中 `collect_local_evidence` 先于 `wait_for_external_evidence`

这说明 phase-3 的轻逻辑支点已经在代码里，旧 runtime 只是此前没有切换成功。

## 3. 现在剩下的真实问题

服务替换完成后，剩余问题不再是“旧 prompt 未下线”，而是下面三类结构性开销。

### 3.1 prompt block 仍然偏厚

当前 `cli.py` 中仍保留了较厚的共享块，尤其是：

- `taskboard_memory_contract_lines()`
- `taskboard_protocol_card_lines()`
- `compact_binding_execution_sections()`
- `continuous_research_flow_lines()`

这些块虽然已经比旧版短，但在多个 scene 中叠加后，依然容易让模型把一次正常推进理解为“先合规、再停下”。

### 3.2 parked / waiting 仍然带有软停机重心

虽然逻辑上已经把 `collect_local_evidence` 前置，但 prompt 仍会在多个场景中强调：

- 什么时候可以 `WAITING_ON_EXTERNAL_EVIDENCE`
- 什么时候可以 `PARKED_IDLE`
- 什么时候“不应重复某些动作”

这些提示本来是防止乱扩任务，但在语气和排序上仍容易把 agent 拉向“先证明自己知道停机条件”，而不是“先把当前上下文还能做完的本地动作吃干”。

### 3.3 同义治理信息仍在多场景重复

尤其重复的是：

- canonical head 相关提醒
- proposal/history binding
- inline vs async 判定
- parked fallback 提示
- footer 之外的自然语言治理说明

这类重复不会改变状态机，却会显著增加回复趋向“单步、守规、保守结束”的概率。

## 4. 目标重述

本轮后续改造目标不是重写状态机，而是把 prompt 调整到如下行为：

1. 先吸收本地 evidence。
2. 再完成当前上下文可落地的 bounded local action。
3. 若当前 family 已收口，则优先在当前上下文完成 successor skeleton / route replanning / proposal writeback。
4. 只有当以上动作都真正完成，且确实没有本地下一跳时，才输出 `WAITING_ON_EXTERNAL_EVIDENCE` 或 `PARKED_IDLE`。

换句话说：

- `WAITING_ON_EXTERNAL_EVIDENCE` / `PARKED_IDLE` 必须变成“最后兜底”，不是“默认安全出口”。

## 5. 约束：优先 prompt-first，少动逻辑

本提案维持下列约束不变：

1. 不先合并 signal。
2. 不先改 parser contract。
3. 不先大改 dispatcher / watchdog 状态机。
4. 优先通过 prompt block 去重、排序和语气修正达成目标。
5. 只有 prompt-first 之后仍明显软停机，再补最小逻辑修整。

## 6. Prompt-first 改造主线

### 6.1 第一优先级：把 prompt 从“防误停机”改成“默认继续吃干”

核心原则：

- 先给 agent 当前场景下 1-2 个可执行动作；
- 再给出等待态的兜底条件；
- 不要把等待态写成醒目的主叙事。

具体做法：

1. 把 `WAITING_ON_EXTERNAL_EVIDENCE` / `PARKED_IDLE` 从 intro 和长段说明里继续后移。
2. 保留 footer contract，但尽量不在正文里重复解释 footer 已能表达的规则。
3. 尽量把“不要……”改写成“优先……”或“当……已完成时，可……”。
4. 只要还是同一个 prompt-first / CPU-only / 无外部等待的工作包，就继续在当前轮直接推进；只有遇到真正的观察等待、异步生命周期、或需要先看新回流效果时，才分到下一轮。

### 6.2 第二优先级：按 scene 压缩 block

使用 `docs/prompt-scene-matrix.md` 作为实施矩阵，重点压缩：

- `standard_followup`
- `continuous_research`
- `parked_watchdog`
- `resume_event`
- `queued_batch`

其中 `parked_watchdog` 和 `continuous_research` 是最优先的两类，因为它们最容易把模型推回“原子动作式守规回复”。

### 6.3 第三优先级：去掉重复 machine anchor

后续 prompt 共享块要进一步执行两条规则：

1. `canonical_head_check + proposal_binding/project_history_file` 只在同一 prompt 中出现一次；
2. binding 路径块和写回要求不再在不同 scene helper 中重复拼接。

### 6.4 第四优先级：把 parked continuity 改成正向分流

对 parked / waiting 场景，正文优先级应统一为：

1. `local_evidence_sweep`
2. `same-context writeback`
3. `successor skeleton / route replanning`
4. `async/live dispatch`
5. `WAITING_ON_EXTERNAL_EVIDENCE` / `PARKED_IDLE`

也就是说，waiting/parked 不是主动作，只是兜底输出。

## 7. 需要删除或降级的文案类型

### 7.1 直接删除

1. 旧负向措辞：
   - `不要先为了证明推进而扩动作。`
   - `不要先想要不要扩动作。`
   - `不要先为了推进而扩动作。`
2. 同一 prompt 中重复出现的 parked-safe-exit 提示。
3. footer 已能表达但正文重复解释的 machine rule。

### 7.2 改写为正向措辞

下面这类句子不一定错误，但会削弱 agent 的推进姿态，应改写：

1. `若确认当前是 parked continuity + no new evidence + no live task，且确实已经没有 bounded local action，请输出 WAITING_ON_EXTERNAL_EVIDENCE 或 PARKED_IDLE。`
   - 问题：句法焦点落在“输出等待态”，而不是“先完成本地动作”。
   - 建议改成：`先完成当前回合仍可吸收的本地动作；当这些动作已做完、且没有新 evidence / live task 时，再用 WAITING_ON_EXTERNAL_EVIDENCE 或 PARKED_IDLE 作为兜底状态。`

2. `只有在出现新的证据对象/经验分支、准备发起真正需要 async/GPU/remote 生命周期的动作、或形成足以改变主线路由的阶段性里程碑时，才新建 proposal、阶段 closeout 或真实 async task。`
   - 问题：虽然约束合理，但语气像层层设门，容易让 agent 把注意力放在“我是否越界”，而不是“我能否先完成当前 writeback / proposal skeleton”。
   - 建议改成：`默认继续在当前 proposal/history 中完成同上下文 writeback；当确实出现新证据对象、独立经验分支、async/GPU/remote 生命周期需求或足以改变主线路由的里程碑时，再升级为新 proposal、阶段 closeout 或真实 async task。`

## 8. 仍然保留的最小 contract

以下内容必须保留，但应尽量 machine-like、一次出现：

1. `canonical_head_check`
2. `Evidence-first loop`
3. footer contract 五行
4. 当前绑定的 `proposal_file` / `project_history_file`
5. `inline microstep` vs `async` 的最小判定
6. continuous mode 下三个关键出口：
   - `WAITING_ON_EXTERNAL_EVIDENCE` / `PARKED_IDLE`
   - `MATERIALS_READY_FOR_PROPOSAL`
   - `NEW_TASKS_STARTED`

## 9. 实施顺序

### Phase-4A：只动 prompt 共享块

1. 进一步压缩 `compact_context_sections(...)`
2. 进一步压缩 `compact_binding_execution_sections(...)`
3. 把 parked fallback 从正文主体降为尾段兜底
4. 统一正向措辞

### Phase-4B：按 scene 再去重

1. `parked_watchdog`
2. `continuous_research`
3. `resume_event`
4. `queued_batch`
5. `standard_followup`

### Phase-4C：仅在 prompt-first 不足时再补逻辑

若完成 Phase-4A / 4B 后，仍频繁出现：

- 没有 live task、没有新 evidence，却快速落回 parked
- agent 明明还能做 proposal skeleton / route note，却先停机

再考虑最小逻辑改动，例如：

1. parked 场景下更激进地优先 `collect_local_evidence`
2. 把 `ANALYZING_NEW_EVIDENCE` 收口成 `LOCAL_MICROSTEP_BATCH` 的 reason
3. 进一步弱化 `WAITING_ON_EXTERNAL_EVIDENCE` 与 `PARKED_IDLE` 的 prompt 露出

## 10. 验收标准

完成后，至少满足以下观测标准：

1. 新启动的服务进程时间戳晚于源码改动时间戳。
2. 新 followup prompt 中不再出现旧负向短语。
3. `parked_watchdog` / `continuous_research` 场景优先给出本地 evidence sweep 或 same-context writeback，而不是先写等待态。
4. 同一 prompt 中：
   - `canonical_head` 只出现一次；
   - `proposal binding` 只出现一次；
   - `Taskboard quick memory` 不与长版 protocol card 同时大段共现。
5. agent 在无新 live task 时，优先：
   - 审查本地 receipt / artifact
   - 完成 proposal/history 写回
   - 起草 successor skeleton
   而不是 1 个 microstep 后立即 parked。

## 11. 本提案的定位

本文件现在不是“是否需要切服务”的提案，而是“服务已切到新 runtime 后，如何继续把制度性开销从 prompt 层打掉”的执行提案。

后续实施应以 `docs/prompt-scene-matrix.md` 为 scene/block 级别规范，以本文件作为总路线与优先级说明。
