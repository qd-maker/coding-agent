# ch09: 记忆系统 Tasks

> 任务粒度: 每个任务可在一次会话内完成，可独立交付。所有 T 任务完成后逐项勾上，每条任务记录实际落地的文件与行号。

## T1: 项目指令 `@include` 递归展开
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/instructions.py:9-46`（`process_includes`）
- 依赖任务: 无
- 完成标准:
  - `MAX_INCLUDE_DEPTH=5` 与 `INCLUDE_PREFIX="@include "` 常量到位
  - 逐行扫描 content，命中前缀的行剥出相对路径，按 `(base_dir / rel_path).resolve()` 解析
  - 解析后用 `abs_path.relative_to(resolved_root)` 判断是否在 project_root 内，越界落 `<!-- @include blocked: path outside project -->`
  - 文件不存在 / 非 file 落 `<!-- @include skipped: file not found -->`
  - 命中的文件递归 `process_includes(..., depth+1)` 后拼回
  - `depth >= MAX_INCLUDE_DEPTH` 直接 return 原 content
  - 测试 `TestProcessIncludes` 5 个用例（无 include / 基本 include / 递归 include / depth 限制 / path 越界 / 文件不存在）全部命中

## T2: 项目指令三层加载
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/instructions.py:48-66`（`load_instructions`）
- 依赖任务: T1
- 完成标准:
  - 三层优先级顺序：`<root>/MEWCODE.md` → `<root>/.mewcode/MEWCODE.md` → `~/.mewcode/MEWCODE.md`
  - 每层文件存在且 is_file 时读取并对其内容跑 `process_includes(content, path.parent, root)`
  - 多段用 `\n---\n` join
  - 无任何文件存在时返回 `""`
  - 测试 `TestLoadInstructions`（`test_single_layer / test_multi_layer_priority / test_no_files_returns_empty`）通过

## T3: SessionRecord 序列化
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/session.py:30-119`（`RecordType / SessionRecord`）
- 依赖任务: 无
- 完成标准:
  - `RecordType(str, Enum)` 5 个值：`system_prompt / user / assistant / tool_result / compression`
  - `SessionRecord` dataclass 字段：`type / content / timestamp / tool_use_id / is_error`
  - `to_jsonl()`：序列化 `{type, content, timestamp}`，可选 `tool_use_id`，仅 tool_result 写 `is_error`，`ensure_ascii=False`
  - `from_jsonl(line)`：异常返回 None；未知 RecordType 也返回 None
  - `from_message(message)`：tool_results 拆多条 tool_result 记录；assistant + tool_uses 内联到 content blocks (`[{type:text}, {type:tool_use,id,name,input}]`)；plain user / assistant 走单条普通记录
  - 测试 `TestSessionRecord` 5 个用例（user roundtrip / assistant with tool_uses / tool_results multiple records / malformed jsonl / plain assistant）通过

## T4: 记录 ↔ 消息互转 + 链路校验
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/session.py:122-222`（`records_to_messages / validate_message_chain`）
- 依赖任务: T3
- 完成标准:
  - `records_to_messages`：维护 `pending_tool_results` 队列，遇到非 tool_result 记录前先把队列冲到一条 user message 的 `tool_results`；system_prompt 跳过；compression 渲染为 `[摘要]\n<content>` 的 user message；assistant content list 时拆 text + tool_uses
  - `validate_message_chain`：维护 `pending_tool_uses set`，assistant content list 里 tool_use block 的 id 进集合，tool_result 出集合；集合为空时记录前缀长度，最后返回最大完整前缀
  - 测试 `TestRecordsToMessages`（3 个）+ `TestValidateMessageChain`（3 个）全部通过

