# ch11: Skill 系统 Tasks（Python 版）

> 顺序执行。每完成一个任务跑 `ruff check mewcode/skills mewcode/tools/load_skill.py` 与 `pytest tests/test_skills.py -q` 确保通过；接入主流程的任务（T10、T11、T12）做完后立刻补一次端到端验证再进下一项。

## T1: 定义 SkillDef 数据结构与 frontmatter 解析

- 影响文件: `mewcode/skills/parser.py`（新建）
- 依赖任务: 无
- 完成标准: dataclass `SkillDef` 含 `name / description / prompt_body / allowed_tools / mode / model / context / source_path / is_directory`；`parse_frontmatter(raw) -> (meta, body)` 处理 `---\n...\n---\n<body>` 格式；`_validate_meta` 校验 `name` 正则 `^[a-z][a-z0-9\-]*$`、`mode in {inline, fork}`、`context in {full, recent, none}`；`SkillParseError` 自定义异常类
- 备注: yaml 库走 `import yaml`（pyyaml）；`substitute_arguments(prompt_body, args)` 简单 `.replace("$ARGUMENTS", args)` 即可

## T2: 实现 SkillLoader 三级搜索与热重载

- 影响文件: `mewcode/skills/loader.py`（新建）
- 依赖任务: T1
- 完成标准:
  - 常量 `PROJECT_SKILLS_DIR = ".mewcode/skills"` / `USER_SKILLS_DIR = "~/.mewcode/skills"`
  - `SkillLoader(work_dir)` 构造时计算 `_project_dir` / `_user_dir`
  - `load_all()` 按 project → user → builtin 顺序扫描，首次出现的 name 保留，后续跳过；维护 `_skills` 与 `_cache` 两份字典
  - `_scan_directory(path, source)` 同时处理 `*.md` 与 `<dir>/SKILL.md` 两种布局，目录型 skill `is_directory = True`
  - `_load_builtins()` 走 `importlib.resources.files("mewcode.skills.builtins")` 遍历子目录
  - `get(name)` 命中后 `parse_skill_file(source_path)` 强制重读；失败回退 `_cache` 中旧版本并 `log.warning`
  - `get_catalog()` 返回 `[(name, description), ...]`；`get_source_label(name)` 按路径前缀返回 `project | user | builtin`
- 备注: 解析失败用 `log.warning("Skipping %s skill '%s': %s", ...)` 不抛出

## T3: 内置 skill 资源

- 影响文件: `mewcode/skills/builtins/__init__.py`（新建空文件）、`mewcode/skills/builtins/commit/SKILL.md`、`mewcode/skills/builtins/commit/__init__.py`、`mewcode/skills/builtins/review/SKILL.md`、`mewcode/skills/builtins/review/__init__.py`、`mewcode/skills/builtins/test/SKILL.md`、`mewcode/skills/builtins/test/__init__.py`、`mewcode/skills/builtins/backend-interview/SKILL.md`、`mewcode/skills/builtins/backend-interview/__init__.py`、`mewcode/skills/builtins/backend-interview/tool.json`、`mewcode/skills/builtins/backend-interview/references/parse_resume.py`
- 依赖任务: T2
- 完成标准:
  - `commit/SKILL.md`：`mode: inline / allowedTools: [Bash, ReadFile, Grep]`，body 描述 git status → diff → conventional commit
  - `review/SKILL.md`：`mode: fork / context: none / allowedTools: [Bash, ReadFile, Grep, Glob]`，body 描述 5 维度审查（逻辑/安全/性能/风格/可维护性）+ Critical/Warning/Info 分级
  - `test/SKILL.md`：`mode: inline / allowedTools: [Bash, ReadFile, Grep, Glob]`，body 描述项目类型检测（`pyproject.toml` → `pytest`、`go.mod` → `go test`、`package.json` → `npm test`、`Cargo.toml` → `cargo test`）+ 区分代码 bug 与测试 bug
  - `backend-interview/`：目录型，`tool.json` 声明 `parse_resume` schema，`references/parse_resume.py` 内 `async def execute(file_path: str = "", **kwargs) -> str` 实现
- 备注: `pyproject.toml` 的 `[tool.setuptools.package-data]` 需要把 `mewcode.skills.builtins` 的 `*.md / *.json` 也打包

## T4: 工具白名单与系统工具豁免

- 影响文件: `mewcode/tools/base.py`（修改，加 `is_system_tool` 字段）、`mewcode/skills/executor.py`（新建，部分实现）
- 依赖任务: T1
- 完成标准:
  - `Tool` 抽象基类增加类属性 `is_system_tool: bool = False`
  - 同文件常量 `SYSTEM_TOOL_NAMES = frozenset({"LoadSkill"})`
  - `SkillDependencyError` 异常类在 `mewcode/skills/executor.py` 定义
  - `filter_tool_registry(registry, allowed)` 返回新 `ToolRegistry`：`allowed` 为空时直接返回原 registry；遍历 `allowed` 缺工具 `raise SkillDependencyError`；扫描原 registry 把 `is_system_tool=True` 的工具自动透传

## T5: SkillExecutor.execute_inline

- 影响文件: `mewcode/skills/executor.py`（继续）
- 依赖任务: T2, T4
- 完成标准: `class SkillExecutor(agent, client, protocol)` 三个属性持有；`execute_inline(skill, args) -> None`：
  - `substitute_arguments(skill.prompt_body, args)`
  - `agent.activate_skill(skill.name, rendered)`
  - 不需要立即调用 LLM，rendered body 钉到 env 后由 command handler 再 `ctx.ui.send_user_message(trigger)` 触发 loop
