# codex-taskboard 管理员手册

## 1. 目标

这份手册给维护宿主机 taskboard 的管理员使用，内容覆盖：

- 本地安装与升级
- systemd 常驻服务
- API 与 Docker 接入
- continuous research mode 持久化状态
- 日志与常见排障

## 2. 关键路径

默认路径由 CLI 参数或环境变量决定。

- 仓库目录：`/home/Awei/codex-taskboard`
- 默认 `CODEX_TASKBOARD_HOME`：`/home/<user>/.local/state/codex-taskboard`
- 默认 `CODEX_HOME`：`/home/<user>/.codex`

常见状态文件：

- `tasks/`: 每个任务的运行目录
- `locks/`: 调度锁、session 锁
- `continuous_research_mode.json`: 持续推进科研模式开关
- `human_guidance_mode.json`: 人工干预暂停租约
- `session_migrations.json`: session cutover 记录、缓冲回流摘要
- `active_feedback_runtime.json`: taskboard 自己正在执行的回流子进程登记
- `executors.json`: executor 注册表
- `api_tokens.json`: API token 注册表
- `api-server.log`: API 服务日志

## 3. 安装或升级

```bash
cd /home/Awei/codex-taskboard
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
.venv/bin/codex-taskboard doctor
```

升级后建议跑一轮最小回归：

```bash
PYTHONPATH=src .venv/bin/python -m py_compile \
  src/codex_taskboard/cli.py \
  tests/test_api_security.py \
  tests/test_followups.py \
  tests/test_session_migration.py \
  tests/test_queue_policy.py \
  tests/test_dashboard.py
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_api_security \
  tests.test_followups \
  tests.test_session_migration \
  tests.test_queue_policy \
  tests.test_dashboard
```

### 安装路径约定

管理员侧请把下面这条约定当作单一事实来源：

- 真实程序入口：`/home/Awei/codex-taskboard/.venv/bin/codex-taskboard`
- 真实代码来源：`/home/Awei/codex-taskboard/src/codex_taskboard/cli.py`
- `/usr/local/bin/codex-taskboard` 如果存在，应当只是一个 shell wrapper，用来转发到仓库 `.venv/bin/codex-taskboard`

这意味着：

- “看到两个路径”不等于“装了两份 taskboard”
- 只要 `/usr/local/bin/codex-taskboard` 最终 `exec` 到仓库 `.venv/bin/codex-taskboard`，它就只是一个快捷入口
- 真正需要避免的是再装一个独立的全局 Python 包，或者让 systemd 指到另一个虚拟环境

建议管理员用下面三条命令核对：

```bash
which -a codex-taskboard
head -n 5 /usr/local/bin/codex-taskboard
.venv/bin/python - <<'PY'
import codex_taskboard.cli
print(codex_taskboard.cli.__file__)
PY
```

只要最后一条打印的是仓库里的 `src/codex_taskboard/cli.py`，就说明当前命令入口和服务代码来源是一致的。

如果改动涉及 `resume` / `followup` / continuous prompt，请把下面这些约定当作必须守住的回归边界：

- `resume`、标准 `followup`、continuous prompt 默认发 `compact` profile；测试必须同时检查关键锚点仍在，以及 `训练执行规范：`、`项目发展史维护要求：`、`proposal binding guard：` 这类 full 块没有被重新带回后台 prompt。
- `resume` 的截断测试必须保证尾部仍保留 `安全说明：` 和 `后续动作指令：`，避免长 prompt 把真正执行动作截掉。
- queued feedback batch 必须继续采用“共享治理头 + 分任务块”，不要让每个 batch item 再复制一遍长治理 prompt。
- continuous anti-stall 至少覆盖三类 edge case：已有 live running task 时不再补发 idle reminder；已有其他 followup 时 defer 新 reminder；连续 followup 却没有 queue hygiene 时必须触发 attention。
- session cutover 必须覆盖：旧 session 回流被精确中断、非隐藏 task 记录迁到新 session、followup/queued feedback 重绑、continuous/human-guidance 迁移、以及缓冲状态写入 `session_migrations.json`。

现有锚点测试集中在：

- [`tests/test_api_security.py`](/home/Awei/codex-taskboard/tests/test_api_security.py)
- [`tests/test_followups.py`](/home/Awei/codex-taskboard/tests/test_followups.py)
- [`tests/test_session_migration.py`](/home/Awei/codex-taskboard/tests/test_session_migration.py)
- [`tests/test_queue_policy.py`](/home/Awei/codex-taskboard/tests/test_queue_policy.py)

## 4. systemd 常驻服务

