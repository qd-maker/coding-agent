# CH16 Spec：稳妥交付加固

## 1. 用户能感觉到什么

这一章不增加新玩法。目标是让日常用起来更放心：

1. **改文件不再覆盖你刚改过的内容。** Agent 没看过这个文件，或看完之后文件又变了，就不会动手改。它会先重读，再改。
2. **按 Ctrl+C 取消后，对话还能继续。** 不会出现“下一轮突然报协议错误、整段任务作废”。
3. **无人值守（`-p` / 脚本）不会卡住等人点确认。** 需要你拍板的操作会被拒绝，Agent 用文字说明原因并继续，而不是挂在那里。
4. **对话太长时先自己收拾，再继续干活。** 用户看到一句“正在压缩上下文”，而不是突然失败退出。
5. **只看不改的命令可以一起跑。** `ls`、`git status` 这类只读操作不再一个个排队，感觉更快。
6. **外部工具更老实。** 声明自己只读的 MCP 工具按只读对待；没声明的默认当有风险。

一句话：少踩用户文件、少卡死、少半途崩掉，最终改出来的代码更接近用户刚看过的那一版。

## 2. 功能范围

### 2.1 改文件像改草稿，不覆盖别人的笔迹

- `EditFile` 在挂了 `FileCache` 时（TUI / 默认 Agent 都是这种情况）：
  - 没读过、也没刚写过 → 拒绝，提示先 `ReadFile`。
  - 读过但磁盘上的文件已经变了 → 拒绝，提示重新读取。
  - 准备写入的瞬间文件又变了 → 拒绝，不覆盖。
- `WriteFile` / `ReadFile` / 成功的 `EditFile` 之后，缓存记下当前内容，后续编辑可以接着改。
- 没有 cache 的孤立调用保持原来的精确替换行为，方便单测和脚本。
- 尽量保留原文件的换行风格（`\n` / `\r\n`）。

用户看到的失败信息必须能照做，例如：

```text
Error: file has not been read in this session: src/app.py. Call ReadFile first.
Error: file changed since last read: src/app.py. Call ReadFile again.
```

### 2.2 取消和出错后，对话仍然完整

- 每个工具调用都必须有一条对应结果，哪怕结果是“已取消 / 已中断”。
- 发给模型之前再检查一遍：缺的结果补上，多余的结果丢掉。
- 用户取消、模型报错、流式中断，都走同一条补齐路径。
- 补齐后的对话可以立刻进入下一轮，不需要用户 `/clear`。

### 2.3 没有人点按钮时，绝不等人

- `run_to_completion`、`-p` headless、子 Agent 都视为无人值守。
- 需要确认的操作直接拒绝，返回可读原因，循环继续。
- 不允许留下一个永远等不到的确认框。
- 交互式 TUI 行为不变：该问还是问。

### 2.4 上下文爆了先收拾再试

- 模型返回“上下文太长”时，最多自动压缩并重试 2 次。
- 成功时发出 `RetryEvent`，TUI 显示正在重试。
- 压缩熔断或两次都失败，才向用户报错结束。
- 用户取消不走这条重试，立刻停。

### 2.5 只读命令可以并行

- `ls`、`git status`、`python --version` 等已有安全白名单命令，可以和其他只读工具一起跑。
- 带管道、重定向、`&&`、写盘的命令仍然串行，并且继续走确认 / 拦截。
- 不靠“理解命令语义”，只靠白名单 + 危险符号排除。

### 2.6 外部 MCP 工具按风险贴标签

- MCP 自己声明 `readOnlyHint=true` → 按只读、可并行、Plan 模式可用。
- 声明 `destructiveHint=true` → 按危险命令，不并行。
- 没声明 → 维持现在的保守默认：当命令、延迟加载、不并行。
- 超长工具说明截到 2048 字符，避免把系统提示撑爆。
- 不改现有工具名 `mcp_{server}_{tool}`，避免打断已经能跑的 Context7 链路。

### 2.7 权限决定留下原因

- 每次放行 / 拒绝 / 询问都带上来源：`safety` / `rule` / `mode` / `system`。
- 先把决策变成可讲清楚的对象。本轮不强制落库，也不做“拒绝 N 次就熔断”。

## 3. 非功能边界

- 不引入新的权限模式，不改 TUI 默认 `Accept Edits`。
- 不把完整历史和模型视图拆成两套存储（留给以后）。
- 不做真流式边生成边跑工具（留给以后）。
- 不把 Bash 放进路径沙箱（命令风险继续由危险检测 + 白名单管）。
- 不改 MCP 工具命名，不做远程 skill 危险字段审批。

## 4. 数据与 API

- `FileCache.status(path) -> "missing" | "stale" | "fresh"`
- `ConversationManager.ensure_tool_result_pairing(reason: str) -> int`（补了几条）
- `Agent.allow_permission_prompts: bool`
- `Decision(effect, reason, source="system")`
- `Tool.concurrency_safe_for(arguments) -> bool`
- `Tool.is_destructive: bool`
- 新增 `RetryEvent` 原因：`prompt_too_long compact N`

详细请求 / 响应 / 错误见 [`../api-contract.md`](../api-contract.md) 的 CH16 段。

## 5. 验收标准

- 没读过的文件，EditFile 失败且磁盘内容不变。
- 读完后用编辑器改过的文件，EditFile 失败且磁盘保持用户改过的版本。
- 写完立刻再编辑，不必再读一次。
- 取消生成后，历史里每个工具调用都有结果；下一轮可以正常发模型。
- `run_to_completion` 遇到需要确认的写/命令，返回拒绝而不是挂起。
- 模拟上下文超限时，会压缩并重试，而不是第一次就退出。
- 相邻的 `ls` 与 `ReadFile` 被分成同一并发批次。
- 只读 MCP 工具进入 `read` 类别；未标注的仍是 `command`。
- Ruff、Mypy、相关 Pytest 通过。
