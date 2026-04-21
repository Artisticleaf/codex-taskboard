# Docker 结果回收手册

## 1. 适用模式

这份手册适用于最稳妥的容器接入方式：

- 容器只负责提交任务
- 容器不绑定 Codex 会话
- 任务完成后通过 API 查询结果
- 不参与自动 followup 和自动科研链路

这也是默认推荐模式。

### 为什么它最省心

如果你只是想稳妥地提交和取回结果，result-only 就是最轻量的防呆模式：

- 不需要绑定 Codex 会话。
- 不会触发自动 followup prompt。
- 不会因为 429、会话迁移或人工接管而影响结果回收。

只有在你明确需要“结果完成后自动唤回 agent 继续 proposal/history 链路”时，才升级到自动科研模式。

## 2. token 要求

result-only token 通常配置为：

- `allow_submit_job=true`
- `allow_read_results=true`
- `allow_read_global_queue=true`
- `allow_session_feedback=false`
- `allow_dangerous_codex_exec=false`

这意味着：

- 可以提交任务
- 可以看共享 `/queue`，了解当前排队情况
- 可以查自己的结果
- 不可以把任务绑定到 Codex session

## 3. 提交任务

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  "$CODEX_TASKBOARD_API_URL/submit-job" \
  -d '{
    "task_id": "docker-result-only-demo",
    "workdir": "/workspace/project",
    "command": "python train.py",
    "feedback_mode": "off",
    "gpu_slots": 1
  }'
```

## 4. 查看队列

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  "$CODEX_TASKBOARD_API_URL/queue?limit=30"
```

这里返回的是共享排队视图：

- 会显示当前 `queued/submitted` 任务，便于判断宿主机队列压力
- 对普通 Docker token 默认只返回排队所需的脱敏字段
- 不会因此放开别人的已完成结果

## 5. 获取结果

### 查询当前状态

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  "$CODEX_TASKBOARD_API_URL/status-result?task_id=docker-result-only-demo"
```

### 阻塞等待完成

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  "$CODEX_TASKBOARD_API_URL/wait-result?task_id=docker-result-only-demo&timeout_seconds=3600&poll_seconds=2"
```

## 6. 你会看到什么

结果里通常会包含：

- `task_id`
- `status`
- `phase`
- `result_ready`
- `blocked_reason`
- `executor_name`
- `assigned_gpus`
- `workdir`
- `command`
- `report` 或结构化结果

## 7. 常见报错

### `This API token is result-only and cannot target a Codex session.`

说明你传了：

- `feedback_mode=auto` 或 `manual`
- 或传了 `codex_session_id`

对于 result-only token，这两种都不允许。

### `Task not found`

常见原因：

- 任务还没成功提交
- 你查的是原始 `client_task_id`，但 token 视图里做了 namespace
- 该任务不属于你的 tenant

## 8. 推荐做法

- 容器端默认都走 result-only。
- 只有确实需要自动回传到 Codex 会话时，才升级到自动科研模式。
- 想看宿主机当前排队情况，用 `/queue`。
- 想看自己 tenant 的任务历史与完成结果，用 `/tasks`、`/status-result`、`/wait-result`。
