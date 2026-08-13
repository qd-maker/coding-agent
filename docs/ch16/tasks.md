# CH16 Tasks

## PDR

- [x] 写 `docs/ch16/spec.md`、`checklist.md`，并更新 `docs/README.md`、`docs/api-contract.md`。

## 改文件不覆盖用户

- [x] `FileCache.status()` 区分没读过 / 读过但已过期 / 仍然新鲜。
- [x] `EditFile`：有 cache 时强制先读或先写；过期和写入瞬间变化都拒绝。
- [x] `WriteFile` / 成功的 `EditFile` 把新内容写进 cache，而不是只作废。
- [x] 保留原文件换行风格。
- [x] 更新 EditFile 工具说明，让模型知道要先读。

## 取消后对话不断

- [x] `conversation.ensure_tool_result_pairing()`：缺结果补错误，孤儿结果丢掉。
- [x] 每轮发给模型前调用一次。
- [x] `Agent.run` 在取消 / 异常时补齐尚未返回的工具结果。
- [x] 用户可见的取消文案固定、简短。

## 无人值守不卡死

- [x] `Agent.allow_permission_prompts`，默认 True。
- [x] `run_to_completion` 期间强制关闭弹确认。
- [x] ask 在无人值守下变成 deny，循环继续。
- [x] 交互式 TUI 路径不回归。

## 上下文爆了先收拾

- [x] 公开 `is_prompt_too_long()`。
- [x] 主循环捕获超限错误，强制 compact，最多重试 2 次。
- [x] 取消不走重试；熔断失败才结束。

## 只读更快、外部工具更老实

- [x] `Tool.concurrency_safe_for()` / `is_destructive`。
- [x] Bash 白名单命令可与只读工具并行。
- [x] MCP 读取 `readOnlyHint` / `destructiveHint` / alwaysLoad；描述截断。

## 测试与门禁

- [x] `tests/test_tools.py`：先读后改、过期拒绝、写后可改。
- [x] `tests/test_agent.py` 或新测试：配对修复、取消补齐、headless 不挂、超限重试。
- [x] `tests/test_permissions.py`：Decision.source 有值。
- [x] `tests/test_mcp.py`：只读 annotation 映射。
- [x] `ruff` / `mypy` / 相关 `pytest`。
