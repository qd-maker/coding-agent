# CH14 Tasks

## API Contract（先行）

- [x] 在 `docs/api-contract.md` 定义名称、生命周期、工具、命令、恢复、删除保护和 SubAgent 隔离。
- [x] 明确 Git 失败时 fail closed，明确删除需要显式 discard。

## 核心实现

- [x] `mewcode/worktree/slug.py`：安全校验、扁平化、临时名称生成。
- [x] `mewcode/worktree/models.py`：Worktree、Session、变更与清理结果。
- [x] `mewcode/worktree/session.py`：原子持久化和无 Git 子进程 HEAD 恢复。
- [x] `mewcode/worktree/changes.py`：dirty、新 commit、未推送检查及兼容计数。
- [x] `mewcode/worktree/setup.py`：本地配置、hooks、依赖软链接、ignored include。
- [x] `mewcode/worktree/manager.py`：完整 lifecycle、锁、缓存回调、恢复和保护删除。
- [x] `mewcode/worktree/cleanup.py`：三层过期过滤和周期后台任务。

## 工具与 Agent 集成

- [x] 为核心 Read/Write/Edit/Glob/Grep/Bash 增加实例级 `work_dir`，禁止全局 chdir。
- [x] `ToolRegistry.set_work_dir()` 支持会话级重定向。
- [x] 子 Agent 工具过滤器创建独立 FileCache 和工作目录绑定工具。
- [x] `EnterWorktree` / `ExitWorktree` 工具注册到主 Agent。
- [x] `AgentTool` 支持调用参数或定义式 `isolation: worktree`。
- [x] 前台、后台、Fork 共用隔离执行和 clean/dirty 自动保留策略。
- [x] SubAgent 禁止再次调用 worktree 工具，避免嵌套失控。

## TUI、配置与恢复

- [x] `WorktreeConfig`：enabled、symlink_directories、cleanup interval/cutoff。
- [x] `/worktree` 与 `/wt`：create/list/enter/exit/status。
- [x] enter/exit 回调刷新 FileCache、工具、指令、memory、权限规则和 Agent 环境目录。
- [x] `mewcode --resume` 恢复崩溃前的活动 worktree。
- [x] on_mount 启动清理 task，on_unmount 安全取消，不误删保留工作。

## 测试与文档

- [x] `tests/test_worktree.py` 覆盖路径安全、生命周期、保护、快速恢复和持久化。
- [x] 覆盖核心工具真实目录隔离和缓存清空。
- [x] 覆盖 SubAgent clean 自动删除、dirty 保留、后台任务回传。
- [x] 覆盖 post-create 和 stale cleanup。
- [x] 更新 README、章节索引、示例配置和 checklist。
- [x] 执行 Ruff、Mypy、Pytest、compileall、build 门禁。
