# CH11：Skill 系统 Tasks

## T1：先更新 API Contract

- 文件：`docs/api-contract.md`
- 产出：文件格式、搜索优先级、两阶段加载、执行模式、工具边界和命令语义。
- 状态：[x]

## T2：实现定义与解析器

- 文件：`simplecode/skills/parser.py`
- 产出：SkillDef、SkillParseError、frontmatter 解析/校验、文件解析、参数替换。
- 状态：[x]

## T3：实现三级加载与热更新

- 文件：`simplecode/skills/loader.py`
- 产出：project/user/builtin 搜索、同名优先级、坏文件隔离、缓存回退、catalog/source label。
- 状态：[x]

## T4：实现目录型 Skill

- 文件：`simplecode/skills/directory.py`
- 产出：tool.json 解析、references 动态加载、SkillCustomTool、重复保护和注册计数。
- 状态：[x]

## T5：实现执行器和工具过滤

- 文件：`simplecode/skills/executor.py`
- 产出：依赖 fail-fast、系统工具豁免、Registry 过滤、inline/fork、三档上下文。
- 状态：[x]

## T6：接入 Agent 两阶段加载

- 文件：`simplecode/agent.py`、`simplecode/conversation.py`、`simplecode/prompts.py`
- 产出：active skills、白名单交集、catalog setter、每轮 environment 刷新、schema/执行双重过滤。
- 状态：[x]

## T7：实现 LoadSkill Tool

- 文件：`simplecode/tools/load_skill.py`
- 产出：渐进式激活、未知名称反馈、目录工具注册、运行期依赖复验。
- 状态：[x]

## T8：实现 Skill 命令

- 文件：`simplecode/commands/handlers/skill.py`、`skill_register.py`、
  `simplecode/commands/registry.py`、`clear.py`
- 产出：动态短命令、review 替换、list/info/reload、unregister、clear 清理。
- 状态：[x]

## T9：接入 App 生命周期

- 文件：`simplecode/app.py`
- 产出：LoadSkill/Loader/Executor 装配、启动依赖校验、catalog 注入、命令注册、后台 task 回收。
- 状态：[x]

## T10：添加三个内置 Skill 与打包规则

- 文件：`simplecode/skills/builtins/{commit,review,test}/SKILL.md`、`pyproject.toml`
- 产出：inline/fork 样例 SOP 和 package-data。
- 状态：[x]

## T11：测试和文档收口

- 文件：`tests/test_skills.py`、`tests/test_tui.py`、`docs/ch11/`、README。
- 验收：完整 pytest、Ruff、Mypy、compileall、`uv build`。
- 状态：[x]
