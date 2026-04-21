# taskboard-light

这不是一套新调度器，而是现有 `codex-taskboard` 的轻量使用口径：少改状态机，主要把自动唤起 prompt 压成“短而精准”的最小合同。

## 运行时最小合同

当前后台唤起 prompt 默认只保留下面几块：

- 固定“轻度科研约定”；continuous mode 只额外补一句当前目标。
- 仅在 canonical head 异常时才提示 `missing_block / missing_keys`；`ok`、`read_error`、`unbound` 不再反复刷屏。
- 单次出现的绑定路径：`proposal_file`、`closeout_proposal_dir`、`project_history_file`、`project_history_log_dir`。
- 当前回流/任务摘要：状态、关键日志入口、关键 artifact 入口、最小 action hint。
- `Taskboard 操作简介`：只告诉 agent 何时留在当前对话、何时 `submit`、何时 `bind-before-launch` / `attach-pid`、何时进入等待信号。
- 固定协议尾注：`TASKBOARD_PROTOCOL_ACK`、`CURRENT_STEP_CLASS`、`TASKBOARD_SELF_CHECK`、`LIVE_TASK_STATUS`、`FINAL_SIGNAL`。

## 可自定义 prompt 文件

runtime 默认从 `prompts/taskboard_runtime_prompt_zh.toml` 读取文案；也支持下面两个覆盖入口：

- `CODEX_TASKBOARD_PROMPT_FILE=/path/to/taskboard_runtime_prompt_zh.toml`
- `~/.config/codex-taskboard/taskboard_runtime_prompt_zh.toml`

也就是说，scene 路由、footer 解析、长度控制、busy-session defer 这些框架逻辑仍在代码里；“轻度科研约定”、`Taskboard 操作简介`、`Evidence-first`、`安全说明` 这类自然语言内容可以按设备或项目单独调整。

## 新版“轻度科研约定”关注什么

1. 先读 `proposal_file`、`project_history_file` 和本轮回流，再决定下一步；要设计实验时，再对照必要文献和官方文档/推荐参数。
2. 同一认知线程里的本地短工作默认一次做完：结果读取、CPU 审计、数据处理、必要代码修复、proposal/history 写回、必要文献对照。不要把几分钟内能完成的 CPU-only 小步拆成新阶段。
3. 默认先怀疑实现，再解释结果。只要结果异常、和 history/文献/官方文档冲突、日志异常、smoke 失败、OOM 或代码报错，就先诊断代码逻辑、数据契约、数据泄漏、评测污染、split、配置与 run 完整性；没有排查清楚前，不要把结果当成有效结论继续扩实验。
4. 正式 GPU/remote 实验前必须先过 smoke。launch 失败、OOM、明显 bug、参数错误、路径错误、配置错配都属于执行问题，不是科研结论；能在当前对话修掉的就直接修掉，不要包装成单独实验阶段。
5. 先做对，再做快。正式 GPU 实验先看训练/推理框架官方文档与推荐参数，优先把吞吐、显存占用和 GPU 利用率调到合理水平；程序明显低效时，先优化实验程序效率再正式发车。
6. proposal/history 写回必须说人话：写清 benchmark/数据集、比较对象、关键数字、变化趋势、科学含义和 next bounded action，不要只写项目缩写和内部代号。
7. 当前 proposal 收口后不要停在完成态。先写回可靠结果、失败边界、关键诊断和 next bounded action；如果方向已无信息增益，就切到新 proposal 或提交下一条受托管实验。

## Taskboard 操作简介

- 当前对话能完成的 CPU-only 工作，直接做完；不需要再次唤起就输出 `TASKBOARD_SIGNAL=LOCAL_CONTINUE_NO_WAKE`，需要短延迟再进来就输出 `TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH`。
- 需要 GPU、remote、长时间等待或独立生命周期的任务，用 `codex-taskboard submit`。
- 本地跨回复长任务，未启动先 `codex-taskboard bind-before-launch`，已启动后用 `codex-taskboard attach-pid` 接管；正式实验默认优先用 tmux 托管。
- 已有 live task 且当前只是等待结果，用 `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`；只有在没有新 evidence、没有 live task、也没有本地动作时，才用 `TASKBOARD_SIGNAL=PARKED_IDLE`。
- 连续 short CPU-only 审计不需要单独开阶段；真正会跨回复运行、值得 receipt 留痕的实验再交给 taskboard。

## 为什么它更“防呆”

轻量版不靠更长的 prompt，而是靠现有生命周期能力兜底：

- `pending_feedback` / `queued feedback`：会话忙、429、最近有活动时，结果先缓存，稍后再回流。
- `session_output_busy` / `session_busy` defer：当前会话正在忙时，不会强塞结果打断 live rollout。
- `parked watchdog`：第一次 `PARKED_IDLE` 后先做 bounded self-review；只有仍无增益时，才进入更长的 backoff。
- `continuous mode`：agent 中断后还能按同一 proposal/history 继续唤起，而不是要求重开一套上下文。
- `bind-before-launch` / `attach-pid`：本地长任务先绑定，tmux session 可以稳定承接掉线和长时间运行。
- `human-guidance` / `migrate-session`：人工接管或 session 切换时，回流与绑定关系不会丢。

## 手动检查当前 prompt

- 查看当前生效 scene：`codex-taskboard prompt-preview --scene resume`
- 查看 continuous scene：`codex-taskboard prompt-preview --scene continuous --continuous --trigger-signal LOCAL_MICROSTEP_BATCH`
- 查看当前使用的文案来源：预览输出第一行会显示 `prompt_source`
