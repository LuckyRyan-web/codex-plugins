# 安全政策

## 支持版本

| 版本 | 支持 |
| --- | --- |
| 0.1.x | 是 |

## 报告漏洞

请使用本仓库 GitHub Security 页中的私密 **Report a vulnerability** 流程。
不要在公开 Issue 中提交凭据、私有提示词、专有源码或其他敏感数据。

请提供插件版本、Codex 版本、操作系统、最小复现与影响。维护者预计在七天内
给出首次回复。

## 本地执行与模型请求

Task Completion Guard 的生命周期 Hook 在本机执行随插件提供的 Python
脚本。用户必须先审阅并信任准确的 Hook 定义。

脚本自身不发送遥测，也不调用模型。`prepare-review` 只准备本地材料，
`review-claim` 只参与身份认领。需要独立验收时，主 Agent 使用 Codex 原生
`spawn_agent` 启动子 Agent；该子 Agent 会调用当前模型并消耗账号额度。
原始用户要求、验收要求和其读取的代码或证据会进入子 Agent 的模型上下文，
不能将整个独立验收流程描述为“任务数据不离开本机”。

## 保存的数据

私有数据目录为
`PLUGIN_DATA/completion-guard/v1/sessions/<session-hash>/`。
当 `PLUGIN_DATA` 未设置或为空时，使用 `CLAUDE_PLUGIN_DATA`。

| 位置 | 内容 |
| --- | --- |
| `active.json` | 任务元数据、审计路径、验收请求与状态、宿主子 Agent 身份、尝试次数等。验收请求包含原始用户请求和证据说明。 |
| 事件记录 | 工具名、分类、标签、时间、结果和输入输出的 SHA-256 哈希；常规事件不保存原始工具内容。 |
| `audits/<task-id>/` | 审计诊断、修改与验证计数。详细诊断经内部 Hook 上下文提供给 Codex。 |
| `reviews/<task-id>/` | 来自子 Agent 最终消息的验收报告及宿主身份；报告可能引用源码或需求。 |

系统临时目录下的
`codex-task-completion-guard/<session-hash>/` 还会保存：

- `<task-id>.json`：通过 `submit` 标准输入提交的验收项、证据、摘要、问题或阻塞原因；
- `<task-id>.context.json`：为验收交接收集的原始用户请求及补充要求；
- `<task-id>.review-request.json`：验收要求、风险、原始请求、验证说明与快照元数据；
- `<task-id>.review-<run-id>/`：快照文件副本、`diff.patch` 和文件摘要清单。

因此，只有常规事件以哈希为主，**全部插件数据并非只有哈希**。代码、diff、
请求和报告均可能含有敏感内容。Git 快照自动纳入所有已修改和未跟踪文件，
包括本次工作前就存在的脏改动；显式传入 `paths` 不能将这些文件排除。

在 POSIX 系统上，新建私有目录和可写 JSON 文件使用 `0700`、`0600`。
保存后的快照目录和文件使用 `0500`、`0400`，用于减少意外改动。插件不会在
完成或中断时自动清理这些记录，也不解析 Codex transcript 格式。

主 Agent 面向用户的最终回复不得附带审计 JSON 或 completion marker。

## 身份、读取限制与边界

独立验收要求原样使用 `prepare-review` 返回的交接参数，并显式设置
`fork_turns: "none"`。子 Agent 先执行 `review-claim`，Hook 结合已观察到的
spawn、`SubagentStart` 和该工具事件中的真实 `agent_id` 绑定身份；最终
结论取自对应的 `SubagentStop.last_assistant_message`，不接受主 Agent
自报“独立验收通过”作为证据。

认领后的验收 Agent 使用允许的 `cat`、`rg` 等命令读取预生成的 `diff.patch`、
快照和必要依赖，不直接运行 Git 命令；Git 内容过滤器也可能执行仓库配置的程序。
这种隔离是编码历史隔离与工作流检查。读取限制由提示和 Hook 实现，
**不承诺操作系统级 sandbox**。文件权限也无法阻止同一用户主动修改脚本、
状态或权限。本插件不构成针对恶意用户、恶意本地进程或所有提示注入的安全边界；
仓库内容应被验收 Agent 作为数据处理。

独立验收最多两次，即初次加一次修复后复审。需要验收的任务只有报告通过、
所有要求有证据且快照仍为当前版本时，才满足完成条件。缺少身份事件、材料、
证据或有效报告，都不能证明通过。独立模型仍可能判断错误。

Stop 阻止续跑最多三次；内部异常、连续无进展或达到上限时会 fail-open。
这只允许会话结束，不代表任务或独立验收已通过。升级前没有 `review` 状态的
旧 active 任务保留原有完成协议，不被追溯要求独立验收。
