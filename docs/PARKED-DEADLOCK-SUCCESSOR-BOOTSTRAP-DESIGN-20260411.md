# Parked 死锁拆解与 successor bootstrap 设计

- created_at: `2026-04-11T19:05:00+08:00`
- document_role: `implementation_design`
- target_repo: `/home/ubunut/awei/codex-taskboard`
- target_scope:
  - `src/codex_taskboard/cli.py`
  - `tests/test_followups.py`
  - `tests/test_dashboard.py`
  - `docs/AGENT_WORKFLOW_SPEC_ZH.md`
  - `docs/USER_MANUAL.md`
  - `README.md`

## 1. 问题定义

当前 continuous mode 的 parked 流程已经能做到“不要机械新建 proposal / 任务”，但还存在三类问题：

1. `recent_next_bounded_action` 的取样可能受日志文件名前缀影响，导致 controller 继承到并非最新的 `Next bounded action`；
2. parked 状态与 `continue_local_microstep` 推荐可能同时出现，容易把 `parked waiting` 与 `仍可继续本地规划` 混成一个模糊状态；
3. agent 即使已经在 parked watchdog 中完成了新 family / 新 proposal 的 CPU-only 规划，也缺少一个明确的 machine-readable 中间态去承接“材料已齐、下一步应把 proposal 固化并分发任务”。

这三点叠加后，会形成一种典型死锁：

- 当前 recipe 已 negative closeout，不能继续原路线；
- 系统又没有明确引导 agent 进入“successor hypothesis / proposal bootstrap”；
- agent 于是不断在 `parked` 与“再做一点 local review”之间反复。

## 2. 目标效果

本次改动后的目标不是让 taskboard 自动乱开实验，而是让它在 parked 之后具备一条受控的、CPU-only 的 successor bootstrap 通道。

最终希望达到五个效果：

1. **最近动作读取正确。**
   - controller 优先读取真正最新的时间日志，而不是文件名前缀看起来较新的旧日志。
2. **parked 与 local bootstrap 分层清楚。**
   - 当 session 处于 parked，但最近日志已经明确写出“新 family 设计 / route replanning / proposal 骨架 / 最小 pilot gate”这类 CPU-only 规划动作时，状态面应明确提示“先 materialize successor proposal”，而不是继续混写成 `continue_local_microstep`。
3. **新增明确的中间信号。**
   - agent 在完成 proposal / hypothesis 材料后，可以用 `TASKBOARD_SIGNAL=MATERIALS_READY_FOR_PROPOSAL` 进入下一段 focused followup，而不是被迫在同轮内要么直接 parked、要么直接完成全部 live task 分发。
4. **prompt 鼓励自主推进，但不冗余。**
   - parked watchdog prompt 应明确写出“禁止 parked 死锁”的原则，但只在 parked / proposal-materialization 两个分支中出现一次，不把同一句话重复塞进所有 continuous prompt。
5. **服务热更新后继承旧状态。**
   - API / dispatcher 服务仍继续使用当前 `CODEX_TASKBOARD_HOME`，重启后继承已有 task、followup、continuous-mode 和 session migration 状态，不做破坏性迁移。

## 3. 实现方案

### 3.1 修正最近时间日志的 recency 计算

当前 `project_history_log_candidates()` 先按文件名前缀时间排序，再按 mtime 排序。若文件名时间戳与文档内 `created_at` 不一致，controller 可能拿到错误的“最近动作”。

改法：

1. 新增统一 recency key：
   - 首选日志正文中的 `created_at`
   - 其次文件前缀时间
   - 再次 `mtime`
2. `project_history_log_candidates()` 改为按该 recency key 排序。

效果：

1. `recent_project_history_next_action_hint()` 会优先读到真正最新的日志；
2. parked watchdog 不再被更老但文件名更“新”的日志误导。

### 3.2 为 next action hint 增加 successor bootstrap 语义

在 `parse_project_history_next_action_from_text()` / `recent_project_history_next_action_hint()` 中增加一层轻量语义判断：