## T5: SessionMeta 落盘 + Session 句柄
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/session.py:225-307`（`SessionMeta / Session / ResumeResult`）
- 依赖任务: T3
- 完成标准:
  - `SessionMeta` dataclass 7 字段（id / title / summary / message_count / total_tokens / created_at / last_active），`created_at / last_active` 默认 `datetime.now(timezone.utc)`
  - `SessionMeta.save(path)`：JSON 落盘，含 `isoformat` 时间字段
  - `SessionMeta.load(path)`：异常返回 None
  - `Session.__init__(session_id, file, meta, sessions_dir)` 持文件句柄
  - `Session.append(message)`：调 `SessionRecord.from_message` 拆条逐条 `to_jsonl + "\n"` 写入并 `flush`；`meta.message_count += 1`；`meta.last_active = now`；首次遇到 user content 时截 `TITLE_MAX_LENGTH=50` 写入 `meta.title`；每次 append 后 `meta.save` 覆盖 `.meta` 文件
  - `Session.close()`：判空 + `flush + close`
  - `ResumeResult` dataclass：`session / messages / last_active`
  - 测试 `TestSession`（2 个：append 写 jsonl + title 设置）通过

## T6: SessionManager 生命周期
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/session.py:384-482`（`_generate_session_id / SessionManager`）
- 依赖任务: T4, T5
- 完成标准:
  - `_generate_session_id()`：`session_<YYYYMMDD_HHMMSS>_<4 字符 a-z0-9>` 格式
  - `SessionManager.__init__(work_dir)`：构造 `<work_dir>/.mewcode/sessions/` 并 `mkdir(parents=True, exist_ok=True)`
  - `create()`：新 ID + 写 `.meta` + 打开 jsonl `mode="a"` + 返回 `Session`
  - `list()`：扫 `*.meta`、`SessionMeta.load` 反序列化、按 `last_active` 倒序
  - `resume(id)`：jsonl 缺失返回 None；逐行 `from_jsonl` 跳空跳错；`validate_message_chain` 截断；`records_to_messages` 重建；重新打开 jsonl `mode="a"` 续写
  - `delete(id)`：删 jsonl + .meta，任一存在即返回 True
  - `cleanup(max_age_days=30)`：迭代 `.meta`、`last_active < cutoff` 调 `delete` 并计数
  - 测试 `TestSessionManager`（create_and_list / delete / cleanup / generates_valid_id）+ `TestSessionResume`（restores_messages / nonexistent / truncates_incomplete_chain）通过

## T7: 断会话时长提示
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/session.py:358-380`（`build_time_gap_message`）+ `TIME_GAP_THRESHOLD` 常量
- 依赖任务: 无
- 完成标准:
  - `TIME_GAP_THRESHOLD=timedelta(hours=24)` 常量
  - 距 `last_active < 24h` 返回 None
  - `gap.total_seconds() // 3600 >= 48` 表达为「N 天」，否则「N 小时」
  - 返回的 Message 包含 `代码可能有变更，建议在操作前重新读取相关文件。`
  - 测试 `TestTimeGapMessage`（no gap returns none / gap returns message）通过

## T8: 会话摘要生成（可选）
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/session.py:316-355`（`generate_session_summary`）+ `SESSION_SUMMARY_PROMPT`
- 依赖任务: 无
- 完成标准:
  - `SESSION_SUMMARY_PROMPT` 文本到位（要求一句话总结、不调用工具）
  - `generate_session_summary(client, conversation, protocol)`：取 `history[-10:]`；构造单独 `ConversationManager` 拼装 prompt + 最近消息 + 收尾问句；跑 `client.stream` 收 `TextDelta`；异常返回 `""`
  - 不做单独单元测试（集成在 App 的异步 summary 更新中）

## T9: MemoryManager 双路径基础
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/auto_memory.py:8-71`（常量 + `__init__ / user_path / project_path / load`）
- 依赖任务: 无
- 完成标准:
  - 常量：`USER_MEMORIES_RELPATH = ".mewcode/memories.md"` / `PROJECT_MEMORIES_RELPATH = ".mewcode/memories.md"`
  - `__init__`：算 `_user_path = Path.home() / USER_MEMORIES_RELPATH` 与 `_project_path = Path(project_root) / PROJECT_MEMORIES_RELPATH`，`_last_extraction_msg_count = 0`
  - `user_path / project_path` property 暴露
  - `load()`：两个路径若存在且非空，`strip` 后用 `\n\n` join；都空返回 `""`
  - 测试 `TestMemoryManager.test_load_empty / test_load_merges_user_and_project` 通过

## T10: MEMORY_EXTRACTION_PROMPT + extract LLM 跑提取
- 影响文件: `/Users/codemelo/mewcode/mewcode/memory/auto_memory.py:11-37, 72-127`（`MEMORY_EXTRACTION_PROMPT / extract`）
- 依赖任务: T9
- 完成标准:
  - prompt 含 4 类分类标题（用户偏好 / 纠正反馈 / 项目知识 / 参考资料）、`不要重复添加` / `不要写任何条目，不要写占位符` / `不要调用任何工具` 等关键 marker
  - `extract(client, conversation, protocol)`：
    - 从 `conversation.history[self._last_extraction_msg_count:]` 取增量
    - 把 user/assistant 文本拼成 `"用户: ..."` / `"助手: ..."` 行
    - 拼装 prompt 含 `## 当前 memories.md\n<当前内容 or (空)>` + `## 最近对话\n...`
    - 构造独立 `ConversationManager`、`history = [Message(role="user", content=prompt)]`
    - 跑 `client.stream(extract_conv, system="你是一个记忆提取助手。")`，收集 `TextDelta.text`
    - 异常静默 `return`
    - 成功后更新 `self._last_extraction_msg_count = len(conversation.history)`，把 collected 转给 `_write_memories`
  - 测试 `TestMemoryExtraction.test_extraction_prompt_contains_categories` 通过

## T11: `_write_memories` 分流 + 占位过滤