仓库当前提供两个 systemd 单元模板：

- [`extras/systemd/codex-taskboard-dispatcher.service`](/home/Awei/codex-taskboard/extras/systemd/codex-taskboard-dispatcher.service)
- [`extras/systemd/codex-taskboard-api.service`](/home/Awei/codex-taskboard/extras/systemd/codex-taskboard-api.service)

### dispatcher 是什么

dispatcher 的核心调度循环仍然是：

```bash
codex-taskboard serve --mode gpu-fill --gpu-count 4 --cpu-thread-limit 40 --poll-seconds 5
```

但生产常驻入口现在统一改成：

```bash
codex-taskboard service run dispatcher --mode gpu-fill --gpu-count 4 --cpu-thread-limit 40 --poll-seconds 5
```

这个托管入口会先做实例锁、遗留 pid 文件清理和 runtime 记录，再进入真正的 `serve` 循环。

作用：

- 周期性扫描队列
- 依据 GPU/CPU 资源和依赖关系启动可运行任务
- 让整个 taskboard 在无人值守时也会持续 dispatch

它不是 dashboard，也不是 API server，而是“调度循环本体”。

### 安装 systemd 单元

```bash
sudo cp extras/systemd/codex-taskboard-dispatcher.service /etc/systemd/system/
sudo cp extras/systemd/codex-taskboard-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now codex-taskboard-dispatcher.service
sudo systemctl enable --now codex-taskboard-api.service
```

服务路径约定：

- systemd 单元的 `ExecStart` 应直接写仓库 `.venv/bin/codex-taskboard service run ...`
- 不要把 service 指到另一个 Python 环境
- 也不要把 `python src/codex_taskboard/cli.py ...` 当成生产常驻入口
- `/usr/local/bin/codex-taskboard` 可以保留给人手工调用，但生产服务应优先直连 `.venv/bin/codex-taskboard`，这样排障时最透明
- 改完 unit 或怀疑 pid 漂移时，优先运行 `.venv/bin/codex-taskboard service doctor` 做一致性检查

### 查看状态

```bash
systemctl status codex-taskboard-dispatcher.service
systemctl status codex-taskboard-api.service
journalctl -u codex-taskboard-dispatcher.service -f
journalctl -u codex-taskboard-api.service -f
.venv/bin/codex-taskboard service doctor
```

## 5. continuous research mode 管理

### 查看

```bash
.venv/bin/codex-taskboard continuous-mode status
```

### 开启

```bash
.venv/bin/codex-taskboard continuous-mode on --session-id <codex_session_id>
```

### 关闭

```bash
.venv/bin/codex-taskboard continuous-mode off --session-id <codex_session_id>
```

### 绑定新的默认 session

```bash
.venv/bin/codex-taskboard continuous-mode bind --session-id <new_codex_session_id>
```

### 清理旧 session 的 continuous 状态

```bash
.venv/bin/codex-taskboard continuous-mode clear-session --session-id <old_codex_session_id>
.venv/bin/codex-taskboard continuous-mode clear-all
```

### continuous 收口转下一阶段

continuous mode 打开时，`TASKBOARD_SIGNAL=NO_FURTHER_TASKS`、`STOP_AUTOMATION`、`END_EXPERIMENT` 都不会直接停机，而会触发一条更强的“收口转下一阶段” followup。管理员需要知道这条 followup 的完成条件已经变成：

- agent 已写出论文级 closeout markdown；
- `project_history_file` 已吸收当前 proposal 的关键结论；
- 新 proposal 已生成，且必须是“分阶段、分决策分支、带实现细节的实验规划书”；
- 至少一条下一阶段 live task 已经真正提交；
- agent 明确返回 `TASKBOARD_SIGNAL=NEW_TASKS_STARTED`，并且 `LIVE_TASK_STATUS=submitted|awaiting`。

如果缺少上述显式信号，taskboard 会继续保留这条转场 followup，而不是把自动科研链判成已结束。

持久化文件：

```text
$CODEX_TASKBOARD_HOME/continuous_research_mode.json
```

字段包括：

- `default_codex_session_id`
- `updated_at`
- `updated_by`
- `source`
- `sessions`

## 5.1 管理员手动 session cutover

当旧 session 在 VSCode/Codex 插件中已经出现不可恢复的渲染/滚动异常，但 taskboard 仍需继续自动推进科研时，使用：

```bash
.venv/bin/codex-taskboard migrate-session \
  --from-session-id <old_codex_session_id> \
  --to-session-id <new_codex_session_id>
```

可选预演：