1. 识别这类关键词：
   - `新 family`
   - `new family`
   - `successor hypothesis`
   - `hypothesis packet`
   - `route replanning`
   - `proposal 骨架`
   - `proposal bootstrap`
   - `最小 pilot gate`
   - `新 proposal`
2. 将其归类为：
   - `proposal_bootstrap=true`
   - `proposal_bootstrap_reason=<matched_keyword or heuristic>`

注意：

1. 这不是自动开 GPU 的许可；
2. 只是表明“当前 next bounded action 的本质是把新路线材料写出来”。

### 3.3 parked 状态面增加 proposal bootstrap 推荐

在 continuous mode status payload 中新增：

1. `proposal_bootstrap_ready`
2. `proposal_bootstrap_reason`

并调整 `automation_recommendation_for_session()`：

1. 若有 live task，仍然优先 `wait_for_live_task`
2. 若有 pending feedback，仍然优先 `absorb_completed_receipt`
3. 若处于 parked 且 `proposal_bootstrap_ready=true`，返回：
   - `materialize_successor_proposal`
4. 若收到 `MATERIALS_READY_FOR_PROPOSAL` 后、下一步应把 proposal 绑定并发起 live task，则返回：
   - `finish_proposal_dispatch`
5. 其他情况才走：
   - `continue_local_microstep`
   - `wait_for_external_evidence`
   - `dispatch_parked_watchdog`

额外约束：

1. parked 不应因为存在 `recent_next_bounded_action` 就在状态面上被“抹掉”；
2. 也就是说，waiting state 仍保持 parked，但 recommendation 可以提示“下一步是 materialize successor proposal”。

### 3.4 为 `MATERIALS_READY_FOR_PROPOSAL` 增加专用 followup 分支

当前 `MATERIALS_READY_FOR_PROPOSAL` 只是被归到 local microstep batch，没有专用后续行为。

改法：

1. 在 followup 处理里识别 `TASKBOARD_SIGNAL=MATERIALS_READY_FOR_PROPOSAL`
2. 为 continuous mode 安排一个短延迟 focused followup
3. 该 followup 使用新的 prompt 分支，目标不是重新 parked，而是：
   - 固化 proposal / history
   - 显式绑定 proposal
   - 提交至少一条下一阶段 live task
   - 完成后输出 `NEW_TASKS_STARTED`

这样形成清晰链条：

1. `PARKED_IDLE`
2. watchdog 提醒 agent 先做 CPU-only successor bootstrap
3. agent 写出材料后输出 `MATERIALS_READY_FOR_PROPOSAL`
4. taskboard 再推进到 focused materialization followup
5. agent 绑定 proposal 并分发任务
6. 输出 `NEW_TASKS_STARTED`

### 3.5 精简 parked prompt，显式加入“禁止 parked 死锁”

在 `build_parked_watchdog_prompt()` 中保留当前 evidence-first 结构，但收束成三件事：

1. 明确说明 parked watchdog 不是要求机械扩实验；
2. 明确说明：如果当前 family 已 negative closeout，且最近日志已给出 successor 方向，那么“写新 family 设计 / route replanning / proposal 骨架”就是合法 bounded local action；
3. 明确说明：如果你已经完成这些材料，不要再 immediately parked，改用 `MATERIALS_READY_FOR_PROPOSAL`。

为避免 prompt 冗余：

1. “禁止 parked 死锁”的强表达只放在 parked watchdog prompt；
2. `build_continuous_research_prompt()` 的普通 followup 只保留一句简短提醒，不再整段重复。

### 3.6 新增 focused materialization prompt

新增一个轻量 prompt builder，专门处理 `MATERIALS_READY_FOR_PROPOSAL`：

1. 输入：
   - 当前 proposal/history canonical head
   - closeout_proposal_dir
   - recent next bounded action
2. 输出要求：
   - 不再复读 parked 协议；
   - 直接把 proposal 材料固化为新 proposal / history；
   - 若 async task 已提交则写 `WAITING_ON_ASYNC` 或最终 `NEW_TASKS_STARTED`；
   - 若 live task 尚未提交，不允许再次输出 `MATERIALS_READY_FOR_PROPOSAL` 无限自旋。

