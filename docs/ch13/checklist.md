# CH13：SubAgent 系统 Checklist

## 1. 定义与加载

- [x] `AgentDefinition` 与 `AgentDef` 均可导入。
- [x] YAML frontmatter 和 Markdown body 完整解析。
- [x] 必填、枚举、轮次、布尔和工具列表错误可定位。
- [x] 未识别 metadata 被保留。
- [x] project > user > builtin > plugin 优先级正确。
- [x] project/user 插件目录可自动发现，额外目录可注册。
- [x] 单文件坏配置不阻断 catalog。
- [x] 热重载成功更新，失败回退有效缓存。
- [x] Explore、Plan、general-purpose 均已打包进 wheel。
- [x] Verification 默认隐藏、flag 开启后可见。

## 2. Fork 与运行循环

- [x] Fork 深拷贝全部 Message 字段。
- [x] 环境、长期记忆注入标记和 token 估算状态被复制。
- [x] 悬挂 tool_use 补 interrupted 错误结果。
- [x] Fork boilerplate 含非交互、禁止再 Fork 和结构化汇报要求。
- [x] Fork-of-Fork 返回明确错误。
- [x] Fork 复用父 client/system/Hook，消息前缀保持一致以支持 prompt cache。
- [x] `run_to_completion` 返回最终模型回合文本。
- [x] 子 Agent 中不存在需要用户响应却无人消费的权限 future。

## 3. 工具与状态隔离

- [x] MCP 工具在过滤各层中直通。
- [x] Agent、AskUserQuestion 等全局禁用。
- [x] 自定义 Agent 限制生效。
- [x] 后台工具白名单生效。
- [x] definition tools/disallowedTools 同时生效。
- [x] 模型 Schema 和实际 Registry 来自同一过滤结果。
- [x] 文件工具和 FileCache 与父 Agent 隔离。
- [x] Conversation、Permission、replacement state、Token、轮次均为子实例。

## 4. 后台任务与追踪

- [x] TaskManager 覆盖 running/completed/failed/cancelled。
- [x] 显式后台、定义后台和 Fork 后台均立即返回 Task ID。
- [x] 前台超时通过 shield 移交原 task。
- [x] ESC 移交原实例，不终止重跑。
- [x] 后台任务自动超时。
- [x] cancel 同时处理 monitor 未启动竞态和实际 execution task。
- [x] completion queue 仅消费一次。
- [x] TraceRegistry 记录父子链路、状态、时间、错误和 Token。
- [x] trace token 汇总正确。
- [x] App 退出时 shutdown 所有后台任务。

## 5. UI 与通知

- [x] `/tasks` 列举任务。
- [x] `/task info <id>` 展示完整详情。
- [x] `/task cancel <id>` 取消运行任务。
- [x] `/trace [trace-id]` 展示链路与 Token。
- [x] `<task-notification>` 包含所有结构字段。
- [x] 超长结果截断到 5000 字符并标记 truncated。
- [x] TUI 只在主 Agent 空闲时注入通知。
- [x] Agent catalog 已进入环境上下文。
- [x] Agent 工具已注册到主 Registry，不是死代码。

## 6. 自动化验收

- [x] Parser 正常和全部失败分支有测试。
- [x] Loader builtin/覆盖/plugin/hot reload/cache 有测试。
- [x] 四层 Tool Filter、MCP、缓存隔离有测试。
- [x] Fork 保真、pending tool、deep copy、嵌套拒绝有测试。
- [x] Trace create/update/complete/tree/tokens 有测试。
- [x] Task complete/fail/timeout/cancel/poll 有测试。
- [x] AgentTool sync/background/fork/auto-timeout/manual-detach/error 有测试。
- [x] TUI 注册、Slash Command 和异步通知有测试。
- [x] `tests/test_subagent.py`：31 passed。
- [x] 全量测试：450 passed，1 skipped。
- [x] Ruff：All checks passed。
- [x] Mypy：91 source files 无问题。
- [x] compileall 通过。
- [x] wheel 和 sdist 构建成功，内置 Agent Markdown 已进入产物。
- [x] `git diff --check` 通过。
- [x] 真实 Provider 冒烟：`SUBAGENT_LIVE_OK`、`FORK_LIVE_OK`，Trace 为 2241 in / 34 out。

## 7. 明确边界

- [x] Worktree 执行留给 CH14，当前对 `isolation` 返回明确错误。
- [x] Team 长期成员留给 CH15，当前对 `team_name` 返回明确错误。
- [x] 后台任务没有伪装成跨进程持久化。
- [x] 没有实现远程 Agent backend、市场或版本管理。
