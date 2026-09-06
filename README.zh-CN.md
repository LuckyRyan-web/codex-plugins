# LuckyRyan Codex 插件

[English](README.md)

这是由 [LuckyRyan-web](https://github.com/LuckyRyan-web) 维护的 Codex 插件
Marketplace 仓库。

## Task Completion Guard

Task Completion Guard 用于减少 Codex 在实现任务中只完成一个中间步骤、给出
进度汇报后就提前结束的问题。

它结合以下机制：

- Skill：要求 Codex 根据用户完整的自然语言需求自动提取验收项；
- 生命周期 Hook：识别执行型任务，观察本地修改与验证事件；
- 独立验收：为需要复核的改动创建不继承编码历史的子 Agent，逐项检查固定快照；
- Stop Hook：在任务结束前审计完成声明，发现缺项时真正阻止本轮结束并要求
  Codex 继续。

用户不需要自己编写停止条件，也不需要调用 `/goal`。

> [!IMPORTANT]
> 本插件会在本机执行 Python Hook。安装插件并不等于信任 Hook；你必须使用
> `/hooks` 阅读并信任准确的 Hook 定义后，它才会运行。

### 环境要求

- 当前支持 Plugin 与生命周期 Hook 的 Codex 版本；
- 系统可以通过 `python3` 启动 Python 3；
- 当前已测试的命令路径适用于 macOS 和 Linux。

0.1.0 在 macOS 上使用 Codex CLI 0.153.0-alpha.5 和 Python 3.14 完成
本地测试；CI 还会在 Linux 上运行 Python 3.9、3.11 和 3.13。由于 Windows
专用 Hook 命令尚未实际验证，本版本暂不声明 Windows 支持。

### 安装

先把这个 GitHub 仓库添加为 Codex Marketplace：

```bash
codex plugin marketplace add LuckyRyan-web/codex-plugins --ref main
```

然后安装插件：

```bash
codex plugin add task-completion-guard@luckyryan-codex-plugins
```

安装后请根据使用方式操作：

- **Codex 桌面应用：**重启应用并新建一个任务；
- **Codex CLI：**退出安装前已经运行的会话，然后重新启动一个 `codex` 会话。

在新任务或新会话中输入 `/hooks`，阅读 Task Completion Guard 提供的七个
Hook，并选择信任。

如果你希望把 Marketplace 固定在首个发行版，而不是跟随 `main`：

```bash
codex plugin marketplace add LuckyRyan-web/codex-plugins --ref v0.1.0
```

### 使用

继续像平常一样用自然语言给 Codex 下达任务。例如：

```text
把这个功能完整实现，并完成必要验证。
修复登录问题，补回归测试并更新文档。
Implement this feature completely and verify the result.
```

插件不会为以下请求开启完成门禁：

- 只解释；
- 只读分析；
- 只审查；
- 只写计划；
- 明确暂停或停止。

如果某一次任务不希望启用门禁，在请求中加入：

```text
[completion-guard:off]
```

### 工作原理

插件监听七个 Codex 生命周期事件：

| 事件 | 作用 |
| --- | --- |
| `UserPromptSubmit` | 判断是否为执行任务，并向模型注入完成协议。 |
| `PreToolUse` | 检查独立验收的原始交接参数、`fork_turns: "none"`、次数与快照；限制已绑定验收 Agent 的工具调用。 |
| `PostToolUse` | 记录主任务的修改和验证证据；观察验收准备、spawn 返回与子 Agent 的身份认领。 |
| `SubagentStart` | 记录宿主提供的子 Agent 身份。 |
| `SubagentStop` | 从该子 Agent 的最终消息收集验收结论。 |
| `Stop` | 检查结构化完成声明；不符合条件时阻止提前结束。 |
| `Interrupt` | 用户主动中断时暂停当前门禁。 |

新任务在结束前先运行 Hook 提供的 `prepare-review` 命令，从标准输入传入完整
验收项、文件路径、风险等级和已执行的验证证据。命令保存私有验收请求、文件副本、
diff 与摘要；不调用模型。Git 项目会自动纳入所有已修改和未跟踪文件，传入
`paths` 不会排除已有脏改动；非 Git 目录必须显式列出文件路径。

是否需要独立验收由实际快照和 `risk` 共同决定：

| 条件 | 独立验收 |
| --- | --- |
| `risk: "important"`，或路径命中权限、认证、支付、迁移等 critical 检测 | 必须执行。 |
| `risk: "routine"` | 仅供细小、低风险的文案或格式调整；不超过 1 个文件且不超过 20 行时可免，critical 检测仍优先。 |
| 默认 `risk: "auto"` | 包含代码/配置文件，或超过 2 个文件，或超过 80 行时必须执行。 |

行数计算新增与删除行；非 Git 文件按完整内容计数。`critical` 是路径检测结果，
不是第四种 `risk` 参数。业务逻辑、权限、数据和有实际影响的行为应使用
`important`，不能为省略验收而标为 `routine`。

需要独立验收时，主 Agent 将命令返回的 `spawn` 字段**原样**交给原生
`spawn_agent`，显式设置 `fork_turns: "none"`。这创建独立上下文的子 Agent，
不创建用户可见的新任务，也不复制编码对话。子 Agent 首先执行交接中给出的
`review-claim` 命令；Hook 用宿主提供的真实 `agent_id` 绑定本次验收，再从
`SubagentStop.last_assistant_message` 收集其实际结论。主 Agent 自填的
“review passed”不算独立验收证据。

认领后的验收 Agent 使用允许的 `cat`、`rg` 等命令读取预生成 `diff.patch`、
快照和必要依赖，不直接运行 Git 命令；Git 内容过滤器也可能执行仓库配置的程序。

每个任务最多启动两次独立验收：初次验收和修复后的复审。修复并重新验证后，
只有代码或要求发生变化才重新准备；不要因额度或容量不足反复重试。需要验收的
任务只有全部验收项通过、没有遗留发现、用户要求和代码快照仍为当前版本时，
才满足完成条件。无法完成验收时必须如实说明未通过验证。

最后，Codex 通过 Hook 提供的 `submit` 命令，从标准输入提交 JSON 声明。
声明保存在私有本地审计文件；面向用户的回复不包含审计 JSON 或 completion
marker。声明应在最后一次业务修改、必要验证和独立验收之后提交，内容包括：

- 从完整需求提取出的有效验收项；
- 每个验收项的状态和具体证据；
- 空的剩余任务列表；
- 最后一次修改之后执行的验证，或者验证不适用的具体理由；
- 确实需要用户决定时，应说明明确问题和原因；
- 确实存在外部阻塞时，应有已经观察到的失败或拒绝事件。

如果审计失败，Stop Hook 会阻止结束，并显示简短的续跑提示。详细诊断保存在
本地，续跑时通过内部 Hook 上下文提供给 Codex。为了避免错误判断造成无限
循环，最多阻止三次；连续没有可观察进展时也会降级为 fail-open。这与两次
独立验收上限分别计数；降级放行不等于独立验收通过。

升级前已开启、没有 `review` 状态的旧任务沿用原有完成协议，不强行追加独立
验收。新任务使用上述流程。

### 数据和隐私

Python 脚本只在本机执行，不自行请求模型或发送遥测。独立验收子 Agent 会正常
调用 Codex 当前模型，消耗账号额度；交接中的用户要求及其读取的代码和证据会进入
该子 Agent 的模型上下文。模型覆盖参数默认省略。

任务状态、事件记录和审计诊断保存在
`PLUGIN_DATA/completion-guard/v1/sessions/<session-hash>/` 下，内容包括任务
元数据、审计文件路径、事件分类、时间、结果、SHA-256 哈希、诊断错误和验证
计数。常规事件记录对提示词和工具数据保存哈希。**独立验收还会保存原始用户
请求、要求、验证说明、diff、文件副本和子 Agent 报告**，因此不能将全部插件数据
视为只有哈希。验收状态中的请求和正式报告保存在此私有数据目录中；Hook 不解析
Codex 会话 transcript 格式。

提交的 JSON 声明单独保存在系统临时目录下的
`codex-task-completion-guard/<session-hash>/<task-id>.json`，包含模型填写的
验收项、证据描述、摘要以及需要用户回答的问题或阻塞原因，因此可能含有任务
相关文本。相邻的 `.context.json`、`.review-request.json` 与
`.review-<run-id>/` 保存原始需求、验收请求和快照。在 POSIX 系统上，新建私有
目录和可写 JSON 文件使用 `0700`、`0600`；保存完成的快照目录和文件使用
`0500`、`0400`。任务完成或中断时不会自动删除这些数据。

安全模型和漏洞报告方式见 [SECURITY.md](SECURITY.md)。

### 已知边界

Task Completion Guard 是工作流门禁，不是对代码正确性的形式化证明。

- 自然语言识别和命令分类使用保守的启发式规则，仍可能误报或漏报；
- 需求语义拆解仍由模型完成，Hook 能检查完成声明和可观察证据，但无法证明模型
  理解了每一项业务要求；
- 托管工具和部分特殊工具可能不经过本地工具 Hook；
- 独立模型复核仍可能遗漏问题；通过不等于形式化证明，也不能替代未运行的测试；
- 验收 Agent 的读取限制由提示与 Hook 实现，不承诺操作系统级 sandbox；本地用户
  或恶意进程可以修改脚本或状态，本插件不构成对抗恶意行为的安全边界；
- 超过快照大小限制、无法安全读取文件或缺少证据时，不能宣称验收通过；
- 内部异常或超过有限重试次数时会 fail-open，必须把未完成的验收如实标为未验证。

### 更新

发布新版本后，刷新 Marketplace 并重新安装：

```bash
codex plugin marketplace upgrade luckyryan-codex-plugins
codex plugin add task-completion-guard@luckyryan-codex-plugins
```

重新安装后请新建任务。如果 Hook 定义发生了变化，需要再次通过 `/hooks`
信任新的定义哈希。

### 卸载

```bash
codex plugin remove task-completion-guard@luckyryan-codex-plugins
codex plugin marketplace remove luckyryan-codex-plugins
```

如果以后还要安装本仓库里的其他插件，可以只卸载当前插件并保留 Marketplace。

## 开发与测试

```bash
python3 -m unittest discover -s tests -p "test_*.py"
python3 -m unittest discover \
  -s plugins/task-completion-guard/tests \
  -p "test_*.py"
```

插件只依赖 Python 标准库，不需要安装第三方 Python 包。

## 版本与许可证

版本遵循语义化版本规范，变更记录见 [CHANGELOG.md](CHANGELOG.md)。

本仓库使用 [MIT License](LICENSE)。
