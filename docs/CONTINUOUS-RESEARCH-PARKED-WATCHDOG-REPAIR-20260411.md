# continuous research parked watchdog 修复设计（2026-04-11）

## 1. 改进目标

本次改造的目标是把 continuous research 在 parked continuity 下的语义真正贯通到调度、状态机、提示词与可观测性四层，避免再次出现“状态显示已 parked backoff，但真实唤醒仍沿用通用 reminder 语义”的部分上线问题。

具体目标如下：

1. parked watchdog 到期后，真实 followup 调度要与 parked backoff 语义一致，不再额外叠加通用 `delay/min_idle`。
2. parked / no-live-task 场景下，即便模型误输出 `WAITING_ON_LIVE_TASK` 或 `WAITING_ON_ASYNC`，也不能把 session 漂移到错误等待态，或继续派生 `waiting_on_async_watchdog`。
3. `continuous-mode status --json` 要同时展示：
   - parked watchdog 的计划唤醒时间；
   - 当前真实 followup 的下一次恢复时间；
   - 两者的 reason / interval / min_idle。
4. parked watchdog prompt 要缩到“只做 bounded self-review”的最小必要集合，避免每轮重复灌入整套重协议。

## 2. 根因拆解

### 2.1 调度语义未贯通

现有实现中，`continuous_session_parked_watchdog_due()` 已能正确计算 parked backoff，但 `ensure_continuous_research_session_reminders()` 在 `parked_watchdog_due=True` 时，仍然统一调用：

- `continuous_session_reminder_delay_seconds()`
- `schedule_continuous_session_reminder(...)`

这会把 parked watchdog 到期后的真实 followup 重新落回通用 idle reminder 语义，继续携带：

- `reason=continuous_research_session_idle`
- `min_idle_seconds=DEFAULT_CONTINUOUS_RESEARCH_MIN_IDLE_SECONDS`
- 通用 `delay_seconds`

结果就是状态层显示“该 session 已经到 15/30/60 分钟 parked backoff”，但真实 followup 仍然会再被通用 min-idle 推迟。

### 2.2 parked 场景缺少 WAITING 信号守卫

`process_single_followup()` 和 `handle_task_feedback()` 中，对 `WAITING_ON_ASYNC_SIGNAL` / `WAITING_ON_LIVE_TASK_SIGNAL` 的处理，主要依据模型输出和 `newer_async_task_exists`，缺少一条更硬的保护：

- 如果当前 followup 本身来自 parked watchdog；
- 当前 session 也没有 live task；
- 也没有新的 async task 作为证据；

那么 `WAITING_ON_LIVE_TASK` / `WAITING_ON_ASYNC` 不应被当成有效等待态继续派生 watchdog。

缺少这条守卫时，parked session 会因为一次错误输出进入：

- `WAITING_ON_LIVE_TASK`
- `WAITING_ON_ASYNC`
- `waiting_on_async_watchdog`

然后又需要后续 queue hygiene 纠正。

### 2.3 parked prompt 仍然过重

`build_continuous_research_prompt()` 虽然为 parked signal 提供了特殊开头，但后面仍然继续拼接：

- `taskboard_protocol_card_lines(...)`
- `taskboard_memory_contract_lines(...)`
- `continuous_research_flow_lines(...)`

这导致 parked watchdog 每轮都在重复灌入大段治理卡、绑定说明与工作流说明，增加上下文负担，也放大了模型把 parked self-review误判成“必须给出某种等待信号”的概率。

### 2.4 status 缺少“真实调度实体”视角

`build_continuous_mode_status_payload()` 当前能给出：

- `parked_watchdog_interval_seconds`
- `parked_watchdog_due`

但不能直接看出：

- 当前 session 是否已经存在真实 followup；
- 该 followup 的 `check_after_ts`；
- 真实 `reason / interval_seconds / min_idle_seconds`；
- 真实下一次恢复时间与 parked 计划时间是否一致。

因此“状态层已修好，但真实调度仍旧错误”的问题不易第一时间暴露。

## 3. 设计修改点

### 3.1 状态机与调度层

1. 新增 parked watchdog 专用 reason：
   - `CONTINUOUS_RESEARCH_PARKED_WATCHDOG_REASON`
