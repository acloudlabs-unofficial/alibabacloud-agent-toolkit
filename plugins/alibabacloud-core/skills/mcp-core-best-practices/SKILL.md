---
name: mcp-core-best-practices
description: >
  Shared reference for using Alibaba Cloud OpenAPI MCP Server Core effectively.
  Covers tool usage patterns, API exploration workflow, CLI command generation,
  scripted execution, async task polling, cross-account access, and safety policy
  configuration. Referenced by other alibabacloud-core skills as the canonical
  guide for MCP Core interactions.
allowed-tools: "mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___CallCLI,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___SearchApis,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___GetApiDefinition,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___ListApis,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___ListProductRegions,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___GenerateCLICommand,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___ListProducts,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___SearchDocument,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___ReadDocument,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___RunScript,mcp__plugin_alibabacloud-core_alibabacloud-core__AlibabaCloud___GetTask"
---

# Alibaba Cloud MCP Core Best Practices

This skill documents the canonical patterns for using the `alibabacloud-core` MCP
server — the generalized OpenAPI MCP Server Core that covers all Alibaba Cloud
APIs without requiring pre-selection of specific operations.

## Available Tools

| Tool | Purpose |
|------|---------|
| `AlibabaCloud___SearchApis` | Natural language search for APIs matching a requirement |
| `AlibabaCloud___CallCLI` | Execute a single CLI command remotely |
| `AlibabaCloud___GetApiDefinition` | Get full API definition (params, response, errors) by product/version/name |
| `AlibabaCloud___ListApis` | List all APIs for a product, optionally filtered |
| `AlibabaCloud___ListProductRegions` | List regions where a product is available |
| `AlibabaCloud___GenerateCLICommand` | Generate a CLI command from API definition + parameters |
| `AlibabaCloud___ListProducts` | List all Alibaba Cloud products |
| `AlibabaCloud___SearchDocument` | Search Alibaba Cloud documentation by keyword |
| `AlibabaCloud___ReadDocument` | Read a specific documentation page by URL |
| `AlibabaCloud___RunScript` | Start a restricted Python task with structured Alibaba Cloud OpenAPI access |
| `AlibabaCloud___GetTask` | Long-poll a `RunScript` task until approval, execution, or failure reaches a terminal state |

## Standard Workflow

### 1. API Discovery

When the target API is unknown, use `AlibabaCloud___SearchApis` with a natural
language description of the requirement. Each query should be granular enough to
map to a single API call.

For known products, use `AlibabaCloud___ListApis` with a filter keyword to browse
available operations.

### 2. API Inspection

Once the target API is identified, use `AlibabaCloud___GetApiDefinition` to
retrieve the full definition including:

- Required and optional parameters
- Request/response schemas
- Authentication requirements
- Error codes

### 3. Command Generation

Use `AlibabaCloud___GenerateCLICommand` to produce a correct CLI command from the
API definition and user-provided parameters. This avoids manual CLI syntax errors.

### 4. Execution Choice

Use `AlibabaCloud___CallCLI` for one known API call or generated command. Use
`AlibabaCloud___RunScript` for multi-call work: pagination, List-to-Describe
enrichment, cross-resource comparison, multi-region/multi-product collection,
permission checks, or response-driven follow-up calls.

Do not split one user task across multiple `RunScript` submissions just to
inspect intermediate data. Put discovery, pagination, filtering, and follow-up
Describe/Get calls in one self-contained script and return via `result`.

### 5. Single-Call Execution

Use `AlibabaCloud___CallCLI` to execute the generated command. Key constraints:

- Commands must start with `aliyun`
- No shell pipes, redirections, or operators
- No shell variables or command substitution
- No local file path references (MCP server is remote)

### 6. Scripted Multi-Call Execution

`AlibabaCloud___RunScript` starts an async sandboxed Python task and returns a
`processID`. The script's OpenAPI access goes through injected `call_cli()` only:

```python
result = await call_cli(product='Ecs', action='DescribeInstances',
                        params={'RegionId': 'cn-hangzhou'},
                        version='2014-05-26', region='cn-hangzhou')
```

RunScript essentials:

- `call_cli()` is structured OpenAPI, not CLI text. Never pass `aliyun ...`.
- Use exact OpenAPI parameter names, usually PascalCase; arrays/objects stay as
  Python lists/dicts unless the API parameter itself is JSON text.
- `version`, `region`, and `endpoint` are optional; still pass API region fields
  such as `RegionId` when the API defines them.
- Assign final output to `result`. Do not pass credentials, profiles, shell
  flags, or host file paths. Use `/tmp` only for small sandbox scratch files.
- Discover IDs inside the script, paginate every list call, and never sample or
  truncate complete inventory/compliance answers.
- List APIs are not enough for attribute checks. Follow List with Describe/Get
  and feature-specific APIs for every resource.
