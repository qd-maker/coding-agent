# Simple Code 章节文档索引

每章使用 `spec.md / tasks.md / checklist.md` 管理需求、施工步骤和验收证据。

## 已完成并通过测试

| 章节 | 主题 | 文档 |
|---|---|---|
| CH2 | Provider、流式输出与对话管理 | [`ch2/`](ch2/) |
| CH3 | Tool Contract 与内置工具 | [`ch3/`](ch3/) |
| CH4 | Agent Loop 与上下文基础治理 | [`ch4/`](ch4/) |
| CH5 | System Prompt 设计 | [`ch5/`](ch5/) |
| CH6 | 权限系统与三种执行模式 | [`ch6/`](ch6/) |
| CH7 | MCP stdio / Streamable HTTP 外部工具接入 | [`ch7/`](ch7/) |
| CH8 | Token 估算、大结果落盘、Auto-Compact 与压缩后恢复 | [`ch8/`](ch8/) |
| CH9 | 项目指令、JSONL 会话存档与自动记忆 | [`ch9/`](ch9/) |
| CH10 | Slash Command、UIController 与 Tab 补全 | [`ch10/`](ch10/) |
| CH11 | 两阶段 Skill、inline/fork、LoadSkill 与目录能力包 | [`ch11/`](ch11/) |
| CH12 | Hook 生命周期、条件 DSL、自动化 Action 与工具拦截 | [`ch12/`](ch12/) |
| CH13 | Agent 定义、上下文 Fork、后台任务、工具过滤与 Trace | [`ch13/`](ch13/) |
| CH14 | Git Worktree 隔离、生命周期、恢复、保护与 SubAgent 集成 | [`ch14/`](ch14/) |
| CH15 | 长期 AgentTeam、共享任务、磁盘邮箱、恢复、合并与纯调度 | [`ch15/`](ch15/) |
| CH16 | 稳妥交付：先读后改、取消不断档、无人值守不卡、超限重试 | [`ch16/`](ch16/) |

完整的真实 Provider、MCP、TUI 与质量门禁证据见 [`ch2-12-live-audit.md`](ch2-12-live-audit.md)。
CH2–CH15 的最终完整性、性能、修复项和付费 API 验收见 [`final-audit.md`](final-audit.md)。

模块是否完成以对应 `checklist.md`、主流程接入和自动测试三者共同为准，不能只依据 spec 或
代码文件是否存在判断。
