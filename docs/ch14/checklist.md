# CH14 Checklist

## Contract 与安全边界

- [x] API Contract 已写明请求、响应、错误和删除确认语义。
- [x] 名称拒绝绝对路径、`.`/`..`、空段、非法字符和超长输入。
- [x] 嵌套名称只影响目录层级，分支名安全扁平化。
- [x] worktree 目录位于 `.simplecode/worktrees` 且仓库忽略 `.simplecode/`。
- [x] Git 子进程禁用交互、设置 timeout；检查失败按有变更处理。
- [x] 未显式 discard 时不会删除 dirty/new/unpushed work。

## 生命周期与恢复

- [x] create、enter、exit、remove、list、status 可用。
- [x] 已存在目录走纯文件系统快速恢复，不启动 Git。
- [x] post-create 四类设置均为 best-effort 并输出报告。
- [x] enter/exit 均清空 FileCache 并重载 checkout-local 状态。
- [x] session 原子持久化；空 session 写 `{}`。
- [x] `--resume` 能恢复有效 session，失效 session 会被清空。
- [x] worktree remove 与 branch delete 之间等待 Git lockfile。

## Agent 与 UI

- [x] 核心文件工具、搜索工具和 Bash 使用实例级 work_dir。
- [x] 主 Agent Enter 后后续相对路径落到 worktree，Exit 后回到主目录。
- [x] `AgentToolParams.isolation` 和 `AgentDefinition.isolation` 都支持 worktree。
- [x] SubAgent prompt 含 parent/worktree 路径翻译及 re-read 约束。
- [x] SubAgent clean 自动删除；dirty/commit worktree 保留并回报路径/分支。
- [x] 后台 Agent 运行中的实例不重启，完成后正常进入 task notification。
- [x] `/worktree create|list|enter|exit|status` 和 `/wt` 可用。
- [x] EnterWorktree / ExitWorktree 为 deferred tools。

## 自动验证证据

- [x] `tests/test_worktree.py`：19 个 CH14 用例通过。
- [x] 真实临时 Git 仓库 smoke：主目录 `witness.txt` 未被隔离写入修改。
- [x] dirty 删除保护返回 `1 uncommitted file`，显式 discard 后目录和分支删除。
- [x] crash session JSON 能由新 Manager 恢复。
- [x] `ruff check simplecode tests` 通过。
- [x] `mypy simplecode` 通过（103 source files）。
- [x] `pytest -q` 全量通过（最终数量见 README）。
- [x] `compileall` 与 Python build 通过。

## 明确不做

- [x] 不实现自动 merge/rebase/push。
- [x] 不实现跨进程 AgentTeam 或分布式 worktree 调度。
- [x] 不在 Git 状态未知时猜测“安全可删”。