- Verify response shape before saying "0 found"; list responses often wrap arrays
  twice, e.g. `{"Instances":{"Instance":[...]}}`.
- Use `asyncio.gather(..., return_exceptions=True)` for parallel calls. Treat
  expected NotFound as "not configured" for optional features.
- Do exact aggregation in Python; return raw fields for judgment calls such as
  "misconfigured" or "risky".

### 7. RunScript Task Polling

After `AlibabaCloud___RunScript`, inspect `nextAction`:

| `nextAction` | Meaning | Agent action |
|--------------|---------|--------------|
| `None` | Task succeeded | Use `result` and `callTrace` |
| `CallGetTask` | Initial call is not terminal | Call `AlibabaCloud___GetTask` with the same `processID` |
| `CallGetTaskAgain` | Poll timed out and task is still not terminal | Call `AlibabaCloud___GetTask` again with the same `processID` |
| `Stop` | Validation failed, approval rejected/expired, task expired, or process missing | Stop; do not retry automatically |
| `InspectError` | Execution failed | Inspect `error` and decide whether a corrected new script is needed |

`AlibabaCloud___GetTask` parameters:

- `processID`: required, from `RunScript`.
- `waitTimeoutSeconds`: optional, capped at 30 seconds by the server. Use `0`
  for an immediate status check.
- `pollIntervalSeconds`: optional polling interval, between 1 and 10 seconds.

Human approval handling:

- `ApprovalPending` with `approvalReqId` means external approval is required.
  Ask the user to complete approval or rejection out of band, then call
  `AlibabaCloud___GetTask` again with the same `processID`.
- Do not call `AlibabaCloud___RunScript` again while waiting for approval or
  execution; that creates a new process instead of continuing the current one.
- `ApprovalRejected` and `ApprovalExpired` are terminal. Stop and report the
  approval result.

### 8. Cross-Account Execution

**IMPORTANT**: For any operation involving member accounts, cross-account queries,
or Resource Directory account resolution, you MUST first load the
`multi-account-query` skill (invoke it via the Skill tool) before proceeding.
That skill contains the complete workflow for alias-to-UID resolution, pagination
handling, and proper use of `x_assume_account_id`. Do not attempt cross-account
operations without reading it first.

For Resource Directory member accounts, pass additional parameters to
`AlibabaCloud___CallCLI`:

| Parameter | Usage |
|-----------|-------|
| `x_assume_account_id` | Target member account UID |
| `x_assume_role_name` | Custom role name (default: `ResourceDirectoryAccountAccessRole`) |
| `x_assume_role_arn` | Full role ARN (highest priority) |

Priority: `x_assume_role_arn` > `x_assume_account_id` + `x_assume_role_name` >
default configuration.

## CLI Command Constraints

When using `AlibabaCloud___CallCLI`, the following are NOT supported:

- Bash/zsh pipes (`|`) or shell operators
- `grep`, `awk`, `sed`, or other shell tools
- Shell redirection (`>`, `>>`, `<`)
- Command substitution (`$()`)
- Shell variables or environment variables
- Local file paths (`file://`, `fileb://`)

For commands that need local file access (e.g., `ossutil cp`), use the Bash tool
directly instead of MCP.

## Region Handling

- Use `AlibabaCloud___ListProductRegions` to check product availability in a
  specific region before making calls.
- Always include `--region` when operating across regions or when the default
  region may not match the target.

## Documentation Access

- `AlibabaCloud___SearchDocument`: Find relevant docs by keyword.
- `AlibabaCloud___ReadDocument`: Read full content of a known documentation URL.

Use these to verify behavior, understand quotas, or find configuration guides
that are not captured in API definitions alone.

## Error Handling Patterns

- **InvalidParameter**: Check parameter names and values against
  `GetApiDefinition` output.
- **AccessDenied / Forbidden**: Verify RAM permissions for the current identity.
- **Throttling**: Retry with backoff; do not loop aggressively.
- **RegionNotSupported**: Use `ListProductRegions` to find valid regions.
- **RunScript ValidationFailed**: Fix unsafe or invalid Python authoring patterns
  before submitting a new `RunScript`.
- **RunScript Failed / InspectError**: Inspect `error` and `callTrace`; if the
  script needs correction, submit one new corrected `RunScript`.

## Integration Guidance

When building stable workflows:

1. Use `SearchApis` to identify the correct API during development.
2. Use `GenerateCLICommand` to produce validated commands.
3. Use `RunScript` for workflows that require complete pagination,
   List-to-Describe enrichment, or multi-call analysis.
4. Capture the stable command or script patterns into a dedicated Skill.
5. Configure a safety policy to restrict the MCP connection to only the commands
   the Skill needs (for production use).

This progression — explore, validate, codify, restrict — ensures both flexibility
during development and safety in production.
