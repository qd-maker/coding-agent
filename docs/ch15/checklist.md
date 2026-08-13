# CH15：AgentTeam Checklist

## 数据与持久化

- [x] 团队/成员名称经过安全校验，无法路径穿越。
- [x] `config.json / tasks.json / mailbox / transcripts` 均位于团队目录并使用原子替换或单消息文件。
- [x] 团队可以在新 `TeamManager` 实例中从磁盘重新加载。
- [x] tool use/result/thinking 可无损保存并恢复。
- [x] App 退出保留团队，不把“进程退出”误当作“用户删除团队”。

## 运行与协作

- [x] pane 后端不可用时明确报错；显式 in-process 可运行。
- [x] 每个 teammate 使用独立 Git worktree 和独立核心文件工具实例。
- [x] Team 工具只对 Lead/成员按需可见，普通 SubAgent 无法递归创建 Team/Agent。
- [x] 点对点、广播、生命周期和审批回复消息可落盘。
- [x] 文本消息缺少 summary、目标不属于当前团队时返回 `is_error`。
- [x] Agent Loop 在每轮 LLM 调用前消费 mailbox。
- [x] 成员完成后变 idle、通知 Lead 并保存 transcript。
- [x] 给 idle in-process 成员发送消息会恢复原 Agent；pane 不可恢复时显式失败。

## Lead 与安全控制

- [x] Lead 可创建/列举/更新带依赖的共享任务。
- [x] 纯调度必须配置和环境变量双锁同时开启。
- [x] 纯调度 Lead 没有代码读写和 shell 工具。
- [x] Coordinator Prompt 包含 Research/Synthesis/Implementation/Verification 四阶段。
- [x] active 成员存在时拒绝删除团队。
- [x] Lead worktree dirty 时拒绝合并。
- [x] 任一 merge conflict 都回滚到原始 HEAD，不留下半合并状态。
- [x] worktree 有受保护内容时默认拒绝删除，`--discard` 必须显式给出。

## UI、配置与可观测性

- [x] `/team create/list/status/tasks/merge/stop/delete` 已接入 UI CommandContext。
- [x] `teammate_mode` 仅接受 `"" / auto / in-process / tmux / iterm2`。
- [x] `enable_coordinator_mode` 默认关闭。
- [x] 父子 Trace 记录 teammate id、状态和 Token。
- [x] Team 状态可显示成员 backend、active/idle 和 worktree 路径。

## 质量门禁

- [x] `tests/test_teams.py`：24 passed。
- [x] `tests/test_team_tasks.py`：兼容旧共享任务 API。
- [x] `tests/test_subagent.py` 与 `tests/test_worktree.py`：未破坏 CH13/CH14。
- [x] `ruff check simplecode tests`：通过。
- [x] `mypy simplecode`：通过。
- [x] `python -m compileall -q simplecode`：通过。
- [x] 全仓 pytest：见最终验证记录（Windows symlink 权限用例保持 skip）。
