# CH15：长期协作 AgentTeam Spec

## 1. 目标

在 CH13 一次性 SubAgent 与 CH14 Git Worktree 隔离之上，增加可长期存在的团队：Lead、成员、
共享任务、磁盘邮箱、两类运行后端、对话恢复、事务式合并和可选纯调度模式。团队元数据位于
`~/.mewcode/teams/<team>/`，应用退出只停止本地协程，不删除团队和成员上下文。

## 2. 核心模型与持久化

- `AgentTeam`：`name / lead_agent_id / members / config_path / description`；原子保存
  `config.json`，支持按成员名或 agent id 查询。
- `TeammateInfo`：记录角色、模型、worktree、后端、活跃状态、审批要求与扩展 metadata。
- `SharedTaskStore`：原子更新 `tasks.json`；任务含负责人、状态以及 `blocks / blocked_by` 依赖。
- `Mailbox`：每条消息一个原子 JSON 文件，按文件名前缀 FIFO 消费；支持直接消息、广播、
  shutdown 生命周期消息和 approval reply。
- `transcripts/<agent-id>.json`：完整保存文本、thinking、tool_use、tool_result；空闲成员收到新消息时
  从原实例和磁盘 transcript 恢复，而不是重新 spawn。

## 3. 生命周期

1. `TeamCreate` 显式选择 `in-process`，或在交互终端按 tmux / iTerm2 优先级探测；无法满足
   隔离要求时直接报错，绝不静默降级。
2. `Agent(..., team_name=..., name=...)` 为成员创建独立 worktree，叠加 Agent definition、后台白名单
   和团队工具过滤，然后以协程或独立 CLI pane 启动。
3. 成员用 Task 工具更新共享清单，用 `SendMessage` 发送点对点或广播消息。Agent Loop 每轮调用
   LLM 前消费自己的 mailbox。
4. 成员自然完成后 transcript 落盘、状态变 idle，并向 Lead 写入通知。向 idle 成员发消息会恢复同一
   Agent 的上下文继续执行。
5. 所有成员 idle 且 Lead 工作区 clean 时，`TeamMerge` 逐个 `git merge --no-ff`；任一冲突会
   `merge --abort` 并 `reset --hard` 到合并前 HEAD，避免半合并状态。
6. `TeamDelete` 默认保护成员 worktree 中的未提交或未推送内容；只有全部 idle 才允许清理。

## 4. 运行后端

- `in-process`：同进程协程，LLM/Hook/文件系统基础设施共享；Conversation、权限记录、Token 与
  文件工具实例隔离。
- `tmux` / `iTerm2`：通过 `mewcode -p --work-dir ...` 启动完整独立 CLI；团队名、成员名和 mailbox
  路径经环境变量传递。tmux 使用 split/new-window/new-session 三级创建链，失败直接返回错误。
- `detect_backend` 支持显式 `in-process / tmux / iterm2 / auto`；非交互环境默认 in-process；
  交互环境不存在 pane backend 时要求用户显式配置。

## 5. 工具可见性与安全边界

- 主入口只看到 `TeamCreate`；普通 SubAgent 看不到 Team 工具。
- Team 创建后，Lead 才获得 `Agent / SendMessage / Task* / TeamMerge / TeamStop / TeamDelete`。
- 成员获得独立文件工具、共享任务和 `SendMessage`，但拿不到 `Agent / TeamCreate / TeamDelete`，
  防止无限嵌套或成员销毁团队。
- Agent definition 的 `tools / disallowedTools`、全局禁用、后台白名单和团队白名单逐层取交集。
- `SendMessage` 使用名称注册表寻址，仍校验目标必须属于当前团队；文本消息必须有摘要。

## 6. 纯调度模式

纯调度采用双锁：配置 `enable_coordinator_mode: true` 且环境变量
`MEWCODE_COORDINATOR_MODE=1|true|yes` 同时满足才激活。激活后 Lead 不拥有 Read/Glob/Grep、
Write/Edit/Bash 等代码与 shell 工具，只保留派生/终止成员、消息、共享任务、合并和最终输出。
系统提示固定引导 `Research → Synthesis → Implementation → Verification` 四阶段，但阶段判断由
Lead 完成，不引入脆弱的硬编码状态机。

## 7. Slash Command 与配置

```text
/team create <name> [description]
/team list
/team status [name]
/team tasks [name]
/team merge [name]
/team stop <member>
/team delete [name] [--discard]
```

```yaml
teammate_mode: in-process  # ""/auto/in-process/tmux/iterm2
enable_coordinator_mode: false
```

## 8. 错误与恢复约束

- 非法团队/成员名、未知目标、重复名称、不可用 backend、active 成员删除、dirty Lead 合并、Git 冲突
  都返回可读错误，不伪装成功。
- Mailbox 坏文件跳过并保留诊断；团队 JSON 无法解析时不进入缓存。
- App shutdown 取消本地任务前保存 transcript 并把成员标记 idle；磁盘团队不自动删除。
- Pane 已消失时恢复请求明确失败，不静默切换为 in-process。

## 9. 明确不做

- 跨主机 mailbox、分布式锁和远程队列。
- 自动拓扑调度算法；依赖字段由 Lead 解释与更新。
- 自动解决语义级 Git 冲突；无法安全合并时整体回滚并上报。
- Agent Team 市场、云端控制台与多租户权限模型。

## 10. 完成定义

以 `tasks.md`、`checklist.md` 和全仓质量门禁为准；文档只描述已接入的能力。
