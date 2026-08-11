# CH11：Skill 系统 Checklist

> 验收以当前工作树中的符号、调用链和自动测试为准，不依赖课程仓库行号或历史 commit。

## 1. 解析与加载

- [x] SkillDef 九字段、SkillParseError 和 YAML/Markdown 分离已实现。
- [x] name/mode/context/description/body/allowedTools 校验已覆盖正常和异常测试。
- [x] `$ARGUMENTS` 只替换占位符，没有占位符时不追加参数。
- [x] project > user > builtin 三级加载、同名覆盖和 source label 已测试。
- [x] 单文件与目录型 SKILL.md 均可发现。
- [x] 单文件解析失败 warning 后跳过，其他 Skill 正常加载。
- [x] get(name) 热读成功更新缓存；热读失败回退最后有效版本。
- [x] catalog 只包含名称/描述和 LoadSkill 指引，不包含完整 SOP。

## 2. Executor 与工具边界

- [x] inline 激活渲染后的 SOP 和 allowedTools，不直接调用 LLM。
- [x] fork 使用独立 ConversationManager/Agent，主历史不变。
- [x] full/recent/none 三种 fork 上下文策略均有测试。
- [x] filter_tool_registry 缺依赖抛 SkillDependencyError。
- [x] LoadSkill 作为 system tool 在过滤后仍保留。
- [x] 多个 Skill 白名单取交集，LoadSkill 仍保留。
- [x] 模型 schema 和伪造 tool call 的实际执行入口均执行白名单检查。
- [x] App 启动时缺依赖立即失败；目录 tool.json 声明计入启动检查。

## 3. LoadSkill 与目录型 Skill

- [x] LoadSkill 为 read/system/plan-safe Tool，支持 loader/agent 注入。
- [x] 自然语言路径的测试覆盖 catalog → LoadSkill → 下一轮 Active Skills。
- [x] LoadSkill 返回简短确认，不在 tool result 回传完整 SOP。
- [x] tool.json 支持对象和数组，parameters/input_schema 原样形成 schema。
- [x] references 下同步/异步 execute 均可动态加载。
- [x] 目录专属工具可注册、执行，重复注册返回 0。
- [x] 动态工具异常转换为结构化 ToolResult error。

## 4. Agent 与命令接入

- [x] Agent 维护 active_skills、白名单状态和 skill catalog。
- [x] 每轮 Provider 请求前 refresh_environment，上一轮激活的 SOP 下一轮可见。
- [x] `/clear` 同时清除 active skills 和白名单。
- [x] `/skill list/info/reload` 已接入 CommandRegistry。
- [x] 所有 Skill 自动注册为 `[skill]` 短命令。
- [x] CH10 硬编码 review 被 fork review Skill 替换。
- [x] inline `/commit` 进入主 Agent；fork `/review` 结果回流且主历史不污染。
- [x] 项目 Skill 修改后不重启即可通过短命令加载新正文。
- [x] App 卸载会取消并回收 fork 后台 task。

## 5. 内置 Skill 与明确边界

- [x] 内置 Skill 恰好为 commit、review、test。
- [x] commit：inline，允许 Bash/ReadFile/Grep，含 status/diff/stage/commit/verify SOP。
- [x] review：fork + context:none，含逻辑/安全/性能/风格/维护性和分级。
- [x] test：inline，含 Python/Go/Node/Rust 项目识别和产品 bug/测试 bug 区分。
- [x] 未实现 Skill 市场、远程安装和版本管理。

## 6. 自动验证

- [x] `tests/test_skills.py`：25 个专项测试通过。
- [x] TUI 自动测试覆盖 help/catalog/info、inline、fork、clear、热加载和启动 fail-fast。
- [x] 完整回归：`370 passed, 1 skipped`。
- [x] `ruff check .` 通过。
- [x] `mypy mewcode` 通过：77 个 source files。
- [x] `python -m compileall -q mewcode tests` 通过。
- [x] `uv build` 成功，wheel/sdist 均包含三个 SKILL.md。

## 7. 文档

- [x] API Contract 已加入 Skill Contract。
- [x] spec/tasks/checklist 已按实际实现更新。
- [x] README 与章节索引更新到 CH11。
