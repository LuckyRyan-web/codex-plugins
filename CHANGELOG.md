# Changelog

All notable changes to this repository are documented here.

## [Unreleased]

### 修复

- 需要用户确认的阶段优先进入 `waiting_user`，避免误填 `complete` 或未知验证
  结果导致门禁反复催促继续；等待不等于验收通过。
- 等待期间重复 Stop 与旧 Hook 续跑保持幂等，真实用户回复后才重新处理任务。
- 完成上下文提供完整 `needs_user` 示例，明确等待状态无需先运行独立验收。
- 口语化提问不再被当成新的任务要求。此前只识别“开头是疑问词”或“结尾有问号”
  的提问，像“你怎么知道我的项目路径的”“是不是安装时就写死了”这类中文常见
  问法都会重新激活任务、让已提交的声明立即过期，模型答完就被推回。
- 提问不再作废已提交的完成声明与已准备的验收范围；任务进行中被提问时，回答
  这一轮不再要求重新提交完成审计（该轮内一旦真的动了文件，仍按原规则判定）。
- 独立验收管线自身失败（子 Agent 起不来、身份始终无法绑定、已用尽验收次数）
  时允许 `status: "blocked"` 收尾。此前这类失败的工具事件被验收网关吞掉，
  `blocked` 因“没有观察到失败事件”被拒，任务只能反复推回直至降级。
- 出现上述失败时，Stop 推回改为“独立验收无法完成，请修复后重试，或用
  blocked 状态说明具体原因”，替代无差别的通用提示。
- `prepare-review` 因快照范围超限（单文件 2 MiB、总量 8 MiB、200 文件、
  diff 体积）而无法准备时，同样允许 `blocked` 收尾。此前只覆盖“子 Agent 起不
  来”，准备阶段就失败则 `review.current` 从未建立，`blocking_failure` 认不出
  来；那条失败的命令又因含 `completion_guard.py` 被归为 guard 事件而不入账，
  两路证据同时为空，任务只能被通用提示反复推回直到 fail-open。
- 快照超限与请求本身被拒（要求条数不合法等）分开处理：只有前者由
  `prepare-review` 自己写下记录并计入阻塞，换参数重跑能解决的错误不放行
  `blocked`。每次准备先清空该记录，所以之后任何一次没有再触发超限的准备
  （成功或仅被拒），都会让旧的超限失效。
- 超限时 PostToolUse 当场告知具体限制，Stop 推回改为“独立验收范围超出快照
  限制，重试无效；请用 blocked 状态说明原因收尾”，不再让模型去重试。

### 调整（完成门槛）

- `git diff --check` 不再计入验证：它只扫描空白字符与冲突标记，不构成改动
  可用的证据。
- 改动包含代码或配置文件时不再接受 `verification.status: "not_applicable"`。
- `waived` 验收项需要具体 `reason` 与支持它的 `evidence`，不能只给一个标签。
- `prepare-review` 机械核对用户自己列出的编号/项目符号条数，`requirements`
  少于该条数时拒绝准备，避免验收项在转述中缩水。
- `routine` 标签只能为纯文案改动免除独立验收：改动一旦含代码或配置文件，
  无论多小都回落到默认规则，作者不能自行发放免检。

### 新增

- 独立完成验收：`prepare-review` 保存私有要求、文件快照和 diff；主 Agent
  使用原生 `spawn_agent` 原样传入交接，以 `fork_turns: "none"` 创建子 Agent。
- 子 Agent 首先 `review-claim`，通过宿主 `agent_id` 绑定身份；从
  `SubagentStop` 收集真实报告，并在结束前核对要求与当前代码快照。
- 风险分流：`routine` 仅限不超过 1 文件、20 行的细小低风险调整；`auto` 对
  代码/配置、超过 2 文件或 80 行的改动要求验收；`important` 和 critical 路径
  始终要求验收。每个任务最多初次验收加一次修复后复审。

### 调整

- 子 Agent 工具事件不计入主任务修改与验证证据；旧 active 状态保持原有协议。
- 文档说明独立验收的额度消耗，以及原始用户请求、代码、diff 和报告的私有存储；
  Python 脚本本身不发起模型请求。
- 明确读取限制不是操作系统 sandbox；fail-open 不等于独立验收通过。

## [0.1.0] - 2026-09-04

### Added

- Initial Task Completion Guard plugin.
- Natural-language enrollment for implementation and state-changing tasks.
- Observable mutation and verification tracking without retaining raw tool data.
- Bounded Stop-hook enforcement with explicit completion, user-decision, and external-blocker states.
- Repository marketplace metadata, package tests, and GitHub Actions coverage.

[0.1.0]: https://github.com/LuckyRyan-web/codex-plugins/releases/tag/v0.1.0
