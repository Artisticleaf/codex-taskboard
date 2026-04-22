# Docker 自动科研手册

## 1. 什么时候需要这份手册

如果容器用户不只是想“提交并排队”，而是想让任务完成后自动回传到某个 Codex 会话，并继续推进 proposal 链路，那么你需要这份说明。

这是比 result-only 更高权限的模式。

### 轻量自动科研口径

这里默认使用 `taskboard-light` 思路：

- 后台 prompt 只保留 proposal/history、证据吸收、下一步动作和协议尾部这些最小必要信息。
- 防呆主要依赖 taskboard 的生命周期能力，例如 `pending_feedback`、continuous mode、`bind-before-launch`、`attach-pid` 和 `migrate-session`。
- 如果容器侧不需要自动回传，请优先退回 result-only，而不是把自动科研 prompt 堆得更长。

## 2. 前提条件

API token 必须满足：

- `allow_submit_job=true`
- `allow_session_feedback=true`

如果任务会用：

- `codex_exec_mode=dangerous`

还必须满足：

- `allow_dangerous_codex_exec=true`

此外还需要：

- 提交请求中带 `codex_session_id`
- 该 `codex_session_id` 能在绑定 executor 的 Codex home 中被验证到

否则 taskboard 会拒绝该请求。

## 3. proposal 的推荐绑定策略

### 主链任务

主训练、主分析、主实验编排任务应该显式传 `proposal`。

推荐做法：

- proposal 由主链任务持有
- 主链任务写结果、分析和下一步计划
- 后续同链任务自动继承 proposal

### 辅助任务

辅助任务包括：

- watcher
- eval
- log 整理
- 数据准备
- 外挂分析脚本

推荐做法：

- 默认沿用主 proposal
- 作为 sidecar 回写结论
- 不单独创造一份平行 proposal

### 何时不继承

只有当某个辅助任务和主实验链完全无关时，才建议显式清空 proposal 继承。

## 4. API 提交字段

`POST /submit-job` 的 JSON 中可以传：

- `task_id`
- `task_key`
- `workdir`
- `command`
- `feedback_mode`
- `codex_session_id`
- `proposal` 或 `proposal_path`
- `closeout_proposal_dir`
- `gpu_slots`
- `cpu_threads`
- `depends_on`
- `task_note`

其中：

- `feedback_mode != off` 时，必须显式提供 `codex_session_id`
- `proposal` 用于把结果链路绑定到某个规划文件
- `closeout_proposal_dir` 用于统一约束 close-out 和 proposal 的落盘目录，建议后续任务继续显式传同一路径
- 如果同一 `codex_session_id + proposal_path + command` 已经存在 `queued/submitted/running/watching` 任务，API 默认会拒绝这次提交；只有在调用方已经核对 authoritative task 之后，才应显式传 `allow_duplicate_submit=true`

## 5. 示例

```bash
curl -sS \
  -H "Authorization: Bearer $CODEX_TASKBOARD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -X POST \
  "$CODEX_TASKBOARD_API_URL/submit-job" \
  -d '{
    "task_id": "docker-qwen-main",
    "workdir": "/workspace/P01_curiosity_grpo",
    "command": "python train.py --config configs/qwen.yaml",
    "feedback_mode": "auto",
    "codex_session_id": "019d29e8-b672-70e0-b60c-bb354d1994d8",
    "proposal": "/home/Awei/LLM/passage/projects/P01_curiosity_grpo/experiments/PLAN-QWEN3-14B-AUTOPILOT-LIVING-20260329.md",
    "closeout_proposal_dir": "/home/Awei/LLM/passage/projects/P01_curiosity_grpo/closeout_proposal",
    "gpu_slots": 4
  }'
```

如果返回 `Duplicate submit guard`，先查看 `/tasks` 或宿主机侧 `codex-taskboard status --json` 里当前 proposal 的 live task；确认需要保留重复副本时，再把 `allow_duplicate_submit=true` 加回请求体。

## 6. continuous mode 下的回传含义

当宿主机打开了 continuous research mode：

- `TASKBOARD_SIGNAL=WAITING_ON_ASYNC` 表示当前已有 live task，taskboard 只负责托管并按节奏提醒 agent 回来确认实验没有卡住；
- `TASKBOARD_SIGNAL=CLOSEOUT_READY` 表示 execution 已经明确写出“继续当前 proposal 没有新的信息收益”，taskboard 会进入 closeout；
- `TASKBOARD_SIGNAL=none` 表示当前 closeout 已完成，taskboard 会继续引导下一轮 planning。

## 7. 辅助进程和 proposal 的关系

推荐协作约定如下：

- 主链实验任务负责“拥有” proposal。
- 辅助进程默认继承 proposal，但视作 sidecar。
- sidecar 只负责把局部观察并回 proposal，不负责分叉出第二套总规划。
- 如果 sidecar 发现足以开启新方向的证据，应先让主链或新的主任务写出新 proposal，再切出新链。

这样做的好处：

- proposal 不会在多个辅助进程之间发生漂移
- 结果文档与实验计划能保持单线叙事
- taskboard 自动继承 proposal 时更不容易绑错

## 8. 注意事项

- 不要手工从 `list-threads` 猜 `codex_session_id`。
- 如果容器环境本身没有当前会话上下文，自动科研任务应由宿主机上、已知会话上下文的 agent 发起，或者由上游显式传入正确 session。
- 对于没有必要自动回传的长训练，优先使用 result-only token。

## 9. 一句话建议

把 proposal 看成“主链科研叙事文件”，辅助进程都围绕它服务；只有当新方向被正式确认时，才创建新的 proposal 和新链路。
