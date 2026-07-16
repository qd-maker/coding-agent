# ch08: 上下文管理 Tasks

> 任务粒度: 每个任务可在一次会话内完成，可独立交付。每条任务记录实际落地的文件与行号。

## T1: 常量、tag 与 session 助手

- 影响文件: `mewcode/context/manager.py:14-30, 132-145`
- 依赖任务: 无
- 完成标准: `SINGLE_RESULT_CHAR_LIMIT / AGGREGATE_CHAR_LIMIT / PREVIEW_CHARS / KEEP_RECENT_TURNS / OLD_RESULT_SNIP_CHARS / SNIPPED_TAG / SUMMARY_OUTPUT_RESERVE / AUTO_COMPACT_SAFETY_MARGIN / MANUAL_COMPACT_SAFETY_MARGIN / PERSISTED_TAG / SESSION_SUBDIR` 全部定义；`ensure_session_dir(work_dir)` / `cleanup_tool_results(session_dir)` 实现。

## T2: `CompactEvent` / `ContentReplacementState` / `ContentReplacementRecord` dataclass

- 影响文件: `mewcode/context/manager.py:37-58`
- 依赖任务: T1
- 完成标准: `CompactEvent(before_tokens)` 在 `manager.py:37-38` 定义。`ContentReplacementState` 含 `seen_ids: set[str]` + `replacements: dict[str, str]` 两个 field（都用 `default_factory`），`manager.py:46-49` 定义。`ContentReplacementRecord` 含 `tool_use_id` / `replacement` / `kind="tool-result"`，`manager.py:52-56` 定义。

## T3: `create_replacement_state` / `clone_replacement_state`

- 影响文件: `mewcode/context/manager.py:59-68`
- 依赖任务: T2
- 完成标准: `create_replacement_state()` 返回空容器；`clone_replacement_state(src)` 用 `set(src.seen_ids)` 与 `dict(src.replacements)` 浅拷贝，源与拷贝彼此独立；`test_clone_independent` 通过。

## T4: Transcript JSONL `append_replacement_records` / `load_replacement_records`

- 影响文件: `mewcode/context/manager.py:70-104`
- 依赖任务: T2
- 完成标准: `REPLACEMENT_RECORDS_FILENAME = "replacement_records.jsonl"` 在 `manager.py:70` 定义。`append_replacement_records(session_dir, records)`：空切片直接 return；用 `open("a", encoding="utf-8")` 追加，每行一个 `{"kind": ..., "tool_use_id": ..., "replacement": ...}` 对象；`load_replacement_records(session_dir)`：缺文件返回空列表；逐行 `json.loads`。`test_append_and_load_records_roundtrip` 通过。

## T5: `reconstruct_replacement_state`

- 影响文件: `mewcode/context/manager.py:107-127`
- 依赖任务: T2, T4
- 完成标准: 先 seed `seen_ids` = `{ tr.tool_use_id | for tr in m.tool_results, for m in messages }`；按 `r.kind == "tool-result"` 过滤 records 并命中 candidate 才写入 `replacements`；可选 `inherited_replacements` 在 candidate ∩ 未被 records 覆盖时补全；`test_reconstruct_from_records / test_reconstruct_with_inherited_parent` 通过。

## T6: `persist_tool_result` / `make_persisted_preview`

- 影响文件: `mewcode/context/manager.py:148-170`
- 依赖任务: T1
- 完成标准: `persist_tool_result` 用 `os.open(O_WRONLY | O_CREAT | O_EXCL)` 写到 `<session_dir>/<tool_use_id>.txt`，`FileExistsError` 静默吞掉（幂等）。`make_persisted_preview` 输出 `<persisted-output>\n输出太大（XKB），完整内容已保存到：\n<file_path>\n\n预览（前 2KB）：\n<content[:PREVIEW_CHARS]>\n</persisted-output>`。`TestPersistToolResult` / `TestMakePersistedPreview` 通过。

