# CH12：Hook 系统 Tasks

## T1：定义生命周期事件

- [x] 新增 `simplecode/hooks/events.py`。
- [x] 使用 `LifecycleEvent(StrEnum)` 定义 15 个固定事件。

## T2：实现核心数据结构

- [x] 新增 `Action`、`ActionResult`、`Hook`、`HookContext`。
- [x] 新增 `HookNotification`、`ToolRejectedError`。
- [x] Hook 支持 `once/async_exec/reject/executed`。
- [x] 保留 `HookResult` callback 兼容层。

## T3：实现 Condition DSL

- [x] 新增 `Condition/ConditionGroup/ConditionParseError`。
- [x] 支持 `==/!=/=~/~=`。
- [x] 支持 `&&/||` 且拒绝混用。
- [x] 支持 tool/event/嵌套 args 字段。

## T4：实现上下文模板

- [x] 支持六类上下文变量。
- [x] 未定义变量替换为空。
- [x] 复杂参数用 JSON 字符串展开。

## T5：实现 Action 执行器

- [x] command：子进程、合并输出、退出码、超时和取消清理。
- [x] prompt：模板展开与 prompt 队列。
- [x] http：异步线程池、JSON body、超时、500 字节预览。
- [x] agent：CH13 前占位实现。

## T6：实现 HookEngine

- [x] event/condition/once 匹配。
- [x] 同步和后台 async 调度。
- [x] 通知与 prompt 一次性消费队列。
- [x] pre-tool 专用拒绝入口。
- [x] Action 与 callback 错误隔离。

## T7：实现 YAML 加载与校验

- [x] 四类 Action 必填字段校验。
- [x] event、condition、timeout、bool 字段校验。
- [x] reject/async 与 pre-tool 约束校验。
- [x] 自动 id 与可定位的 `HookConfigError`。

## T8：接入 Config 与 CLI

- [x] `AppConfig.raw_hooks` 以 `hooks` YAML alias 接收原始配置。
- [x] CLI 在构造 TUI 前调用 `load_hooks`。
- [x] 直接构造 App 时同样执行二阶段校验。
- [x] 更新 `simplecode.yaml.example`。

## T9：接入 Agent Loop

- [x] 会话、轮次、模型收发和工具前后事件。
- [x] pre-tool 拦截早于权限判断。
- [x] 拒绝原因回灌 LLM，真实工具不执行。
- [x] prompt Action 注入系统上下文。

## T10：接入系统事件

- [x] startup/shutdown。
- [x] error/compact。
- [x] permission_request/file_change。
- [x] command_execute。

## T11：测试与回归

- [x] 新增 `tests/test_hooks.py`，共 13 个测试类、45 个测试结果。
- [x] 增加 config/CLI 与 TUI 生命周期集成测试。
- [x] 修复 InlineQuestionPrompt 首帧内容竞态并连续验证 5 次。
- [x] 全仓回归：418 passed，1 skipped。
- [x] 更新 API Contract、README 与 CH12 三件套。
