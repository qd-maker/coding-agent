# CH14 Spec：Git Worktree 隔离执行

## 1. 目标

以 Git 原生 worktree 作为文件系统隔离边界，让主 Agent 可以显式进入独立工作目录，也让
SubAgent 通过 `isolation: worktree` 自动获得一次性 checkout。隔离必须覆盖文件工具、Glob/Grep
和 Bash 的实际执行目录，而不通过进程级 `chdir` 影响并发任务。

## 2. 功能范围

1. 名称安全：允许 `feature/demo` 一类嵌套名称；拒绝绝对路径、空段、`.`、`..`、反斜杠逃逸、
   非法字符和超过 64 字符的名称。分支使用 `worktree-<扁平名称>`，`/` 转换为 `+`。
2. 生命周期：`create / enter / exit / remove / list / status / restore_session`。
3. 快速恢复：目标目录已存在时直接读取 `.git → gitdir → commondir → HEAD → ref`，不启动 Git
   子进程；损坏的目录 fail closed。
4. 创建后设置：best-effort 复制 `.env`、`settings.local.json`，同步 hooks 路径，按配置软链接
   大依赖目录，按 `.worktreeinclude` 复制被忽略但运行必需的文件。
5. 缓存与上下文切换：进入/退出时清空 `FileCache`，重定向全部核心工具，重新加载
   `MEWCODE.md`、项目 memory、权限规则、Agent 环境目录和计划文件缓存。
6. 删除保护：有 staged/unstaged/untracked 文件、新 commit、未推送 commit，或 Git 检查失败时，
   默认拒绝删除；只有显式 `discard` 才允许强制清理。
7. 会话恢复：状态原子写入 `.mewcode/worktree_session.json`；`mewcode --resume` 验证并恢复活动
   worktree，坏 JSON/失效目录不恢复。
8. 过期清理：只扫描已知临时命名模式；跳过当前使用中和未过期目录；最后以 dirty/unpushed
   fail-closed 检查决定是否删除。
9. SubAgent：定义或调用参数声明 `isolation: worktree` 时创建 `agent-<8hex>` worktree，注入路径
   翻译提示，子 Agent 核心工具绑定其目录；完成后无变更自动删除，有变更或 commit 则保留并
   返回路径/分支。
10. UI：`EnterWorktree`、`ExitWorktree` 延迟工具；`/worktree`（别名 `/wt`）支持
    `create/list/enter/exit/status`。

## 3. 非功能边界

- 不使用 `os.chdir()`，避免前台、后台与 Fork Agent 互相污染。
- 所有 Git 命令关闭交互输入并设置超时；Git 失败不等价于“无变更”。
- worktree 统一位于 `<repo>/.mewcode/worktrees`，该目录必须被 Git 忽略。
- 删除顺序是 `git worktree remove --force`，等待 lockfile 释放，再删除分支。
- post-create 设置失败只记录 warning，不回滚已成功创建的 worktree。
- CH14 不实现跨进程 AgentTeam、分布式调度或自动合并分支。

## 4. 数据与 API

- `Worktree(name, path, branch, based_on, head_commit, created)`
- `WorktreeSession(original_cwd, worktree_path, worktree_name, original_branch,
  original_head_commit, session_id, hook_based)`
- `WorktreeChanges`：细分 staged/unstaged/untracked/commits/unpushed/check_failed。
- `Changes(uncommitted, new_commits)`：面向 CH14 兼容 Contract 的简化计数。
- `CleanupResult(kept, path, branch)`：SubAgent 自动清理决策。
- `StaleCleanupResult(removed, skipped, errors)`：后台清理诊断。

详细请求、返回与错误语义见 [`../api-contract.md`](../api-contract.md) 的 Worktree Contract。

## 5. 验收标准

- 主目录与 worktree 的相对路径写入严格隔离。
- dirty worktree 删除会给出正确的 `file/files`、`commit/commits` 数量。
- `FileCache` 在 enter 和 exit 后长度为 0。
- 快速恢复路径不执行 Git 子进程。
- `--resume` 恢复活动路径；失效 session 自动清空。
- SubAgent 无变更时目录消失，有变更时主目录不变且结果包含 preserved path/branch。
- Ruff、Mypy、全量 Pytest、compileall 和 build 全部通过。
