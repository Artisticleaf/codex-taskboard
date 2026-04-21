# Docker 用户手册

## 1. 适用场景

如果你在 Docker 容器里，希望把任务提交到宿主机上的同一个 `codex-taskboard` 队列，并让宿主机统一调度 GPU/CPU 资源，这份手册就是给你的。

这类用法的关键点：

- 容器内不需要自己运行 dispatcher。
- 容器内只需要访问宿主机暴露出来的 taskboard API。
- 提交的任务会进入宿主机的统一队列。
- 默认推荐用“非绑定 agent”的 result-only 模式。

## 2. 你能做什么

容器用户可以通过 API：

- 提交任务
- 查看当前排队和运行情况
- 查询某个任务结果
- 等待某个任务完成

## 3. 不能直接做什么

宿主机上的 `dashboard` 是终端 TUI，不是网页。

因此：

- 容器里通常不能直接看宿主机的 curses dashboard。
- 想看排队情况，请改用 API 的 `/queue` 和 `/tasks`。

## 4. 连接信息

容器中通常需要两项：

- `CODEX_TASKBOARD_API_URL`
- `CODEX_TASKBOARD_API_TOKEN`

例如：

```bash
export CODEX_TASKBOARD_API_URL=http://host.docker.internal:8765
export CODEX_TASKBOARD_API_TOKEN=your-secret-token
```

如果容器环境没有 `host.docker.internal`，请替换成宿主机可达 IP。

## 5. 查看队列

### 看共享排队视图

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  "$CODEX_TASKBOARD_API_URL/queue?limit=30"
```

说明：

- `/queue` 面向“当前宿主机队列里有什么任务正在排队”
- 对开启了 `allow_read_global_queue=true` 的普通 Docker token，会显示共享的 `queued/submitted` 任务
- 返回字段默认是脱敏的排队视图，不会暴露别人的 `workdir`、proposal 路径等细节

### 看当前 token 可见任务

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  "$CODEX_TASKBOARD_API_URL/tasks?status=all&sort=queue&limit=50"
```

可用 `status` 过滤：

- `all`
- `active`
- `queued`
- `attention`
- `pending`
- `done`

说明：

- `/tasks` 默认仍是当前 tenant 视图
- 普通 Docker token 可以看见自己的排队、运行中和已完成任务
- 已完成任务不会因为开启共享 `/queue` 而放开到其他 tenant

## 6. 提交一个不绑定 agent 的任务

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  "$CODEX_TASKBOARD_API_URL/submit-job" \
  -d '{
    "task_id": "docker-cpu-demo",
    "workdir": "/workspace/project",
    "command": "python train.py --cpu-only",
    "feedback_mode": "off",
    "cpu_threads": 8,
    "hold": false
  }'
```

如果 token 不是管理员，taskboard 会自动给任务 ID 加 tenant 前缀，进入共享队列但保持租户隔离可见性。
如果该 token 另外启用了 `allow_read_global_queue=true`，它还可以通过 `/queue` 查看共享排队视图，但已完成结果仍只允许看自己的 tenant。

## 7. 查询结果

### 立即查询

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  "$CODEX_TASKBOARD_API_URL/status-result?task_id=docker-cpu-demo"
```

新版本的 `status-result` / `wait-result` 除了基础 `status` 外，还可能返回这些结构化字段：

- `lifecycle_state`
- `runtime_state`
- `dispatch_diagnostics`
- `platform_recovery`
- `automation_recommendation`

建议解释方式：

1. 先看 `status` / `lifecycle_state`
2. 再看 `runtime_state`
3. 若任务异常、等待重试或看起来“完成但仍未回流”，再看 `dispatch_diagnostics` 与 `platform_recovery`

其中 `platform_recovery` 很重要：

- 如果它显示的是 `429`、provider `5xx`、transport transient 等平台侧问题，
- 你应把它当成“等待平台恢复”或“等待自动重试”，
- 而不是当成科学实验本身的负证据。