## T7: 辅助 `_count_turns` / `_copy_message_with_results` / `_snip_stale_messages`

- 影响文件: `mewcode/context/manager.py:173-238`
- 依赖任务: T1, T6
- 完成标准:
  - `_count_turns(messages)` 数 `assistant && not tool_uses` 当作一轮。
  - `_copy_message_with_results(msg, new_tool_results)` 产出新 `Message` 实例，共享 `tool_uses` / `thinking_blocks` 引用（不可变结构）。
  - `_snip_stale_messages(history)` 在 new history 上跑（stateless），总轮数 ≤ `KEEP_RECENT_TURNS` 直接 return；超 boundary 的消息里超 `OLD_RESULT_SNIP_CHARS` 字符且未 PERSISTED/SNIPPED 前缀的 tool result 整体替换为 `<snipped>` 头 + 200 字符预览 + `… (snipped)` 尾。

## T8: Layer 1 `apply_tool_result_budget` Design B 主流程

- 影响文件: `mewcode/context/manager.py:241-348`
- 依赖任务: T2, T6, T7
- 完成标准: 签名 `apply_tool_result_budget(conversation, session_dir, state) -> tuple[ConversationManager, list[ContentReplacementRecord]]`，**不修改入参 conversation**。算法：
  1. 阶段 1: 对每个 tr 分四类——`state.replacements` 命中 → 复读；`state.seen_ids` 命中 → 冻结原文；外部已带 `PERSISTED_TAG` 前缀 → 视为已知决策，写入 state 与 records；其余进 fresh。
  2. 阶段 2 (Pass 1): fresh 中 content 长度 > `SINGLE_RESULT_CHAR_LIMIT` 调 `persist_tool_result` + `make_persisted_preview`，写入 state 与 records。
  3. 阶段 3 (Pass 2): 计算 `total = Σdecisions.values + Σremaining.content`；> `AGGREGATE_CHAR_LIMIT` 时按 content 长度降序挑直到压回上限。
  4. 阶段 4: 未决策的 fresh 全部加进 `state.seen_ids`、`decisions[id] = tr.content`。
  5. 末段: 用 `decisions` 构造新 `[ToolResultBlock]` 保持原顺序 → `_copy_message_with_results` → `_snip_stale_messages` 跑 Pass 3 → 构造新 `ConversationManager` 并复制 `env_injected / ltm_injected / last_input_tokens` flags。
- `test_apply_does_not_mutate_conv / test_first_call_freezes_unreplaced / test_replacement_byte_identical / test_frozen_never_replaced / test_aggregate_only_picks_fresh` 通过。

## T9: 阈值计算 `compute_compact_threshold` / `should_auto_compact`

- 影响文件: `mewcode/context/manager.py:350-358`
- 依赖任务: T1
- 完成标准: `compute_compact_threshold(200_000) == 167_000`、`compute_compact_threshold(200_000, manual=True) == 177_000`、`compute_compact_threshold(128_000) == 95_000`；`should_auto_compact(last_input_tokens, context_window)` 边界精确。`TestComputeCompactThreshold / TestShouldAutoCompact` 通过。

## T10: 摘要 prompt + helpers (`SUMMARY_PROMPT` / `extract_summary` / `COMPACT_BOUNDARY_MESSAGE` / `build_compact_messages` / `_group_messages_by_turn`)

- 影响文件: `mewcode/context/manager.py:360-419`
- 依赖任务: T1
- 完成标准: `SUMMARY_PROMPT` 含九节结构 + 两次禁止工具调用 + 先 `<analysis>` 再 `<summary>` 的要求；`extract_summary` 找到 `<summary>...</summary>` 整对时返回内部 trim，找不到时返回原文整体；`build_compact_messages(summary)` 输出 `[user '[摘要]\n...', assistant COMPACT_BOUNDARY_MESSAGE]` 两条；`_group_messages_by_turn` 按 `assistant && not tool_uses` 切轮。`TestExtractSummary / TestBuildCompactMessages` 通过。

