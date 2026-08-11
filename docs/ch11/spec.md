# CH11：Skill 系统 Spec

## 1. 背景

CH10 的 `/review` 仍是写死在 Python 中的 Prompt。可复用 SOP 如果继续硬编码，会同时带来
编辑需要编译、全部指令常驻上下文、工具过多难以选择、无法按任务收窄权限等问题。Skill
系统将 SOP 和元数据放进 Markdown，并采用 catalog → 激活完整 SOP 的两阶段加载。

## 2. 目标与范围

- YAML frontmatter 保存元信息，Markdown body 保存完整 SOP。
- 项目级 > 用户级 > 内置级三级发现，同名覆盖、单文件失败隔离、调用时热加载。
- 支持 inline/fork、`$ARGUMENTS`、full/recent/none 上下文和 allowedTools 最小权限。
- 支持单文件 Skill 与 `SKILL.md + tool.json + references/` 目录能力包。
- 提供 `LoadSkill` 系统工具、动态 `/<name>` 命令和 `/skill list/info/reload`。
- 内置且只内置 commit、review、test 三个 Skill。

Skill 市场、远程安装、版本管理不在本章范围。

## 3. 文件格式

```yaml
---
name: commit
description: Inspect changes and create a focused commit
allowedTools: [Bash, ReadFile, Grep]
mode: inline
model: inherit
context: full
---
# Commit SOP
User request: $ARGUMENTS
...
```

- `name` 必须匹配 `^[a-z][a-z0-9-]*$`。
- `description` 和 Markdown body 不能为空。
- `mode` 为 `inline | fork`，默认 inline。
- `context` 为 `full | recent | none`，默认 full，仅 fork 使用。
- `allowedTools` 必须是非空字符串列表；空列表表示不收窄工具。
- `model` 保存 Skill 的模型偏好；`inherit` 表示沿用当前 Provider。

## 4. 功能需求

### 4.1 解析与加载

- F1：`SkillDef` 包含 name、description、prompt_body、allowed_tools、mode、model、
  context、source_path、is_directory。
- F2：解析异常统一为 `SkillParseError`；加载器捕获单文件错误、warning 后继续。
- F3：搜索顺序固定为 `<project>/.mewcode/skills`、`~/.mewcode/skills`、
  `mewcode.skills.builtins`，首次出现的同名定义获胜。
- F4：支持 `<skills>/<name>.md` 和 `<skills>/<name>/SKILL.md` 两种入口。
- F5：`load_all` 全量重扫；`get(name)` 每次重读源文件，失败回退最后有效缓存。
- F6：启动期 catalog 只含 name/description 和 LoadSkill 指引，不含完整 SOP。

### 4.2 执行与上下文

- F7：inline 渲染 `$ARGUMENTS` 后激活到主 Agent environment，再用参数或默认触发语句
  进入正常 Agent Loop；结果留在主对话。
- F8：fork 使用独立 ConversationManager/Agent；full 携带全量文本摘要，recent 携带最近
  5 条消息，none 完全隔离；结果以 `[<name> skill result]` 回流 UI，不污染主历史。
- F9：完整 SOP 通过 `## Active Skills / ### Skill: <name>` 固定在 environment message；
  Agent 每轮 Provider 请求前刷新该消息，使上一轮 LoadSkill 激活立即生效。
- F10：`/clear` 同时清空 active skills 和对应工具白名单。

### 4.3 工具最小权限

- F11：allowedTools 非空时，模型可见 schema 与实际工具执行同时受限，不能用伪造 tool call
  绕过。
- F12：多个激活 Skill 的白名单取交集；`is_system_tool=True` 的 LoadSkill 始终保留，允许
  Skill 嵌套。
- F13：App 构造时 fail-fast 验证依赖；目录 Skill 的 tool.json 声明也计入启动校验；
  热加载执行前再次验证。
- F14：`filter_tool_registry` 为 fork 创建独立 Registry；缺少依赖抛
  `SkillDependencyError`。

### 4.4 LoadSkill 与目录能力包

- F15：LoadSkill 是 `category=read`、`is_system_tool=True` 的内置 Tool；未知名称返回 catalog，
  成功后只返回简短确认，不把 SOP 复制进 tool result。
- F16：目录型 Skill 的 tool.json 支持单对象或数组；schema 使用 parameters/input_schema。
- F17：`references/<tool-name>.py` 必须导出同步或异步 `execute(**kwargs)`；通过 importlib
  动态加载，调用异常转换为 ToolResult error。
- F18：同名工具已经注册时跳过，避免覆盖核心 Tool。

### 4.5 命令与内置 Skill

- F19：所有 Skill 自动注册 `/<name>`，描述带 `[skill]`；review Skill 替换 CH10 的硬编码
  `/review`。
- F20：`/skill list` 显示模式/来源，`info` 显示元数据、路径和 SOP，`reload` 重扫、校验并
  同步刷新 catalog 与动态命令。
- F21：内置 commit 为 inline；review 为 fork/context:none；test 为 inline。三个资源必须
  被 sdist/wheel 打包。

## 5. 非功能需求

- N1：Skill 包不依赖 Textual；UI 交互仍通过 CommandContext/UIController。
- N2：主对话和 fork 对话对象完全隔离。
- N3：动态目录工具与宿主 Python 同权限运行，本章不提供脚本沙箱。
- N4：命令冲突默认跳过；只允许内置 review Skill 替换同名 CH10 命令。
- N5：后台 fork task 在 App 卸载时取消并回收。
- N6：运行时不访问网络下载 Skill，也不实现市场和版本解析。

## 6. 主调用链

1. App 创建 Registry → 注册 LoadSkill → 构造 Agent。
2. SkillLoader 扫描三级目录 → fail-fast 依赖检查 → Agent 注入摘要 catalog。
3. register_skill_commands 将三个内置和项目/用户 Skill 注册成短命令。
4. 自然语言匹配：Agent 调 LoadSkill → 激活 SOP/目录工具 → 下一轮刷新 environment →
   白名单过滤后的工具集发送 Provider。
5. 显式 inline：`/commit args` → 热读 → execute_inline → activate_skill → 主 Agent。
6. 显式 fork：`/review args` → 后台独立 Agent → 结果回流 UI。

## 7. 完成定义

以 [checklist.md](checklist.md) 为准；实现、App 真实调用链、自动测试、静态检查和打包验证
必须同时通过。
