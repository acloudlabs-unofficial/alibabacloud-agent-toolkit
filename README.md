# Alibaba Cloud Agent Toolkit

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Build](https://github.com/acloudlabs-unofficial/alibabacloud-agent-toolkit/actions/workflows/build.yml/badge.svg)](https://github.com/acloudlabs-unofficial/alibabacloud-agent-toolkit/actions/workflows/build.yml)
[![Status](https://img.shields.io/badge/status-initializing-yellow.svg)](#current-status)

Help AI coding agents build, deploy, and operate applications on Alibaba Cloud.

This repository provides Alibaba Cloud agent plugins, skills, MCP configuration, and validation tooling.

## Current Status

The repository currently provides:

- A top-level project scaffold for marketplace manifests, validation, CI, rules, and shared skills.
- Two active plugins: [`alibabacloud-core`](plugins/alibabacloud-core/) and [`alibabacloud-spec-ops`](plugins/alibabacloud-spec-ops/).
- Placeholder plugin directories for future agent and data analytics plugins.

`alibabacloud-core` includes an SDK usage skill that generates Alibaba Cloud OpenAPI interaction code through a constrained MCP server. `alibabacloud-spec-ops` delivers a planning-to-execution workflow for Alibaba Cloud infrastructure operations driven by Terraform and IaC Service.

## Repository Layout

```text
.
├── plugins/
│   ├── alibabacloud-core/
│   ├── alibabacloud-spec-ops/
│   ├── alibabacloud-agent/
│   └── alibabacloud-data-analytics/
├── rules/
├── skills/
└── tools/
```

### Hook Implementation Convention

`alibabacloud-core` is the **canonical source of truth** for the hook
implementation. Hooks live at `plugins/alibabacloud-core/hooks/` as a real
directory (no symlinks). When a new plugin (e.g. `alibabacloud-agent`)
needs telemetry/tracing, copy the entire `plugins/alibabacloud-core/hooks/`
verbatim into the new plugin. **Do not maintain parallel implementations.**
CI (`tools/dev-hooks/verify-hooks.sh`) fails on any divergence or on the
re-introduction of a `hooks/` symlink.

## Plugins

| Plugin | Status | Description |
|--------|--------|-------------|
| [alibabacloud-core](plugins/alibabacloud-core/) | Active | Alibaba Cloud OpenAPI SDK code generation using the local `alibabacloud-core` MCP server. |
| [alibabacloud-spec-ops](plugins/alibabacloud-spec-ops/) | Active | Spec-driven Alibaba Cloud infrastructure ops workflow: planning → Terraform codegen → validation → execution via IaC Service. |
| `alibabacloud-agent` | Placeholder | Reserved for future agent-focused capabilities. |
| `alibabacloud-data-analytics` | Placeholder | Reserved for future analytics and data workflow capabilities. |

## Prerequisites

**Python 3.10+** — hook handlers (pre-installed on most systems).

**[uv](https://docs.astral.sh/uv/)** (provides `uvx`) — telemetry tracing view & mcp server:

```bash
# macOS
brew install uv
```

```
# Linux / WSL
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
```

**[Alibaba Cloud CLI](https://help.aliyun.com/document_detail/139508.html)** (`aliyun`) — cloud operations:

```bash
# Linux amd64
curl -fsSL https://aliyuncli.alicdn.com/aliyun-cli-linux-latest-amd64.tgz | tar xz

# macOS
brew install aliyun-cli
```

## Install Plugins

### One-command install (recommended)

```bash
npx openplugin acloudlabs-unofficial/alibabacloud-agent-toolkit
```

Automatically detects installed clients (Claude Code, Codex, QoderWork), lets you pick which plugins to install, and configures everything.

### Manual install

#### Codex

```text
codex plugin marketplace add acloudlabs-unofficial/alibabacloud-agent-toolkit
```

Then open Codex `/plugins` and install `alibabacloud-core` and/or `alibabacloud-spec-ops`.

#### Claude Code

```text
/plugin marketplace add acloudlabs-unofficial/alibabacloud-agent-toolkit
/plugin install alibabacloud-core@alibabacloud-agent-toolkit
/plugin install alibabacloud-spec-ops@alibabacloud-agent-toolkit
/reload-plugins
```

#### QoderWork

Use the one-command install above (`npx openplugin`), which handles QoderWork hook registration automatically.

The installer patches `~/.qoderwork/settings.json` with the same 4-event
hook set Codex uses (`PreToolUse`, `PostToolUse`, `UserPromptSubmit`,
`Stop`) and is idempotent — re-runs only refresh this plugin's entries.

## Use Spec-Ops: Spec-Driven Workflow

Want an expert-guided, spec-driven flow that takes "I need a web app on aliyun" all the way to live infrastructure? One command:

```text
/alibabacloud-spec-ops:alibabacloud-planning  I need a web app on aliyun
```

4 stages, auto-chained, **one user gate** (right before deploy):

1. **planning** — expert dialog across **Security / Cost / Efficiency / Stability**; turns vague needs into a precise `design.md` + architecture diagram
2. **code** — Terraform HCL generated against live `alicloud_*` schemas (IaCService-verified)
3. **validate** — spec + code-quality reviewers run in parallel → "deploy?"
4. **execute** — `terraform plan` + `apply` run remotely via IaC Service; remote state persisted

**Day-2 ready.** 再说一句"升配 RDS / 加 Redis / 缩容"，原 `design.md` 自动加载，在已有 `state_id` 上做增量 plan/apply，不重建已有资源。所有产物保存在 `.aliyun-ai-ops-spec/{name}/`，跨会话可审、可迭代。

## Security, Data Collection, and Privacy / 安全、数据采集与隐私

### MCP Safety / MCP 安全

The plugin defines an MCP server named `alibabacloud-core` with this policy:

```text
openapiexplorer:*=allow,*=deny
```

The SDK skill is restricted to `mcp__alibabacloud-core__AlibabaCloud___CallCLI`, so OpenAPI Explorer metadata is queried through the configured MCP server instead of unrestricted shell execution.

插件定义了名为 `alibabacloud-core` 的 MCP Server，并使用以下访问策略：

```text
openapiexplorer:*=allow,*=deny
```

SDK skill 仅允许使用 `mcp__alibabacloud-core__AlibabaCloud___CallCLI`，因此 OpenAPI Explorer 元数据会通过已配置的 MCP Server 查询，而不是通过不受限制的 shell 执行。

### Security / 安全

Alibaba Cloud credentials are handled by the user's configured Alibaba Cloud tools, SDKs, MCP servers, or CLI profiles. This toolkit does not store AccessKey secrets, STS tokens, bearer tokens, private keys, or passwords.

MCP is an emerging integration standard. Before using this toolkit in production or regulated environments, review the full integration path, including the MCP client, agent runtime, model provider, local hooks, network access, and Alibaba Cloud account permissions.

阿里云凭证由用户已配置的阿里云工具、SDK、MCP Server 或 CLI profile 处理。本工具包不存储 AccessKey Secret、STS Token、Bearer Token、私钥或密码。

MCP 是较新的集成标准。在生产环境或受监管环境中使用本工具包前，建议审查完整链路，包括 MCP Client、Agent 运行时、模型提供方、本地 hooks、网络访问以及阿里云账号权限。

### Permissions and Risk / 权限与风险

MCP clients and AI agents may invoke Alibaba Cloud operations using the permissions available to the configured identity. Misconfigured, overly autonomous, or overly privileged clients may perform costly, sensitive, or destructive operations.

Use least-privilege RAM policies, separate test and production accounts, review generated commands and Terraform plans, and require explicit human approval before applying infrastructure changes or destructive operations.

MCP Client 和 AI Agent 可能会使用当前配置身份拥有的权限调用阿里云操作。配置不当、自治程度过高或权限过大的客户端，可能执行产生费用、涉及敏感资源或具有破坏性的操作。

建议使用最小权限 RAM 策略，隔离测试与生产账号，审查生成的命令和 Terraform plan，并在执行基础设施变更或破坏性操作前要求明确的人工确认。

### Data Collection / 数据采集

This toolkit collects limited, de-identified operational telemetry to improve Alibaba Cloud agent skills, MCP integrations, and troubleshooting quality. Remote telemetry is limited to Alibaba Cloud plugin activity. User prompts, source code, local file contents, and full tool responses are not uploaded.

Telemetry may include event type, timestamps, client name, plugin or skill name, MCP tool name, execution status, anonymous session identifiers, and Alibaba Cloud OpenAPI RequestId when present.

Additional operational context is collected only after user opt-in. This may include sanitized `aliyun` commands, sanitized MCP tool inputs, structured error classes, and token counts. Credentials, secrets, private keys, bearer tokens, STS tokens, passwords, and obvious personal identifiers are stripped before transmission.

Some features may enable you, Alibaba Cloud, or integrated services to collect operational data from users of applications, agents, workflows, or cloud environments that you build, operate, or expose through this toolkit. If you use such features, you are responsible for complying with applicable laws, providing appropriate notices to your users, and obtaining any required consents.

本工具包会采集有限的、去标识化的操作遥测，用于改进阿里云 Agent Skill、MCP 集成和问题排查质量。远程遥测仅限阿里云插件活动，不上传用户 prompt、源码、本地文件内容或完整工具响应。

遥测可能包括事件类型、时间戳、客户端名称、插件或 skill 名称、MCP 工具名称、执行状态、匿名会话标识，以及存在时的阿里云 OpenAPI RequestId。

额外操作上下文仅在用户 opt-in 后采集，可能包括清洗后的 `aliyun` 命令、清洗后的 MCP 工具输入、结构化错误类型和 token 计数。凭证、密钥、私钥、Bearer Token、STS Token、密码和明显个人标识会在传输前被移除。

某些功能可能使你、阿里云或集成服务采集你通过本工具包构建、运行或开放的应用、Agent、工作流或云环境用户的操作数据。若使用此类功能，你有责任遵守适用法律，向你的用户提供适当告知，并在需要时取得必要同意。

#### What is collected by default / 默认采集内容

All fields below describe Alibaba Cloud plugin behavior only.

以下字段仅描述阿里云插件行为。

| Field | Description |
|---|---|
| startTimestamp / endTimestamp | Alibaba Cloud tool call start and end time (ISO 8601 UTC) |
| clientName | Agent client type (`claude-code`, `codex`, `copilot-cli`, `qoderwork`, `vscode`) |
| eventType | Alibaba Cloud event category (`skill_invocation`, `mcp_tool_use`, `cli_command_use`, `subagent_dispatch`, `reference_file_read`, `user_prompt_turn_start`, `llm_call`) |
| sessionId / mcpSessionId | Session identifiers used for correlation; not linked to an Alibaba Cloud account by this toolkit |
| skillName / pluginName / skillTag | Alibaba Cloud skill and plugin identity |
| mcpTool / toolName | Alibaba Cloud MCP tool name and raw tool entry point |
| eventTag | Fixed Alibaba Cloud event marker |
| status | Alibaba Cloud tool call outcome (`success` / `failure`) |
| toolRequestId | Alibaba Cloud OpenAPI RequestId for server-side log correlation |

#### Additional opt-in fields / 额外 opt-in 字段

These fields contain sanitized Alibaba Cloud operational context and are collected only after explicit user authorization.

以下字段包含清洗后的阿里云操作上下文，仅在用户明确授权后采集。

| Field | Description |
|---|---|
| cliCommand | Sanitized `aliyun` CLI command or Alibaba Cloud MCP tool input JSON; credentials stripped; capped at 2000-4000 chars |
| errorMessage | Alibaba Cloud API error class/code only, such as `NoPermission` or `Throttling`; not free-text |
| inputUncachedTokens | LLM uncached input tokens for turns involving Alibaba Cloud tools |
| inputCachedTokens | LLM cached input tokens for turns involving Alibaba Cloud tools |
| inputCreationTokens | LLM cache creation tokens for turns involving Alibaba Cloud tools |
| outputTokens | LLM output tokens for turns involving Alibaba Cloud tools |
| reasoningTokens | LLM reasoning tokens for turns involving Alibaba Cloud tools |

### Telemetry Configuration / 遥测配置

Remote telemetry is enabled by default. To disable remote telemetry:

远程遥测默认开启。禁用远程遥测：

```bash
export ALIBABACLOUD_TELEMETRY=false
```

### Local Audit Trace / 本地审计追踪

The plugin provides a transparent local trace in JSONL format. Local traces are stored on your machine and are not uploaded by default. They are intended for self-audit, troubleshooting, and local visualization.

插件会以 JSONL 格式记录透明的本地 trace。本地 trace 存储在你的机器上，默认不会上传，用于自审计、问题排查和本地可视化。

Local traces may include:

本地 trace 可能包括：

- User prompts for turns that invoke Alibaba Cloud tools
- Full tool inputs and responses, truncated at 64 KB
- Skill invocations, timing, and span hierarchy
- Turn lifecycle events
- 调用阿里云工具的回合中的用户 prompt
- 完整工具输入和响应，最大截断到 64 KB
- Skill 调用、耗时和 span 层级
- 回合生命周期事件

Trace files are stored per session:

trace 文件按 session 存储：

```text
~/.cache/alibabacloud-agent-toolkit/telemetry/<client>/traces/<session-id>.jsonl
```

Light sanitization is applied even locally. Trace files older than 90 days are automatically cleaned up on each session stop to prevent unbounded disk growth.

即使是本地 trace，也会做轻量清洗。超过 90 天的 trace 文件会在每次 session stop 时自动清理，避免磁盘无限增长。

To disable local trace recording:

禁用本地 trace：

```bash
export ALIBABACLOUD_TRACE=false
```

### Local Telemetry Visualization / 本地遥测可视化

`telemetry-view` starts a local web server for browsing and analyzing trace data. It supports multi-client session browsing, span hierarchy tree, Gantt timeline, graph flow chart, and live updates.

`telemetry-view` 会启动本地 Web Server，用于浏览和分析 trace 数据。它支持多客户端 session 浏览、span 层级树、Gantt 时间线、图形链路视图和实时更新。

![Telemetry View](tracing-view.png)

Start:

启动：

```bash
uvx alibabacloud.mcp-proxy@latest telemetry-view
```

It opens `http://localhost:18321` in your browser automatically.

它会自动在浏览器中打开 `http://localhost:18321`。

Options:

参数：

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `18321` | Local server port |
| `--no-open` | - | Do not auto-open browser |

Data sources scanned automatically:

自动扫描的数据来源：

1. `$ALIBABACLOUD_TELEMETRY_STATE_DIR`, if set
2. `~/.cache/alibabacloud-agent-toolkit/telemetry/`
3. `/tmp/alibabacloud-agent-toolkit-telemetry-<uid>/`

### Compliance Responsibility / 合规责任

This toolkit may interact with MCP clients, model providers, local development tools, Alibaba Cloud services, and third-party components outside Alibaba Cloud's compliance boundary. You are responsible for ensuring that your use of this toolkit complies with applicable organizational policies, laws, regulations, and contractual obligations.

本工具包可能与 MCP Client、模型提供方、本地开发工具、阿里云服务以及第三方组件交互，其中部分组件可能位于阿里云合规边界之外。你有责任确保本工具包的使用方式符合适用的组织策略、法律法规和合同义务。

### Third Party Components / 第三方组件

This toolkit may use or depend on third-party components, package managers, MCP clients, model providers, and local development tools. You are responsible for reviewing and complying with the licenses, security posture, and data handling practices of those components.

本工具包可能使用或依赖第三方组件、包管理器、MCP Client、模型提供方和本地开发工具。你有责任审查并遵守这些组件的许可证、安全状态和数据处理实践。

See [`plugins/alibabacloud-core/hooks/README.md`](plugins/alibabacloud-core/hooks/README.md) for the full telemetry field reference, hook behavior, local file structure, and troubleshooting details.

完整遥测字段、hook 行为、本地文件结构和问题排查细节见 [`plugins/alibabacloud-core/hooks/README.md`](plugins/alibabacloud-core/hooks/README.md)。

## Skills

The top-level [`skills/`](skills/) directory is initialized for future shared Alibaba Cloud skills. Category directories are present as placeholders only.

## Rules

Recommended agent guidance lives in [`rules/`](rules/). The initial rules file is Alibaba Cloud oriented and intentionally generic until the first concrete workflows are added.

## Validation

This repository keeps the validation and CI skeleton from the reference toolkit structure.

```bash
mise run lint
mise run validate
```

## License

This project is licensed under the Apache-2.0 License. See [LICENSE](LICENSE) for details.
