# Prompt Scene Matrix

## 目标

本轮矩阵服务于一个更具体的目标：

- 新 runtime 已切换成功；
- 下一步不先大改状态机，而是继续通过 prompt 去重、压缩和正向化，减少 agent 被诱导成“原子动作 + 软停机”的制度性开销。

因此，这份 matrix 的用途不是解释所有状态，而是约束每个 prompt scene 只保留当前动作真正需要的 block。

## 总原则

1. scene 先给动作，再给等待态兜底；
2. 同一 prompt 中，`canonical_head` 只出现一次；
3. 同一 prompt 中，`proposal_binding / project_history_file` 只出现一次；
4. footer contract 保留，但不在正文重复解释 footer 已经表达的机器规则；
5. 能用共享 helper 表达的内容，不再在 scene builder 手写重复文案；
6. 优先用正向措辞，不用打击积极性的负向口吻。
7. 只要还是同一个 prompt-first / CPU-only / 无外部等待的工作包，就继续在当前轮直接推进；只有遇到真正的观察等待、异步生命周期、或需要先看新回流效果时，才分到下一轮。

## Scene Matrix

| scene | 主要用途 | 必留 block | 应删除/禁止 | 建议 soft cap |
| --- | --- | --- | --- | --- |
| `standard_followup` | 常规 same-context followup | `canonical_head_machine` / `evidence_first` / `protocol_card_min` / `binding_once` / `execution_min` / `footer_contract` | `memory_full`、重复 parked fallback、重复 binding、重复 canonical 提醒 | `<= 3600 chars` |
| `continuous_research` | continuous local fast path | `canonical_head_machine` / `recent_next_action_or_local_sweep` / `evidence_first` / `memory_compact` / `flow_compact` / `binding_once` / `footer_contract` | 长版 protocol card、重复 parked-safe-exit、重复执行闭环 | `<= 3400 chars` |
| `parked_watchdog` | parked continuity 唤醒 | `canonical_head_machine` / `recent_next_action_or_local_sweep` / `bounded_self_review` / `binding_once` / `footer_contract` | quick memory 整段、完整 protocol card、长版 execution flow、显眼 waiting 主叙事 | `<= 2500 chars` |
| `materials_ready` | proposal 固化与任务绑定 | `canonical_head_machine` / `dispatch_steps` / `binding_once` / `footer_contract` | parked 规则、重复 evidence-first、重复路径块 | `<= 2600 chars` |
| `resume_event` | 单条后台回流 | `resume_intro` / `event_detail` / `binding_once` / `footer_contract` | quick memory、长版 protocol card、重复 waiting hints | `<= 2600 chars + event block` |
| `queued_batch` | 多条回流合并 | `batch_header` / `event_briefs` / `binding_once` / `footer_contract` | quick memory、完整 protocol card、重复 evidence-first loop | `<= 3200 chars + event blocks` |
| `continuous_transition` | 阶段收口转场 | `canonical_head_machine` / `transition_steps` / `binding_once` / `footer_contract` | 长版 memory、parked 细则、重复 inline/async 定义 | `<= 2900 chars` |
| `protocol_repair` | 只修协议尾部 | `issue_summary` / `current_action` / `footer_contract` | canonical 长块、binding 长块、evidence-first 长解释 | `<= 900 chars` |

## Scene Priority

对于 `continuous_research` 与 `parked_watchdog`，正文中的动作优先级统一为：

1. `local_evidence_sweep`
2. `same-context writeback`
3. `successor skeleton / route replanning`
4. `async/live dispatch`
5. `PARKED_IDLE`

等待态必须是最后兜底，不是开场主叙事。

## Block Budget

| block | 角色 | 预算 |
| --- | --- | --- |
| `canonical_head_machine` | 机器锚点，只输出检查结果与必要摘要 | `2-4 lines` |
| `binding_once` | proposal/history 路径与写回锚点 | `4-7 lines` |
| `evidence_first` | `receipt -> data -> why -> next action` | `2 lines` |
| `protocol_card_min` | 仅保留最必要协议摘要 | `4-6 lines` |
| `memory_compact` | 只列最关键 signals | `3-5 lines` |
| `bounded_self_review` | parked scene 的本轮动作列表 | `4-6 lines` |
| `flow_compact` | continuous flow 的最小推进顺序 | `4-6 lines` |
| `dispatch_steps` | proposal materialization / bind / submit | `4-6 lines` |
| `footer_contract` | 结构化尾部五行 | `5 lines` |

