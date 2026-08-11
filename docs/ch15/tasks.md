# CH15：AgentTeam Tasks

> 状态说明：以下任务均已实现并纳入测试，不以仅存在文件作为完成标准。

- [x] **T1 核心模型**：实现 `BackendType / TeammateInfo / AgentTeam`、安全命名、原子持久化与同名后缀。
- [x] **T2 磁盘邮箱**：实现单文件消息、FIFO read/consume、广播、清理与结构化消息类型。
- [x] **T3 后端探测**：实现显式/自动 tmux、iTerm2、in-process 选择，不静默降级。
- [x] **T4 共享任务**：实现 `SharedTaskStore` CRUD、负责人、状态、依赖、过滤与原子 JSON。
- [x] **T5 名称注册表**：实现线程安全单例、name/id 双向解析和注销。
- [x] **T6 in-process 后端**：实现协程 handle、结果、取消、完成回调与运行时保留。
- [x] **T7 tmux 后端**：实现 CLI command quoting、三级 pane fallback、wake 和 kill。
- [x] **T8 iTerm2 后端**：实现基于 it2 CLI 的 pane 启动与显式错误。
- [x] **T9 transcript**：持久化完整 Conversation block，恢复时避免重复环境/记忆注入。
- [x] **T10 TeamManager**：实现创建、加载、注册、idle、资源映射、恢复、停止、删除与 shutdown 保留。
- [x] **T11 Agent Loop**：每轮发送前消费 mailbox，并把不同消息类型作为 user message 注入。
- [x] **T12 工具隔离**：实现普通 SubAgent、teammate 与纯 Coordinator 的分层工具过滤。
- [x] **T13 协作工具**：实现 `TeamCreate / SendMessage / TeamStop / TeamMerge / TeamDelete / SyntheticOutput`。
- [x] **T14 AgentTool team 分支**：实现独立 worktree、定义式/Fork、后端分发、Trace 与成员注册。
- [x] **T15 空闲恢复**：完成时保存 transcript；后续消息唤醒原 in-process Agent 并恢复同一上下文。
- [x] **T16 Lead 合并**：实现 clean 前置检查、逐分支合并、冲突全量回滚与结果摘要。
- [x] **T17 App/CLI 接入**：创建并注入 TeamManager，按状态显示工具，支持 `mewcode -p` pane 入口。
- [x] **T18 Slash Command**：实现 `/team create/list/status/tasks/merge/stop/delete`。
- [x] **T19 配置与文档**：扩展 `AppConfig`、示例 YAML、API Contract、README 与 CH15 三文档。
- [x] **T20 验证**：新增 24 个 CH15 单元/集成用例，并执行全仓 pytest、Ruff、mypy、compileall、build。
