# LuckyRyan Codex 插件

[English](README.md)

这是由 [LuckyRyan-web](https://github.com/LuckyRyan-web) 维护的 Codex 插件
Marketplace 仓库。

## Task Completion Guard

Task Completion Guard 用于减少 Codex 在实现任务中只完成一个中间步骤、给出
进度汇报后就提前结束的问题。

它由三部分组成：

- Skill：要求 Codex 根据用户完整的自然语言需求自动提取验收项；
- 生命周期 Hook：识别执行型任务，观察本地修改与验证事件；
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

在新任务或新会话中输入 `/hooks`，阅读 Task Completion Guard 提供的四个
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

插件监听四个 Codex 生命周期事件：

| 事件 | 作用 |
| --- | --- |
| `UserPromptSubmit` | 判断是否为执行任务，并向模型注入完成协议。 |
| `PostToolUse` | 记录文件修改、Shell 修改、验证命令及其结果的哈希证据。 |
| `Stop` | 检查结构化完成声明；不符合条件时阻止提前结束。 |
| `Interrupt` | 用户主动中断时暂停当前门禁。 |

对于已经开启门禁的任务，Codex 必须声明：

- 从完整需求提取出的有效验收项；
- 每个验收项的状态和具体证据；
- 空的剩余任务列表；
- 最后一次修改之后执行的验证，或者验证不适用的具体理由；
- 确实需要用户决定时，应说明明确问题和原因；
- 确实存在外部阻塞时，应有已经观察到的失败或拒绝事件。

如果审计失败，Stop Hook 会返回阻塞决定和缺失项，让 Codex 获得继续执行的
机会。为了避免错误判断造成无限循环，最多阻止三次；连续没有可观察进展时也会
降级为 fail-open。

### 数据和隐私

Hook 只在本机执行，不向远程服务发送遥测或任务数据。

状态保存在 Codex 提供的 `PLUGIN_DATA` 目录中。插件记录事件分类、时间、
结果和 SHA-256 哈希；设计上不持久化原始提示词、工具输入或工具输出，也不解析
不稳定的 Codex 会话 transcript 格式。

安全模型和漏洞报告方式见 [SECURITY.md](SECURITY.md)。

### 已知边界

Task Completion Guard 是工作流门禁，不是对代码正确性的形式化证明。

- 自然语言识别和命令分类使用保守的启发式规则，仍可能误报或漏报；
- 需求语义拆解仍由模型完成，Hook 能检查完成声明和可观察证据，但无法证明模型
  理解了每一项业务要求；
- 托管工具和部分特殊工具可能不经过本地工具 Hook；
- Hook 不能独立证明模型填写的证据描述在语义上完全真实；
- 内部异常或超过有限重试次数时会 fail-open。

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