## 删除清单

以下内容默认删除，或只能在全 prompt 中出现一次。

### A. 负向、抑制性语句

1. `不要先为了证明推进而扩动作。`
2. `不要先想要不要扩动作。`
3. `不要先为了推进而扩动作。`
4. 任何把“停下来证明自己没越界”放在句首的写法。

### B. 重复锚点

1. `canonical_head_check` 与“先回读 canonical head”的重复共现；
2. `proposal_file / closeout_proposal_dir / project_history_file` 在多个块里重复打印；
3. `写回要求` 在 scene intro、binding block、execution block 三处重复。

### C. 重复治理说明

1. `Evidence-first loop` 在同一 prompt 中出现多次；
2. `Taskboard protocol card` 和 `Taskboard quick memory` 大段共现；
3. `inline microstep` / `async` 的定义在多个 block 重复；
4. parked fallback 在 intro、memory、flow、尾段四处重复。

### D. 与当前 scene 无关的状态解释

1. parked 规则出现在 `materials_ready`；
2. dispatch 规则出现在 `protocol_repair`；
3. waiting-state 细节出现在普通 `resume_event`；
4. queue hygiene 出现在非 closeout / non-dispatch scene。

## 保留清单

以下内容必须保留，但要尽量短、尽量 machine-like：

1. `canonical_head_check`
2. `Evidence-first loop`
3. 绑定路径块
4. `inline microstep` vs `async` 的最小判定
5. footer contract 五行
6. continuous mode 下三个关键出口：
   - `PARKED_IDLE`
   - `MATERIALS_READY_FOR_PROPOSAL`
   - `NEW_TASKS_STARTED`

## 正向措辞替换表

| 旧写法 | 风险 | 建议替换 |
| --- | --- | --- |
| `若确认当前是 parked continuity ... 请输出 PARKED_IDLE。` | 焦点落在停机 | `先完成当前回合还能吸收的本地动作；当这些动作已做完且没有新 evidence/live task 时，再用 PARKED_IDLE 作为兜底状态。` |
| `只有在出现新的证据对象...才新建 proposal...` | 像层层设门 | `默认继续在当前 proposal/history 中完成同上下文 writeback；当确实出现新证据对象、独立分支或 async 生命周期需求时，再升级为新 proposal / closeout / async task。` |
| `不要为证明推进而扩动作` | 直接打击推进姿态 | `先吸收已落盘 evidence，再决定下一步；优先做当前上下文内能完成的 bounded local action。` |

## 状态重叠记录

以下状态目前只做 prompt 降噪，不急着改状态机：

1. `PARKED_IDLE` 的 parked 兜底露出是否仍然过多
2. live wait 是否都已统一归 `WAITING_ON_ASYNC`
3. `ANALYZING_NEW_EVIDENCE` vs `LOCAL_MICROSTEP_BATCH`
4. `MATERIALS_READY_FOR_PROPOSAL` vs continuous transition dispatch-ready

## 当前建议的最小实现顺序

### Step 1. 共享块去重

1. 让 `canonical_head_machine` 与 `binding_once` 变成严格单实例 block；
2. 精简 `compact_binding_execution_sections(...)`；
3. 把 footer 之外的重复治理文案移除。

### Step 2. scene 优先级重排

1. `parked_watchdog`
2. `continuous_research`
3. `resume_event`
4. `queued_batch`
5. `standard_followup`

### Step 3. 正向化文案

1. 删除显眼负向文案；
2. 把 parked / waiting 语句整体后移；
3. 把“只有在……才……”优先改写成“默认……；当……时再……”。

## 验收口径

重构后，应至少满足：

1. 新 prompt 中不再出现旧负向短语；
2. `parked_watchdog` 与 `continuous_research` 默认先给本地动作，不先给等待态；
3. 单个 prompt 中 `canonical_head` 与 binding 各只出现一次；
4. agent 在无 live task 时，更倾向完成 evidence sweep / writeback / successor skeleton，而不是 1 个 microstep 后立即 parked。