## T11: 熔断器 `CompactCircuitBreaker`

- 影响文件: `mewcode/context/manager.py:421-436`
- 依赖任务: T1
- 完成标准: `@dataclass` 含 `max_failures: int = 3` 默认值与 `consecutive_failures: int = field(init=False, default=0)`；`record_failure / record_success / is_open` 三方法行为正确；`TestCompactCircuitBreaker` 通过。

## T12: Layer 2 `auto_compact`

- 影响文件: `mewcode/context/manager.py:439-end`
- 依赖任务: T9, T10, T11
- 完成标准: 自动模式 `conversation.last_input_tokens < threshold` 返回 `None`；`breaker.is_open()` 返回错误字符串；构造临时 `ConversationManager`（header SUMMARY_PROMPT + 原 history + 结尾再次提醒不要调工具）通过 `client.stream(summary_conv, system=SUMMARY_PROMPT)` 收 `TextDelta` 拼成文本；PTL 重试用 `_group_messages_by_turn` 丢最旧 1/5，最多 3 次；成功调 `conversation.replace_history(build_compact_messages(summary))` + `cleanup_tool_results(session_dir)` + `breaker.record_success()`，返回 `CompactEvent(before_tokens)`；失败 `breaker.record_failure()` 返回错误字符串。

## T13: Anthropic 客户端缓存断点

- 影响文件: `mewcode/client.py:24-68, 138-160`
- 依赖任务: 无
- 完成标准:
  - `_EPHEMERAL = {"type": "ephemeral"}` 常量定义。
  - `_mark_last_user_tail_for_cache(messages)` 倒序找最后一条 user message，对其末块（string content 自动 up-convert 为 block 列表）打 marker。
  - `_mark_last_tool_for_cache(tools)` 返回浅拷贝并给末项加 marker（不污染调用方持有的工具表）。
  - Anthropic `stream` 内：`messages` 构造后调 `_mark_last_user_tail_for_cache(messages)`；`system` 包装成 `[{"type":"text","text":system,"cache_control":_EPHEMERAL}]`；`tools` 经 `_mark_last_tool_for_cache` 处理后赋给 `kwargs["tools"]`。

## T14: Agent 集成

- 影响文件: `mewcode/agent.py:15-27, 314-316, 436-516, 887-918, 960-1003`
- 依赖任务: T8, T12, T13
- 完成标准:
  - import 段加 `ContentReplacementRecord / ContentReplacementState / append_replacement_records / create_replacement_state / load_replacement_records / reconstruct_replacement_state`。
  - `Agent.__init__` 加 `self.replacement_state: ContentReplacementState = create_replacement_state()`（line 316）。
  - 主循环（line 436 附近）：先 `await auto_compact(...)` 处理事件；中间写各种 reminder；在 `client.stream` 调用前一刻：`api_conv, _new_records = apply_tool_result_budget(conversation, self.session_dir, self.replacement_state)` → 非空 `append_replacement_records(self.session_dir, _new_records)` → `self.client.stream(api_conv, ...)`。
  - `manual_compact` 直接走 `auto_compact(..., manual=True)`，不再前置调 `apply_tool_result_budget`（compact 将整段替换 history，前置 apply 的产物会被丢弃）。
  - 另一主循环变体（line 960）：同样把 `apply_tool_result_budget` 移到 `client.stream` 前一刻。

## T15: Fork 状态继承

- 影响文件: `mewcode/tools/agent_tool.py:192-203`
- 依赖任务: T3, T14
- 完成标准: 创建 sub_agent 后判断 `p.subagent_type is None`（即真 fork）时 `from mewcode.context import clone_replacement_state` → `sub_agent.replacement_state = clone_replacement_state(self._parent_agent.replacement_state)`。

## T16: 测试

- 影响文件: `tests/test_context.py`、`tests/test_replacement_state.py`