### 等待完成

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  "$CODEX_TASKBOARD_API_URL/wait-result?task_id=docker-cpu-demo&timeout_seconds=3600&poll_seconds=2"
```

### 新版机器可读状态字段

近期版本的 `/status-result`（以及 `/tasks` 列表中的任务项）会附带更细的状态字段，建议外部系统按下面口径解释：

- `status`
  - 面向用户的粗粒度状态，例如 `queued`、`running`、`completed`。
- `lifecycle_state`
  - 更细的生命周期状态，例如 `running`、`awaiting_feedback`、`completed`。
- `runtime_state`
  - 当前运行子状态，例如 `child_live`、`awaiting_feedback`、`none`。
- `dispatch_diagnostics`
  - 调度器侧的历史判定快照。任务已启动后，这里可能仍保留 launch 前的调度信息，不应用它单独判断“任务还没跑起来”。
- `launch_diagnostics`
  - 真实 launch/finish 视角的快照，更适合确认任务是否已经真正开始或结束。
- `platform_recovery`
  - 平台级自动恢复状态，例如 429 / provider transient / transport retry。
- `automation_recommendation`
  - taskboard 当前建议的自动化动作，例如 `wait_for_live_task`、`absorb_completed_receipt`。

推荐解释规则：

- 判断任务是否仍在运行，优先看 `lifecycle_state`、`runtime_state`、`started_at`、`ended_at`。
- 如果 `status=completed`，但 `lifecycle_state=awaiting_feedback`，表示计算已经完成，但该 receipt 仍在等待 agent/上游工作流吸收。
- 如果 `platform_recovery.state != none`，应先按平台/上游错误处理，而不是直接把它吸收到 scientific 结论。
- 不要只依赖旧的 `blocked_reason` / `gpu_block_reason` 来判断 live/blocked，因为这些字段可能只是历史调度快照。

## 8. 使用附带的轻量客户端

仓库里有一个简化客户端：

[`extras/codex_taskboard_client.py`](/home/Awei/codex-taskboard/extras/codex_taskboard_client.py)

它支持：

- `submit-job`
- `queue`
- `tasks`
- `status-result`
- `wait-result`

示例：

```bash
python extras/codex_taskboard_client.py \
  --base-url "$CODEX_TASKBOARD_API_URL" \
  --api-token "$CODEX_TASKBOARD_API_TOKEN" \
  submit-job \
  --task-id docker-demo \
  --workdir /workspace/project \
  --command "python train.py" \
  --feedback-mode off
```

查看共享队列：

```bash
python extras/codex_taskboard_client.py \
  --base-url "$CODEX_TASKBOARD_API_URL" \
  --api-token "$CODEX_TASKBOARD_API_TOKEN" \
  queue \
  --limit 30
```

查看当前 token 可见任务：

```bash
python extras/codex_taskboard_client.py \
  --base-url "$CODEX_TASKBOARD_API_URL" \
  --api-token "$CODEX_TASKBOARD_API_TOKEN" \
  tasks \
  --status done \
  --limit 30
```

说明：

- `queue` 对普通 Docker token 返回共享排队视图
- `tasks` 返回当前 token 可见任务，通常就是当前 tenant 视图
- 如果你需要判断任务是“真正失败”还是“平台暂时阻塞”，请优先查看 `status-result` / `wait-result` 返回里的 `platform_recovery` 与 `automation_recommendation` 字段。

## 9. 关于 GPU 任务

容器即使本身不分配 GPU，也可以通过 taskboard 把“需要 4 卡”的任务提交到宿主机统一排队，只要：

- API token 允许提交任务
- 宿主机 dispatcher 正在运行
- 提交时使用 `gpu_slots`

例如：

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  "$CODEX_TASKBOARD_API_URL/submit-job" \
  -d '{
    "task_id": "docker-4gpu-smoke",
    "workdir": "/workspace/project",
    "command": "torchrun --nproc_per_node=4 extras/smoke/taskboard_torchrun_4gpu_smoke.py",
    "feedback_mode": "off",
    "gpu_slots": 4
  }'
```

任务是否能真正启动，取决于宿主机当时是否空出 4 卡。

## 10. 常见问题

### 为什么我在容器里看不到 dashboard？

因为 dashboard 不是网页界面。容器侧应使用 API 查看队列。

### 为什么查不到别的容器或宿主机任务？

非管理员 token 默认只能看到自己 tenant 名下可见的任务。

### Docker 用户提交的非绑定 agent 任务能不能进入队列？

可以。只要：

- API server 正常运行
- token 允许 `submit-job`
- 请求格式合法

该任务就会进入宿主机的统一 taskboard 队列中。
