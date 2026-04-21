# codex-taskboard 架构分层图与改造计划（2026-04-21）

## 当前分层图

```text
+-------------------------------------------------------------------+
|  CLI / Operator Layer                                             |
|  - codex-taskboard <subcommand>                                   |
|  - systemd units / service doctor / print-systemd                 |
+-------------------------------------------------------------------+
|  API / Presentation Layer                                         |
|  - api_server.py: HTTP transport                                  |
|  - api_views.py: /tasks /queue /status-result /wait-result        |
|  - task_dashboard.py: dashboard list/filter/sort/presentation     |
|  - task_results.py: terminal result/read model                    |
|  - task_payloads.py: task spec/state defaulting and normalization |
+-------------------------------------------------------------------+
|  Access Control / Submit Policy Layer                             |
|  - api_access.py: tenant visibility / queue visibility            |
|  - api_auth.py: token registry + token resolution                 |
|  - api_submit.py: submit payload -> spec + policy enforcement     |
|  - executors.py: executor registry / remote path / GPU remap      |
+-------------------------------------------------------------------+
|  Scheduler / Workflow Layer                                       |
|  - scheduler_resources.py: CPU/GPU placement and runtime shaping  |
|  - automation_state.py: continuous/human-guidance state machine   |
|  - task_storage.py: spec/state/event IO facade                    |
|  - cli.py: dispatch core / launch / followup wiring / commands    |
|  - dispatcher_service.py: long-running dispatch loop shell        |
+-------------------------------------------------------------------+
|  Service Orchestration Layer                                      |
|  - service_manager.py: instance lock / stale pid cleanup /        |
|                        runtime record / drift doctor / unit render |
+-------------------------------------------------------------------+
|  Runtime / Process Introspection Layer                            |
|  - process_runtime.py: /proc inspection / pid snapshot / tmux name|
+-------------------------------------------------------------------+
|  Storage / Index Layer                                            |
|  - task-index.json + task_index.py                                |
|  - tasks/*/{spec.json,state.json,events/...}                      |
|  - followups/*.json                                               |
|  - locks/*.lock                                                   |
+-------------------------------------------------------------------+
|  Runtime / External Layer                                         |
|  - tmux / subprocess / codex exec / docker or podman / GPUs       |
+-------------------------------------------------------------------+
```

## 为什么要继续改造

1. `src/codex_taskboard/cli.py` 仍然过大，调度、dashboard、result read model、followup 状态机还混在一起。
2. `/queue` `/tasks` 已解决热路径扫盘问题，但展示层与结果层仍缺少独立模块边界。
3. 生产服务已经统一到 `service run ...`，但仓库路径、文档路径和 systemd 模板需要与新的 repo 位置同步。
4. 需要让“管理员全功能”和“普通 docker 用户只看共享队列/只看本人完成任务”这条产品边界长期稳定，而不是继续散落在大文件中。

## 本轮改造计划

### P0 已完成 / 本轮继续落地

- [x] task index / metadata cache，降低 `/queue` `/tasks` 热路径全量扫盘。
- [x] API auth / submit / server / views 拆分。
- [x] service manager 落地，解决 pid 漂移和 systemd 失真。
- [x] 将仓库复制到 `/home/Awei/codex-taskboard`，准备切换生产入口。

### P1 本轮新增拆分

- [x] `task_dashboard.py`
  - 抽离 dashboard 的 filter / sort / issue text / entry builder。
  - 目标：把展示层从 `cli.py` 里剥离，减少 API 与 dashboard 共用逻辑的耦合。
- [x] `task_results.py`
  - 抽离 terminal result payload read model。
  - 目标：把 `status-result` / API result payload 的拼装和 CLI 其它调度逻辑解耦。
- [x] `executors.py`
  - 抽离 executor registry、remote workdir 校验、host<->remote GPU 映射。
  - 目标：把 Docker/SSH 执行器契约从 `cli.py` 中剥离，降低 API submit 与远端执行细节的耦合。
- [x] `task_storage.py`
  - 抽离 task path/spec/state/event IO、任务枚举与 index 写回入口。
  - 目标：把磁盘布局和读写语义集中到 storage facade，减少 CLI 到处直接拼路径。
- [x] `automation_state.py`
  - 抽离 continuous-research / human-guidance 的持久化状态机。
  - 目标：把 session 级自动化状态与 followup 调度主流程解耦，降低状态漂移风险。
- [x] `scheduler_resources.py`
  - 抽离 CPU/GPU 预算计算、命令模板渲染、GPU headroom 选择逻辑。
  - 目标：把资源放置策略从 CLI 控制流里拆开，便于单测和后续继续分出 scheduler。
- [x] `task_payloads.py`
  - 抽离 task spec/state 默认字段补齐与时间戳归一化。
  - 目标：把持久化 payload 契约从 `cli.py` 中拿出来，便于 storage/api/dispatcher 共用。
- [x] `process_runtime.py`
  - 抽离 `/proc` 读取、pid 快照与 tmux session 命名。
  - 目标：把 service doctor / cleanup / runtime 观测相关逻辑从 CLI 命令集里拆开，降低耦合。

### P2 下一阶段建议

- [ ] 继续抽离 dispatch / readiness 主循环到 `scheduler.py` / `scheduler_readiness.py`
- [ ] 把 followup queue/coalescing 继续从 `cli.py` 中拆成 `followup_runtime.py`
- [ ] 把 `/status` / `/dashboard` 的剩余 read model 继续压出 `cli.py`
- [ ] 逐步让 `cli.py` 只保留 parser + wiring + thin wrappers

## 本轮验收口径

1. 新 repo 路径下可完整运行测试。
2. 新 repo 路径下可通过真实 docker 用户完成 API smoke。
3. 新 repo 路径下可完成一次真实 `codex exec` taskboard smoke。
4. systemd 单元切换到 `/home/Awei/codex-taskboard/.venv/bin/codex-taskboard service run ...`。
5. `codex-taskboard service doctor` 在切换后恢复为 healthy。
