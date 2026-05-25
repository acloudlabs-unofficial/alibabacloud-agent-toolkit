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

### Codex

```text
codex plugin marketplace add acloudlabs-unofficial/alibabacloud-agent-toolkit
```

Then open Codex `/plugins` and install `alibabacloud-core` and/or `alibabacloud-spec-ops`.

### Claude Code

```text
/plugin marketplace add acloudlabs-unofficial/alibabacloud-agent-toolkit
/plugin install alibabacloud-core@alibabacloud-agent-toolkit
/plugin install alibabacloud-spec-ops@alibabacloud-agent-toolkit
/reload-plugins
```

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

## MCP Safety

The plugin defines an MCP server named `alibabacloud-core` with this policy:

```text
openapiexplorer:*=allow,*=deny
```

The SDK skill is restricted to `mcp__alibabacloud-core__AlibabaCloud___CallCLI`, so OpenAPI Explorer metadata is queried through the configured MCP server instead of unrestricted shell execution.

## Telemetry & Tracing

### Remote Telemetry

This plugin collects anonymous usage telemetry to help improve product quality. Collection is **strictly limited to Alibaba Cloud tool calls** — no user prompts, code content, or file paths are transmitted.

**What is collected:**

- Tool name (e.g. `AlibabaCloud___CallCLI`)
- Call status (success / failure) and error code
- Request ID (Alibaba Cloud API request tracking)
- Duration and timestamp
- **The full input parameters for every Alibaba Cloud tool call**, captured verbatim for audit (all inputs are considered non-sensitive Alibaba Cloud operational context):
  - **Bash `aliyun ...`** — the full shell command (cap 2000 chars)
  - **MCP `AlibabaCloud___CallCLI`** — the full shell command (cap 2000 chars)
  - **All other MCP `AlibabaCloud___*` tools** (`ListProducts`, `ListApis`, `ListProductRegions`, `SearchApis`, `SearchDocument`, `GetApiDefinition`, `GenerateCLICommand`, `ReadDocument`, …) — the full `tool_input` as compact JSON (cap 4000 chars)

**Privacy protection:**

- All AccessKey, STS tokens, JWT, PEM private keys, Bearer tokens, passwords, and PII are stripped before transmission — including inline credential flags inside the captured command (`--access-key-id`, `--access-key-secret`, `--sts-token`, `--password`, etc., in both `--flag value` and `--flag=value` forms), bare `LTAI*` / `STS.*` / JWT tokens, and long base64 blobs inside MCP tool inputs
- No prompt text is sent; only the alibabacloud tool inputs themselves
- No tool response content is sent
- Data is transmitted to Alibaba Cloud observability endpoints only

**Disable remote telemetry:**

```bash
export ALIBABACLOUD_TELEMETRY=false
```

### Local Audit Trace

The plugin provides a transparent, auditable local trace in JSONL format. This gives you full visibility into every Alibaba Cloud tool interaction — including prompts, inputs, and complete responses — stored locally on your machine for self-audit and visualization.

**What is recorded locally:**

- User prompts (for turns that invoke Alibaba Cloud tools)
- Full tool inputs and responses (truncated at 64 KB)
- Skill invocations, timing, span hierarchy
- Turn lifecycle events

**Trace files are per-session:**

```text
~/.cache/alibabacloud-agent-toolkit/telemetry/<client>/traces/<session-id>.jsonl
```

Local traces are never uploaded. Light sanitization (AK/SK, tokens, phone, email) is applied even locally. Trace files older than **90 days** are automatically cleaned up on each session stop to prevent unbounded disk growth.

### Local Telemetry Visualization

`telemetry-view` starts a local web server for browsing and analyzing trace data. Supports multi-client session browsing (Claude Code, VS Code, Copilot CLI, Codex, Qoderwork), span hierarchy tree, Gantt timeline, Graph flow chart, and live updates.

![Telemetry View](tracing-view.png)

**Start:**

```bash
uvx alibabacloud.mcp-proxy@latest telemetry-view
```

Opens `http://localhost:18321` in your browser automatically.

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `18321` | Local server port |
| `--no-open` | - | Don't auto-open browser |

**Data sources** (scanned automatically):

1. `$ALIBABACLOUD_TELEMETRY_STATE_DIR` (if set)
2. `~/.cache/alibabacloud-agent-toolkit/telemetry/`
3. `/tmp/alibabacloud-agent-toolkit-telemetry-<uid>/`

**Roadmap:** Future releases will support automatic upload to user's [SLS (Simple Log Service)](https://www.alibabacloud.com/product/log-service) for long-term archival, centralized audit, and alarm configuration.

**Disable local trace:**

```bash
export ALIBABACLOUD_TRACE=false
```

See [`plugins/alibabacloud-core/hooks/README.md`](plugins/alibabacloud-core/hooks/README.md) for full field reference and file structure.

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
