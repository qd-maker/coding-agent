# ch05: System Prompt 设计 Checklist

> 所有条目必须可勾选、可观测。验收方式写在每项后面的括号里。

## 1. 实现完整性
- [x] 数据结构 `@dataclass class PromptSection` 含 `name / priority / content` 三字段
- [x] 数据结构 `PromptBuilder` 维护 `_sections`，`add` 可链式调用，`build` 稳定排序、过滤空段并用两个换行拼接
- [x] 函数 `environment_section(work_dir)` 使用 OS / release / `%Y-%m-%d`，返回 priority=70 的 PromptSection
- [x] 函数 `build_system_prompt` 按 8 段固定 + 3 段可选 + 1 段 hook 尾部顺序拼接
- [x] 7 个固定文本 section 常量齐全
- [x] Priority 数字固定：0/10/20/30/40/50/60/70
- [x] 可选 section priority 数字：80 / 90 / 95
- [x] Plan Mode 动态指令使用 `_REMINDER_INTERVAL = 5`、完整版和稀疏版，并保持 `WritePlan` 单路径约束
- [x] 函数 `build_environment_context` 参数为 `work_dir, active_skills, skill_catalog, agent_catalog`
- [x] 关键文本片段保留：安全、system-reminder、emoji、file_path:line_number、tool call 标点规范

## 2. 接入完整性（必查，杜绝死代码）
- [x] `build_system_prompt` 有 Agent 与测试真实调用方
- [x] `build_environment_context` 有 Agent、compact 恢复与测试真实调用方
- [x] `build_plan_mode_reminder` 有 Agent 与测试真实调用方
- [x] Agent.run 调用链：启动注入 environment，每轮构造 system prompt 并传给 client.stream
- [x] Plan Mode 调用链：每轮构造 reminder 并通过 `conversation.add_system_reminder` 注入 user channel
- [x] Compact 后恢复链重新注入 environment 与 long-term memory
- [ ] 已记录差异（不在本章 must-fix）:
 - [x] Python 版本未实现 Reentry reminder；Exit reminder 已由 ch04 `/do` 使用，本章保持兼容

## 3. 编译与测试
- [x] `ruff check mewcode/prompts.py mewcode/teams/coordinator.py mewcode/agent.py tests/test_prompts.py` 通过
- [x] `mypy mewcode/prompts.py mewcode/teams/coordinator.py mewcode/agent.py tests/test_prompts.py` 通过
- [x] `pytest tests/test_prompts.py` 覆盖 builder、固定/可选段、coordinator、Plan、environment、active skills、Agent 与 compact
- [x] `pytest tests/test_agent.py -k "system_prompt or plan or environment"` 通过

## 4. 端到端验证
- [x] Agent 与 `run_to_completion` 均通过同一主循环注入 environment，并把 system prompt 传给 LLMClient
- [x] Plan Mode 验证：`/plan` 后下一轮在 stream 前注入 `<system-reminder>` 包裹的五阶段 Workflow
- [x] Compact 恢复验证：专项测试确认 environment + long-term memory 被重新注入
- [x] 留存证据：`tests/test_prompts.py` 与完整测试通过

## 5. 文档
- [x] spec.md / tasks.md / checklist.md 三件套齐全（`docs/ch5/`）
- [ ] commit 信息标注 `ch05` 与三件套关闭状态（待统一打包提交）
