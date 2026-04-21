# codex-taskboard 轻度科研约定

runtime 默认从 `prompts/taskboard_runtime_prompt_zh.toml` 读取自动唤起 prompt 文案；也可以用 `CODEX_TASKBOARD_PROMPT_FILE` 或 `~/.config/codex-taskboard/taskboard_runtime_prompt_zh.toml` 覆盖。

## 轻度科研约定

1. 先读 `proposal_file`、`project_history_file` 和本轮回流，再决定下一步；要设计新实验时，再对照必要文献和官方文档/推荐参数，不要脱离 proposal/history 盲调。
2. 同一认知线程里的本地短工作默认一次做完：结果读取、CPU 审计、数据处理、必要代码修复、proposal/history 写回、必要文献对照。不要把几分钟内能完成的 CPU-only 小步拆成新阶段、新 proposal 或单独报告。
3. 默认先怀疑实现，再解释结果。只要结果异常、和 history/文献/官方文档冲突、日志异常、smoke 失败、OOM 或代码报错，就先诊断代码逻辑、数据契约、数据泄漏、评测污染、split、配置与 run 完整性；没有排查清楚前，不要把结果当成有效结论继续扩实验。
4. 正式 GPU/remote 实验前必须先过 smoke。launch 失败、OOM、明显 bug、参数错误、路径错误、配置错配都属于执行问题，不是科研结论；能在当前对话修掉的就直接修掉，不要把这些问题包装成单独实验阶段。
5. 先做对，再做快。正式 GPU 实验先看训练/推理框架官方文档与推荐参数，优先把吞吐、显存占用和 GPU 利用率调到合理水平；程序明显低效时，先优化实验程序效率再正式发车。
6. 写回 proposal/history 时必须说人话：写清 benchmark/数据集、比较对象、关键数字、变化趋势、科学含义和 next bounded action；不要只写项目缩写和内部代号。
7. 当前 proposal 收口后不要停在完成态。先把可靠结果、失败边界、关键诊断和 next bounded action 写回当前 proposal；如果方向已无信息增益，就转成新 proposal 或提交下一条受托管实验。

## Taskboard 操作简介

- 当前对话能完成的 CPU-only 工作，直接做完；不需要再次唤起就输出 `TASKBOARD_SIGNAL=LOCAL_CONTINUE_NO_WAKE`，需要短延迟再进来就输出 `TASKBOARD_SIGNAL=LOCAL_MICROSTEP_BATCH`。
- 需要 GPU、remote、长时间等待或独立生命周期的任务，用 `codex-taskboard submit`。
- 本地跨回复长任务，未启动先 `bind-before-launch`，已启动后用 `attach-pid` 接管；正式实验默认优先用 tmux 托管。
- 已有 live task 且当前只是等待结果，用 `TASKBOARD_SIGNAL=WAITING_ON_ASYNC`；只有在没有新 evidence、没有 live task、也没有本地动作时，才用 `TASKBOARD_SIGNAL=PARKED_IDLE`。

协议尾部保持固定：`TASKBOARD_PROTOCOL_ACK=TBP1`、`CURRENT_STEP_CLASS=...`、`TASKBOARD_SELF_CHECK=...`、`LIVE_TASK_STATUS=...`、`FINAL_SIGNAL=...`。
