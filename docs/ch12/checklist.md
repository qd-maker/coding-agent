# CH12：Hook 系统 Checklist

## 1. 数据与 DSL

- [x] `LifecycleEvent` 恰好包含 15 个事件且可和字符串比较。
- [x] `Action/Hook/HookContext/ConditionGroup/Condition/ToolRejectedError` 已实现并导出。
- [x] condition 支持 `==/!=/=~/~=`。
- [x] `&&/||` 可单独使用，混用时报可定位错误。
- [x] 正则外层 `/` 被移除，非法正则只是不匹配。
- [x] HookContext 支持 tool/event/args 嵌套取值。
- [x] 六类模板变量可展开，未知变量为空串。

## 2. Action 与执行控制

- [x] command 合并 stdout/stderr，并按退出码返回结构化结果。
- [x] command timeout 会 kill + wait；任务取消也清理子进程。
- [x] prompt 进入一次性消费队列并注入 Agent 系统上下文。
- [x] HTTP 在 executor 中执行，30 秒超时，响应限制 500 字节。
- [x] agent Action 明确为 CH13 占位，不伪装成已实现 SubAgent。
- [x] once 首次触发后不重复。
- [x] async 后台执行且不阻塞普通事件。

## 3. 拦截与错误隔离

- [x] `run_pre_tool_hooks` 返回 `ToolRejectedError | None`。
- [x] pre-tool Hook 同步执行，YAML 禁止 async。
- [x] reject 只允许 pre-tool。
- [x] 拒绝发生在权限弹窗和工具真实执行之前。
- [x] 模型收到 `Hook rejected: <reason>` 的错误工具结果。
- [x] Action/condition/callback 失败不会中断 Agent 主流程。
- [x] 失败也产生可观察的 HookNotification。

## 4. 配置

- [x] YAML `hooks` 绑定为 `AppConfig.raw_hooks`。
- [x] event、action type、必填字段、timeout、condition、reject/async 被集中校验。
- [x] 缺少 id 时自动生成稳定 id。
- [x] 错误消息包含 Hook id 或数组位置。
- [x] 非法 Hook 在 TUI 启动前退出。
- [x] 示例配置包含上下文注入、危险操作拦截和异步格式化。

## 5. 生命周期集成

- [x] startup/shutdown。
- [x] session_start/session_end。
- [x] turn_start/turn_end。
- [x] pre_send/post_receive。
- [x] pre_tool_use/post_tool_use。
- [x] error/compact。
- [x] permission_request/file_change。
- [x] command_execute。
- [x] TUI 可展示 Hook 通知。

## 6. 自动化验收

- [x] `tests/test_hooks.py`：45 passed。
- [x] 13 个测试类覆盖事件、上下文、DSL、四执行器、loader、engine、Agent 集成。
- [x] 配置原样保留与非法配置 CLI 退出已覆盖。
- [x] startup/command_execute/shutdown 的 TUI 生命周期已覆盖。
- [x] permission_request/file_change 的 Agent 生命周期已覆盖。
- [x] pre-tool reject 跳过真实工具并回灌模型已覆盖。
- [x] InlineQuestionPrompt 竞态修复后连续 5 次通过。
- [x] 全量测试：418 passed，1 skipped。
- [x] Ruff、Mypy、compileall、wheel/sdist 最终校验全部通过。

## 7. 明确未做

- [x] 未实现真实 agent Action/SubAgent。
- [x] 未实现 Hook 热加载、市场或版本管理。
- [x] 未实现条件括号、混合优先级或任意表达式执行。
- [x] 未实现 HTTP 认证、重试和大响应流式消费。