## 4. 测试矩阵

### 4.1 单元测试

#### A. 日志 recency

1. 新增测试：当两个日志文件名前缀顺序与正文 `created_at` 冲突时，`recent_project_history_next_action_hint()` 仍选正文时间更晚的那一份。

#### B. bootstrap hint

1. 新增测试：`Next bounded action` 包含 `proposal 骨架` / `新 family 设计` 等词时，hint 返回 `proposal_bootstrap=true`。

#### C. continuous status

1. 新增测试：session 处于 parked 且 bootstrap-ready 时，status payload 返回：
   - `proposal_bootstrap_ready=true`
   - `automation_recommendation=materialize_successor_proposal`
2. 回归测试：普通 parked 且没有 bootstrap-ready 时，仍为 `wait_for_external_evidence` 或 `dispatch_parked_watchdog`。

#### D. parked prompt

1. 新增测试：parked watchdog prompt 包含：
   - “禁止 parked 死锁”或等价表达
   - `MATERIALS_READY_FOR_PROPOSAL`
   - successor bootstrap 合法动作
2. 回归测试：prompt 长度仍受控，不回退到旧的冗长模板。

#### E. followup dispatch

1. 新增测试：agent 输出 `MATERIALS_READY_FOR_PROPOSAL` 后，会安排 focused materialization followup；
2. 回归测试：输出 `PARKED_IDLE` 仍保持 parked；
3. 回归测试：输出 `NEW_TASKS_STARTED` 仍按现有 continuous transition 收口。

### 4.2 集成/命令级测试

计划执行：

1. `tests/test_followups.py`
2. `tests/test_dashboard.py`
3. 必要时补跑受影响的 CLI prompt / signal 解析测试

### 4.3 上线前验收

1. `codex-taskboard doctor`
2. `codex-taskboard continuous-mode status --json`
3. `codex-taskboard current-thread`
4. 使用现有 state 目录检查：
   - old task records 仍可见
   - continuous-mode session 绑定未丢
   - parked session 的 waiting state 未丢

## 5. 上线与继承方案

当前服务形态：

1. API：
   - `/etc/systemd/system/codex-taskboard-api.service`
   - `ExecStart=/home/ubunut/awei/codex-taskboard/.venv/bin/codex-taskboard serve-api --bind 0.0.0.0 --port 8765`
2. dispatcher：
   - `/etc/systemd/system/codex-taskboard-dispatcher.service`
   - `ExecStart=/home/ubunut/awei/codex-taskboard/.venv/bin/codex-taskboard serve --mode gpu-fill --gpu-count 4 --cpu-thread-limit 40 --poll-seconds 5`

由于 `.venv` 当前直接导入仓库里的 `src/codex_taskboard/cli.py`，因此上线步骤可以保持轻量：

1. 修改源码；
2. 运行测试；
3. 执行 `systemctl restart codex-taskboard-api.service codex-taskboard-dispatcher.service`
4. 用现有 `CODEX_TASKBOARD_HOME=/home/ubunut/.local/state/codex-taskboard` 做状态继承

这意味着：

1. 不需要迁移数据库或状态目录；
2. 重启后会继续继承旧 tasks / followups / continuous mode / human-guidance / session migration；
3. 新旧服务的“继承”通过共用状态目录与相同的 executable entry 完成。

## 6. 与项目 proposal 改写的接口

taskboard 改造完成后，项目侧应利用新能力做两件事：

1. 不再把当前 route-1 parked 解释成“默认无限等待”；
2. 直接基于现有证据重写 mainline / 小主线：
   - 若证据支持 route-level 改写，则修正大主线；
   - 若证据更适合作为新 hypothesis family，则显式新建小主线 proposal；
   - 然后用更新后的 taskboard 分发至少一条真实任务。

这一步不属于 taskboard 代码本身，但会作为上线后的立即验收场景。