- 备注: 工具过滤在 fork 路径才动手；inline 走主 registry，由 Agent loop 每轮根据 ActiveSkills 自然限制工具

## T6: SkillExecutor.execute_fork

- 影响文件: `mewcode/skills/executor.py`（继续）
- 依赖任务: T5
- 完成标准: `async execute_fork(skill, args) -> str`：
  - 渲染 prompt
  - 新 `ConversationManager()`
  - 根据 `skill.context` 装填历史：`none` 空 / `recent` 取 `agent._conversation.history` 最近 5 条 user/assistant 消息 / `full` 拼成一段 `"## Previous conversation summary\n\n"` summary 作为单条 user message
  - `fork_conv.add_user_message(rendered)`
  - `filter_tool_registry(agent.registry, skill.allowed_tools)` 失败返回错误字符串
  - 局部 `from mewcode.agent import Agent as AgentClass, StreamText, LoopComplete, ErrorEvent`（避免循环 import）构造临时 Agent，沿用 `client / protocol / work_dir / max_iterations / context_window`
  - `async for event in fork_agent.run(fork_conv)`：`StreamText` 追加文本，`ErrorEvent` 追加错误标记，`LoopComplete` break
  - 返回 `"".join(result_parts)`

## T7: Agent 集成 active_skills 与 skill_catalog

- 影响文件: `mewcode/agent.py`（修改）、`mewcode/prompts.py`（修改）
- 依赖任务: 无（与 T1-T6 并行可做）
- 完成标准:
  - `Agent.__init__` 增加 `self.active_skills: dict[str, str] = {}` 与 `self._skill_catalog: str = ""`
  - 方法 `activate_skill(name, prompt_body)` / `clear_active_skills()` / `set_skill_catalog(catalog)`
  - 每轮 `_build_system_message`（或同等位置）调用 `build_environment_context(work_dir, active_skills, skill_catalog, agent_catalog)`
  - `mewcode/prompts.py` 的 `build_environment_context` 拼接：先写 `skill_catalog` 段落，再写 `## Active Skills` 标题 + `### Skill: <name>\n<sop>` 子段

## T8: LoadSkill 工具

- 影响文件: `mewcode/tools/load_skill.py`（新建）
- 依赖任务: T2, T7
- 完成标准:
  - `LoadSkill` 继承 `Tool`，`name = "LoadSkill"`、`description` 描述「按需激活 skill」、`params_model = LoadSkillParams(name: str)`、`category = "read"`、`is_concurrency_safe = False`、`is_system_tool = True`
  - 持有 `_loader` 与 `_agent` 私有属性；`set_loader(loader)` / `set_agent(agent)` 注入器
  - `execute(params)`：
    - 未初始化返回 `is_error=True` 的「LoadSkill not properly initialized」
    - `self._loader.get(params.name)` 为 None 时列出 catalog 返回错误
    - 调 `self._agent.activate_skill(skill.name, skill.prompt_body)`
    - 目录型且 `source_path is not None` 时局部 import `register_skill_tools` 并调用，count 累加
    - 返回 `"Skill '<name>' activated. SOP pinned to environment context."` + 若有工具 `" N specialized tool(s) registered."`

## T9: 目录型 Skill 工具注册

- 影响文件: `mewcode/skills/directory.py`（新建）
- 依赖任务: T8
- 完成标准:
  - `parse_tool_json(path) -> list[dict]`：`json.loads`，支持单 dict 包装成 list，失败 warning 后返回空 list
  - `load_tool_implementation(references_dir, tool_name) -> Callable | None`：`importlib.util.spec_from_file_location("mewcode_skill_tool_<name>", references_dir / f"{tool_name}.py")` 动态加载，读取 `execute` 函数；找不到/失败时 warning 后返回 None
  - `_DynamicParams(BaseModel)` 配 `model_config = {"extra": "allow"}` 用作动态参数模型
  - `SkillCustomTool(tool_name, description, schema, impl)` 继承 `Tool`：`get_schema` 用 `schema["parameters"]` 或 `schema["input_schema"]` 作为 `input_schema`；`execute(params)` 检查 `impl` 是否为协程，分别 `await impl(**kwargs)` 或 `impl(**kwargs)`，包成 `ToolResult(output=str(result))`，异常包成 `is_error=True`
  - `register_skill_tools(skill_dir, registry) -> int`：找 `tool.json` 没有返回 0；遍历 schemas，跳过同名已注册，新建 `SkillCustomTool` 注册并 +1

## T10: 接入 app.py —— 加载 + Catalog 注入 + 命令注册

- 影响文件: `mewcode/app.py`（修改）
- 依赖任务: T2, T3, T5, T6, T7, T8
- 完成标准:
  - import `SkillLoader / SkillExecutor / register_skill_commands / LoadSkill`
  - `MewCodeApp.__init__` 字段 `self.skill_loader / self.skill_executor / self._load_skill_tool`
  - 先 `LoadSkill()` 实例化注册到 `self.registry`，再构造 `Agent`（保证 registry 已含 LoadSkill）
  - `SkillLoader(work_dir).load_all()` 加载 catalog
  - `load_skill_tool.set_loader(self.skill_loader)` / `set_agent(self.agent)` 注入