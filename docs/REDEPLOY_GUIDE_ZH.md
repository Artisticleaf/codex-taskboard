# codex-taskboard 重部署与 Smoke Test 指南

## 1. 适用场景

当你修改了 `codex-taskboard` 代码、文档或 systemd 配置后，建议按这份指南重新部署并做最小验证。

## 2. 重新安装

```bash
cd /home/Awei/codex-taskboard
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
```

## 3. 基础检查

```bash
.venv/bin/codex-taskboard doctor
.venv/bin/codex-taskboard current-thread
.venv/bin/codex-taskboard continuous-mode status
```

## 4. 单元测试

最小必跑：

```bash
PYTHONPATH=src .venv/bin/python -m py_compile \
  src/codex_taskboard/cli.py \
  tests/test_api_security.py \
  tests/test_followups.py \
  tests/test_queue_policy.py \
  tests/test_dashboard.py
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_api_security \
  tests.test_followups \
  tests.test_queue_policy \
  tests.test_dashboard
```

建议回归：

```bash
PYTHONPATH=src .venv/bin/python -m unittest \
  tests.test_resume \
  tests.test_api_security \
  tests.test_queue_policy \
  tests.test_dispatch_serial \
  tests.test_subagent \
  tests.test_cleanup \
  tests.test_legacy
```

如果本轮改动触及 prompt 拼装、queued feedback 或 continuous 调度，再额外检查下面这些测试约定：

- `resume`、标准 `followup`、continuous prompt 默认应维持 `compact` profile。测试既要检查关键锚点存在，也要检查 full 版长块没有被悄悄带回后台 prompt。
- `resume` 的截断逻辑必须保住 `安全说明：` 和 `后续动作指令：` 尾部，不允许因为 prompt 过长把真实下一步动作截掉。
- queued feedback batch 必须继续共享一套治理 header，而不是按任务重复长 prompt。
- continuous anti-stall 必须覆盖 edge case：已有 live running task 不补发 idle reminder；已有其他 followup 时 defer 新 reminder；重复 followup 且缺少 queue hygiene 时触发 attention。

建议重点看：

- [`tests/test_api_security.py`](/home/Awei/codex-taskboard/tests/test_api_security.py)
- [`tests/test_followups.py`](/home/Awei/codex-taskboard/tests/test_followups.py)
- [`tests/test_queue_policy.py`](/home/Awei/codex-taskboard/tests/test_queue_policy.py)

## 5. continuous mode smoke test

为了不污染线上状态，建议使用临时 `app-home`：

```bash
TMP_HOME="$(mktemp -d)"
.venv/bin/codex-taskboard --app-home "$TMP_HOME" continuous-mode status
.venv/bin/codex-taskboard --app-home "$TMP_HOME" continuous-mode on
.venv/bin/codex-taskboard --app-home "$TMP_HOME" continuous-mode off
.venv/bin/codex-taskboard --app-home "$TMP_HOME" dashboard --render-mode plain --once
rm -rf "$TMP_HOME"
```

验证点：

- `status` 默认是 `off`
- `on` 后输出 `continuous_research_mode=on`
- `off` 后输出 `continuous_research_mode=off`
- dashboard 头部和底部能看到 continuous mode 状态

## 6. systemd 重载

如果改了服务文件：

```bash
sudo cp extras/systemd/codex-taskboard-dispatcher.service /etc/systemd/system/
sudo cp extras/systemd/codex-taskboard-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart codex-taskboard-dispatcher.service
sudo systemctl restart codex-taskboard-api.service
.venv/bin/codex-taskboard service doctor
```

查看状态：

```bash
systemctl status codex-taskboard-dispatcher.service
systemctl status codex-taskboard-api.service
```

## 7. API smoke test

先确认 token 注册表不为空，再测：

```bash
curl -sS -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  "$CODEX_TASKBOARD_API_URL/queue?limit=5"
```

如需提交一个最小 CPU 任务：

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  "$CODEX_TASKBOARD_API_URL/submit-job" \
  -d '{
    "task_id": "redeploy-smoke-cpu",
    "workdir": "/tmp",
    "command": "python -c \"print(123)\"",
    "feedback_mode": "off",
    "cpu_threads": 1
  }'
```

## 8. 4 卡 smoke 任务

仓库附带了一个 4 卡 smoke 脚本：

[`extras/smoke/taskboard_torchrun_4gpu_smoke.py`](/home/Awei/codex-taskboard/extras/smoke/taskboard_torchrun_4gpu_smoke.py)

示例：

```bash
.venv/bin/codex-taskboard submit-job \
  --task-id local-4gpu-smoke \
  --workdir /home/Awei/codex-taskboard \
  --command 'torchrun --nproc_per_node=4 extras/smoke/taskboard_torchrun_4gpu_smoke.py' \
  --gpu-slots 4 \
  --feedback-mode off
```

## 9. 发布完成检查单

- 文档已重写为当前行为，没有残留历史事故说明。
- `continuous-mode` 命令可用。
- dashboard `c` 热键可用。
- 当前公开信号只剩 `EXECUTION_READY` / `WAITING_ON_ASYNC` / `CLOSEOUT_READY` / `none`。
- proposal 绑定、自动继承和 sidecar 规则已写入手册。
- `closeout_proposal_dir` 参数、目录继承规则以及中文 close-out/proposal 写作要求已写入手册。
- Docker/API 的 result-only 与自动科研两种模式都已说明。