2. parked watchdog 到期后，不再走通用 `continuous_session_reminder_delay_seconds()`，而是走专门调度参数：
   - `delay_seconds=0`
   - `interval_seconds=parked_watchdog_interval_seconds`
   - `min_idle_seconds=0`
   - `last_signal` 保留 parked signal
3. 保留 followup type 为 `continuous_session_reminder`，这样现有 followup 分支与 prompt 入口无需重构。
4. 新增 parked waiting-signal guard：
   - 若 followup 来自 parked watchdog；
   - 当前 session 无 live task；
   - 也不存在新的 async/live task 证据；
   - 但模型输出了 `WAITING_ON_LIVE_TASK` / `WAITING_ON_ASYNC`；
   - 则把该轮结果规范化为“重申 parked idle”，直接 resolve followup，不再调度 `waiting_on_async_watchdog`。
5. 同样的守卫也落到 `handle_task_feedback()`，避免 task notification 路径再次派生错误等待态。

### 3.2 prompt 层

1. 为 parked signal 引入单独的 `build_parked_watchdog_prompt(...)`。
2. parked prompt 只保留：
   - watchdog 背景说明；
   - canonical head 检查；
   - evidence-first loop；
   - bounded self-review / inline microstep / parked 退出条件；
   - 最小 binding 信息；
   - 最小 footer contract。
3. 明确写死两条 guardrail：
   - “无 live task 时不要输出 `WAITING_ON_LIVE_TASK`”
   - “无真实 async/live task 时不要输出 `WAITING_ON_ASYNC`”

### 3.3 可观测性层

在 `continuous-mode status --json` 中新增：

- `parked_watchdog_due_ts`
- `parked_watchdog_due_at`
- `next_actual_resume_ts`
- `next_actual_resume_at`
- `next_actual_resume_in_seconds`
- `active_followup_key`
- `active_followup_reason`
- `active_followup_type`
- `active_followup_interval_seconds`
- `active_followup_min_idle_seconds`
- `active_followup_last_signal`

其中：

- `parked_watchdog_due_*` 表示按 parked backoff 语义计算出的计划时间；
- `next_actual_resume_*` 表示当前真实 followup 实体何时会再次恢复 session。

### 3.4 测试矩阵

#### 单元 / 回归测试

1. `tests/test_followups.py`
   - parked prompt 使用精简模板；
   - stale parked idle session 到期后：
     - reason 应为 parked watchdog reason；
     - `min_idle_seconds=0`；
     - `check_after_ts` 为即时或近即时；
   - repeat_count 增长后，真实 followup 的 `interval_seconds` 应与 dynamic backoff 对齐；
   - parked watchdog followup 若误输出 `WAITING_ON_LIVE_TASK`，且无 live task / 无 newer async task：
     - 不应生成 `waiting_on_async_watchdog`；
     - session 仍保持 parked idle；
     - processed action 应体现 guard 被触发。

2. `tests/test_dashboard.py`
   - status 应返回 parked 计划时间；
   - status 应返回真实 active followup 的下一次恢复时间与调度参数；
   - 可区分 parked 计划与真实实体。

#### 关键集成验证

1. 运行 `process_followups()`：
   - fresh parked：不派发 followup；
   - stale parked：派发 parked watchdog followup；
   - stale parked + repeat_count=3：派发 interval=2x 的 parked watchdog followup。
2. parked followup 被处理时：
   - 若输出 parked signal：正常 resolve；
   - 若错误输出 `WAITING_ON_LIVE_TASK`：被 guard 回 parked idle；
   - 若已有真实 live task：仍允许进入 live-task wait 语义。

## 4. 上线验证步骤

1. 先跑窄测试：
   - `tests/test_followups.py`
   - `tests/test_dashboard.py`
2. 再跑一轮更宽的关键测试集，确保 continuous-mode / followup / dashboard 没被回归。
3. 用仓库 `.venv/bin/codex-taskboard` 覆盖当前服务入口并重启对应服务。
4. 用 `continuous-mode status --json --session-id <session>` 验证：
   - `parked_watchdog_due_*`
   - `next_actual_resume_*`
   - `active_followup_reason`
5. 检查 `followups/*.json`：
   - parked due 后的实体 reason 应为 parked watchdog reason；
   - `min_idle_seconds` 应为 0；
   - `interval_seconds` 应与 parked backoff 对齐。