```bash
.venv/bin/codex-taskboard migrate-session \
  --from-session-id <old_codex_session_id> \
  --to-session-id <new_codex_session_id> \
  --dry-run
```

行为语义：

- 这是一次真实 cutover，不只是改默认 session。
- 所有非隐藏 task 记录都会把 `codex_session_id` 改到新 session。
- followup、queued feedback、continuous mode、human-guidance 的绑定会一起迁走。
- taskboard 会精确中断自己发往旧 session 的回流子进程；这些被打断的项会写入 `session_migrations.json` 的 `buffered_runtime_entries`，并把任务状态标成 `buffered_session_migration_cutover`，提醒后续由新 session 接手。
- 迁移完成后，旧 session 的 redirect 记录会保留在 `session_migrations.json`，这样即使有残留旧绑定，后续 wakeup 也会继续落到新 session。

## 6. API 服务

API 服务由 `serve-api` 提供，当前默认暴露：

- `GET /queue`
- `GET /tasks`
- `GET /status-result`
- `GET /wait-result`
- `POST /submit-job`

非管理员 token 会受到租户隔离：

- 任务 ID 会自动 namespaced
- `/tasks`、`/status-result`、`/wait-result` 默认只能看见自己 tenant 的任务
- 若 token 显式开启 `allow_read_global_queue=true`，则 `/queue` 可查看共享排队视图；该视图默认只返回排队所需的脱敏字段，不暴露 `workdir` / proposal 路径等运行细节
- result-only token 不能绑定 Codex session
- `/submit-job` 默认带 duplicate-submit guard：如果同一 `codex_session_id + proposal_path + command` 已有 live task，API 会拒绝重复提交；只有在调用方确认 authoritative task 后，才应显式传 `allow_duplicate_submit=true`

## 7. API token 注册

注册表路径：

```text
$CODEX_TASKBOARD_HOME/api_tokens.json
```

最小示例：

```json
{
  "tokens": [
    {
      "token_hash": "sha256_of_secret",
      "tenant": "ju-rootless",
      "executor": "ju-rootless",
      "role": "user",
      "default_feedback_mode": "off",
      "agent_name": "docker:ju-rootless",
      "allow_submit_job": true,
      "allow_read_results": true,
      "allow_read_global_queue": true,
      "allow_session_feedback": false,
      "allow_dangerous_codex_exec": false
    }
  ]
}
```

说明：

- 推荐只存 `token_hash`，不要存明文 token。
- `allow_session_feedback=false` 表示这是 result-only token。
- `allow_read_global_queue=true` 表示该 token 可以通过 `/queue` 查看共享排队视图；这不会放开别人的完成结果。
- 只有允许 `allow_session_feedback=true` 且能解析目标 session 时，才允许 API 提交自动回传任务。

## 8. Executor 注册

注册表路径：

```text
$CODEX_TASKBOARD_HOME/executors.json
```

适合 SSH 到其他用户、容器映射目录或远程环境。关键字段通常包括：

- `ssh_target`
- `remote_workdir`
- `remote_workdir_prefix`
- `remote_codex_home`
- `remote_codex_bin`
- `host_gpu_ids`
- `remote_gpu_ids`

Docker 用户通过 API 提交任务时，如果 token 绑定了 executor，提交会自动套用该 executor。

## 9. 日志与排障

### 看主状态

```bash
.venv/bin/codex-taskboard dashboard --render-mode plain --once
```

### 看单任务结果

```bash
.venv/bin/codex-taskboard status --task-id <task_id>
```

### 看当前线程识别

```bash
.venv/bin/codex-taskboard current-thread --json
```

### 常见问题

#### API 能提交，但容器里看不到 dashboard

这是预期。dashboard 是宿主机上的终端 TUI。容器端应改用 API 的 `/queue` 和 `/tasks` 查看排队情况。

#### agent 输出了 `NO_FURTHER_TASKS` 但流程还继续

检查 `continuous_research_mode.json` 或执行：

```bash
.venv/bin/codex-taskboard continuous-mode status
```

#### 自动回传积压

先看是否处于 `pending_feedback`。现在同一 session 的回传会串行化，且会在会话忙、429、最近活动等情况下延后重试，而不是硬撞线程。

## 10. 运维建议

- 主机上的科研主链建议显式绑定 proposal。
- 开启 continuous mode 前，先确认 agent prompt 已经理解“真正停机信号只有 `STOP_AUTOMATION` / `END_EXPERIMENT`”。
- 容器用户默认应走 result-only token，确有需要再放开 session feedback。
- 生产环境优先使用 systemd 托管 `dispatcher` 和 `api`，不要靠临时 shell 常驻。
