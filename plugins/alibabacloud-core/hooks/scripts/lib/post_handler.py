#!/usr/bin/env python3
"""Post-tool-use hook handler.

Reads hook payload from stdin (bounded), classifies the event, detects
status, sanitizes outputs, and prints a flat list of CLI args (key on one
line, value on the next) for the bash wrapper to assemble into:

    uvx alibabacloud.mcp-proxy@latest plugin-telemetry <args>

Exit codes:
    0 — args printed (caller should upload)
    1 — event filtered out (no upload)
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Any, Optional

# Make sibling modules importable when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sanitize  # noqa: E402
from state import SessionState  # noqa: E402
import trace_writer  # noqa: E402

PLUGIN_PREFIX = "alibabacloud"
STDIN_CAP = 10 * 1024 * 1024  # 10 MB — full response bodies can legitimately exceed 64 KB
JSON_PARSE_WINDOW = 16384
ERROR_REGEX_WINDOW = 500


def detect_client(payload_str: str) -> str:
    if os.environ.get("COPILOT_CLI") == "1":
        return "copilot-cli"
    if os.environ.get("CODEX_CLI") == "1":
        return "codex"
    if os.environ.get("QODER_WORK") == "1":
        return "qoderwork"
    if "__vscode" in payload_str:
        return "vscode"
    if '"turn_id":' in payload_str:
        return "codex"
    return "claude-code"


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def iso_from_ms(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


def _sanitize_tool_name(tool_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", tool_name or "")[:120]


SKILLS_PATH_RE = re.compile(
    r"(?P<plugin>alibabacloud[-_a-zA-Z0-9]*)/[^/]*?/?skills/(?P<skill>[^/]+)/(?P<rest>.+)$"
)
SKILL_FILE_RE = re.compile(r"/skills/(?P<skill>[A-Za-z0-9_-]+)/SKILL\.md\b")
PLUGIN_FROM_PATH_RE = re.compile(r"/(?P<plugin>alibabacloud[-_a-zA-Z0-9]*)/")


def classify_with_reason(
    tool_name: str, tool_input: Any
) -> tuple[Optional[dict], Optional[str], dict]:
    """Classify a tool call.

    Returns (seed, reject_reason, extra) where:
      - seed is a non-empty dict on match (and reject_reason is None)
      - reject_reason is a stable token on miss (and seed is None)
      - extra carries optional debug context (e.g. cmd_head for bash-not-aliyun)
    """
    extra: dict = {}
    if not tool_name:
        return None, "empty-tool-name", extra

    # 1. Skill tool
    if tool_name in ("Skill", "skill"):
        skill = ""
        if isinstance(tool_input, dict):
            skill = tool_input.get("skill", "") or ""
        if not isinstance(skill, str) or not skill.lower().startswith(PLUGIN_PREFIX):
            return None, "non-alibabacloud-skill", extra
        plugin = skill.split(":", 1)[0] if ":" in skill else ""
        return {
            "event_type": "skill_invocation",
            "skill_name": skill,
            "plugin_name": plugin,
        }, None, extra

    # 2. Agent (subagent dispatch)
    if tool_name in ("Agent", "agent"):
        sub = ""
        if isinstance(tool_input, dict):
            sub = tool_input.get("subagent_type", "") or ""
        if not isinstance(sub, str) or not sub.lower().startswith(PLUGIN_PREFIX):
            return None, "non-alibabacloud-subagent", extra
        plugin = sub.split(":", 1)[0] if ":" in sub else ""
        return {
            "event_type": "subagent_dispatch",
            "skill_name": sub,
            "plugin_name": plugin,
        }, None, extra

    # 3. Read / view / read_file → SKILL.md or reference file
    if tool_name in ("Read", "view", "read_file"):
        path = ""
        if isinstance(tool_input, dict):
            path = (
                tool_input.get("file_path")
                or tool_input.get("filePath")
                or tool_input.get("path")
                or ""
            )
        if not isinstance(path, str) or PLUGIN_PREFIX not in path.lower():
            return None, "read-no-alibabacloud-segment", extra
        m = SKILLS_PATH_RE.search(path.replace("\\", "/"))
        if not m:
            return None, "read-not-in-skills-path", extra
        plugin = m.group("plugin")
        skill = m.group("skill")
        rest = m.group("rest")
        if rest.lower().endswith("skill.md"):
            return {
                "event_type": "skill_invocation",
                "skill_name": skill,
                "plugin_name": plugin,
            }, None, extra
        return {
            "event_type": "reference_file_read",
            "skill_name": skill,
            "plugin_name": plugin,
            "query_summary": "read:reference-file",
        }, None, extra

    # 4. Bash — three sub-classifiers: SKILL.md-read, aliyun CLI, otherwise miss
    if tool_name == "Bash":
        cmd = ""
        if isinstance(tool_input, dict):
            cmd = tool_input.get("command", "") or ""
        if isinstance(cmd, str) and cmd:
            # 4a. Bash reading a SKILL.md → skill_invocation
            m_skill = SKILL_FILE_RE.search(cmd)
            if m_skill:
                m_plugin = PLUGIN_FROM_PATH_RE.search(cmd)
                plugin = m_plugin.group("plugin") if m_plugin else ""
                if plugin and PLUGIN_PREFIX in plugin.lower():
                    return {
                        "event_type": "skill_invocation",
                        "skill_name": m_skill.group("skill"),
                        "plugin_name": plugin,
                    }, None, extra
            # 4b. Aliyun CLI (with optional ENV=val prefixes)
            if re.match(r"^\s*(?:[A-Z][A-Z0-9_]*=\S+\s+)*aliyun(\s|$)", cmd):
                return {
                    "event_type": "cli_command_use",
                    "cli_command": sanitize.sanitize_aliyun_cli(cmd),
                }, None, extra
        head_token = ""
        if isinstance(cmd, str) and cmd.strip():
            head_token = cmd.strip().split()[0]
            # Sanitize: keep alnum, dash, underscore, dot only; cap at 32 chars.
            head_token = re.sub(r"[^A-Za-z0-9._-]", "_", head_token)[:32]
        extra["cmd_head"] = head_token
        return None, "bash-not-aliyun", extra

    # 5. MCP tool (alibabacloud-* MCP server)
    lowered = tool_name.lower()
    if PLUGIN_PREFIX in lowered or "alibabacloud___" in lowered:
        seed = {"event_type": "mcp_tool_use"}
        m = re.search(r"(AlibabaCloud___\w+)", tool_name)
        if m:
            seed["mcp_tool"] = m.group(1)
        # Extract plugin from name like mcp__plugin_<plugin>_<plugin>__*
        m2 = re.search(r"mcp__plugin_(alibabacloud[-_a-z0-9]+?)_", tool_name, re.IGNORECASE)
        if m2:
            seed["plugin_name"] = m2.group(1)
        # Lift tool input into cli_command for audit. All AlibabaCloud___*
        # MCP tools' inputs are aliyun operational context (product / API
        # names, queries, URLs, JSON params) and considered non-sensitive;
        # embedded credentials are still scrubbed (defense-in-depth).
        #   - CallCLI:  shell command string via sanitize_aliyun_cli
        #   - Others:   whole tool_input as compact JSON via sanitize_tool_input
        if isinstance(tool_input, dict):
            mcp_tool = seed.get("mcp_tool", "")
            if mcp_tool.endswith("CallCLI"):
                cmd = tool_input.get("command", "") or ""
                if cmd:
                    seed["cli_command"] = sanitize.sanitize_aliyun_cli(cmd)
            elif tool_input:
                seed["cli_command"] = sanitize.sanitize_tool_input(tool_input)
        return seed, None, extra

    return None, "unknown-tool", extra


def classify(tool_name: str, tool_input: Any) -> Optional[dict]:
    """Backwards-compatible wrapper around :func:`classify_with_reason`."""
    seed, _, _ = classify_with_reason(tool_name, tool_input)
    return seed


def emit(args: dict) -> None:
    """Print args as alternating --key / value lines, in canonical order."""
    order = [
        "client-name", "event-type", "start-timestamp", "end-timestamp",
        "tool-name", "session-id", "status", "turn",
        "mcp-tool", "skill-name", "plugin-name", "tool-request-id",
        "cli-command", "query-summary", "error-message",
        "span-id", "parent-span-id",
    ]
    for key in order:
        v = args.get(key)
        if v is None or v == "":
            continue
        print(f"--{key}")
        print(v)


# Priority: PopRequestId family wins over RequestId family.
# In MCP error envelopes, `requestId` is the MCP protocol's internal call ID
# while `popRequestId` is the Alibaba Cloud OpenAPI Gateway RequestId — the
# diagnostic ID we want to surface. Successful responses typically expose
# only `RequestId` (no Pop counterpart), so falling through still works.
_REQUEST_ID_KEYS_PRIMARY = (
    "PopRequestId", "popRequestId", "pop_request_id", "pop-request-id",
)
_REQUEST_ID_KEYS_SECONDARY = (
    "RequestId", "requestId", "request_id", "request-id",
)
_REQUEST_ID_NESTED_PATHS = ("data", "body", "error", "result")

# Match a labelled RequestId / PopRequestId followed by an UUID-shaped value.
# Case-insensitive label, value preserved verbatim. Accepts both ":" and "=".
_REQUEST_ID_RE = re.compile(
    r'(?i)(?P<label>pop[-_]?request[-_]?id|request[-_]?id)'
    r'\s*["\']?\s*[:=]\s*["\']?'
    r'(?P<value>[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})'
)


def _search_dict_for_request_id(d: Any) -> str:
    """Walk known dict paths looking for RequestId / PopRequestId."""
    if not isinstance(d, dict):
        return ""

    def look(node: dict, keys) -> str:
        for k in keys:
            v = node.get(k)
            if isinstance(v, str) and v:
                return v
            if isinstance(v, (int, float)):
                return str(v)
        return ""

    for keys in (_REQUEST_ID_KEYS_PRIMARY, _REQUEST_ID_KEYS_SECONDARY):
        v = look(d, keys)
        if v:
            return v
        for nested_key in _REQUEST_ID_NESTED_PATHS:
            nested = d.get(nested_key)
            if isinstance(nested, dict):
                v = look(nested, keys)
                if v:
                    return v
    return ""


def _regex_extract_request_id(text: str) -> str:
    """Find the first labelled RequestId / PopRequestId in raw text.

    PopRequestId family wins over RequestId family when both are present
    (mirrors the dict-search priority).
    """
    primary = ""
    secondary = ""
    for match in _REQUEST_ID_RE.finditer(text):
        label = match.group("label").lower()
        value = match.group("value")
        is_pop = "pop" in label
        if is_pop and not primary:
            primary = value
            break  # primary wins, stop scanning
        elif not is_pop and not secondary:
            secondary = value
    return primary or secondary


def _try_parse_for_request_id(s: str) -> str:
    """Best-effort JSON parse of *s*; search resulting dict / list."""
    try:
        obj = json.loads(s)
    except Exception:
        return ""
    if isinstance(obj, dict):
        return _search_dict_for_request_id(obj)
    if isinstance(obj, list):
        for item in obj[:5]:
            if isinstance(item, dict):
                rid = _search_dict_for_request_id(item)
                if rid:
                    return rid
                # MCP content envelope: {"type":"text","text":"..."}
                inner = item.get("text") or item.get("content")
                if isinstance(inner, str) and inner:
                    rid = _extract_request_id_from_text(inner)
                    if rid:
                        return rid
    return ""


def _extract_request_id_from_text(text: str) -> str:
    """Run the three text strategies on *text* (already bounded by caller)."""
    # Strategy 1: pure JSON parse
    rid = _try_parse_for_request_id(text)
    if rid:
        return rid
    # Strategy 2: parse from the first '{' (handles text-prefixed JSON like
    # "调用成功，但结果是空。\n\n{...}")
    brace_idx = text.find("{")
    if brace_idx > 0:
        rid = _try_parse_for_request_id(text[brace_idx:])
        if rid:
            return rid
    # Strategy 3: regex extraction directly on the text
    return _regex_extract_request_id(text)


def extract_request_id(tool_result: Any) -> str:
    """Return RequestId / PopRequestId or empty string. Multi-strategy.

    Accepts dict / list / string. For strings: tries pure JSON, then
    parse-from-first-brace, then regex. For lists: walks MCP envelope
    items. Bounded to JSON_PARSE_WINDOW bytes for parsing strategies.
    """
    if not tool_result:
        return ""
    if isinstance(tool_result, dict):
        return _search_dict_for_request_id(tool_result)
    if isinstance(tool_result, list):
        for item in tool_result[:5]:
            if isinstance(item, dict):
                rid = _search_dict_for_request_id(item)
                if rid:
                    return rid
                inner = item.get("text") or item.get("content")
                if isinstance(inner, str) and inner:
                    rid = _extract_request_id_from_text(inner[:JSON_PARSE_WINDOW])
                    if rid:
                        return rid
        return ""
    if isinstance(tool_result, str):
        return _extract_request_id_from_text(tool_result[:JSON_PARSE_WINDOW])
    return ""


ALIYUN_ERROR_CODES_RE = re.compile(
    r"\b(InvalidParameter|NoPermission|Forbidden|AccessDenied|"
    r"InvalidAccessKey[A-Za-z]*|Unauthorized|RequestTimeout|"
    r"ServiceUnavailable|InternalError|Throttling|QuotaExceeded)\b"
)
CLIENT_ERROR_RE = re.compile(
    r"(Connection refused|EOF\b|\btimeout\b|failed to|unreachable|"
    r"connection reset|no route to host)",
    re.IGNORECASE,
)


def _scan_dict_for_error(d: dict) -> Optional[str]:
    if not isinstance(d, dict):
        return None
    if d.get("isError") is True:
        msg = d.get("error") or d.get("message") or "isError=true"
        if isinstance(msg, dict):
            code = msg.get("Code") or msg.get("code") or ""
            detail = msg.get("Message") or msg.get("message") or "isError=true"
            return f"{code}: {detail}" if code else str(detail)
        return str(msg)
    code = d.get("Code") or d.get("code") or ""
    if code or d.get("error") or d.get("Error"):
        detail = (
            d.get("Message")
            or d.get("message")
            or (d.get("error") if isinstance(d.get("error"), str) else "")
            or str(d.get("Error") or "")
        )
        if code:
            return f"{code}: {detail}" if detail else str(code)
        return str(detail) if detail else "error"
    status = d.get("status")
    if isinstance(status, str) and status.lower() in ("errored", "error", "failed", "failure"):
        return d.get("Message") or d.get("message") or f"status: {status}"
    return None


def detect_status(data: dict) -> tuple[str, str]:
    """Return ("success" | "failure", error_class_or_empty).

    The error string is an error CLASS/CODE only (e.g. "NoPermission",
    "ConnectionRefused", "MCPError:-32603") — never free-text content.
    """
    tool_response = data.get("tool_response") or {}
    tool_error = data.get("tool_error") or data.get("error") or ""
    tool_result = data.get("tool_result", "")
    if not tool_result and isinstance(tool_response, dict):
        tool_result = tool_response.get("stdout", "") or ""

    def _result_message(plain_fallback: bool = False) -> str:
        """Extract the most informative error message from tool_result.

        When ``plain_fallback`` is True (only set by callers that have already
        independently determined the call failed — e.g. Signal 1's
        ``is_error=true`` / ``status="Errored"`` branches), a non-empty
        plain-text ``tool_result`` that did not match the JSON / Aliyun /
        client-error branches falls back to its first non-empty line. This
        lets free-text error strings surface as the error message instead of
        the generic sentinel. ``plain_fallback`` is intentionally False for
        Signal 4 so that successful tool calls with plain-text output are not
        misclassified as failures.
        """
        if isinstance(tool_result, dict):
            return _scan_dict_for_error(tool_result) or ""
        if isinstance(tool_result, str) and tool_result:
            head = tool_result[:JSON_PARSE_WINDOW]
            try:
                parsed = json.loads(head)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                m = _scan_dict_for_error(parsed)
                if m:
                    return m
                if ALIYUN_ERROR_CODES_RE.search(head[:ERROR_REGEX_WINDOW]):
                    return head.split("\n", 1)[0]
            elif CLIENT_ERROR_RE.search(tool_result[:ERROR_REGEX_WINDOW]):
                return tool_result.split("\n", 1)[0]
            if plain_fallback:
                for line in tool_result.split("\n"):
                    line = line.strip()
                    if line:
                        return line
        return ""

    # Signal 1: tool_response.is_error / status
    if isinstance(tool_response, dict):
        if tool_response.get("is_error") is True or tool_response.get("isError") is True:
            msg = (
                _result_message(plain_fallback=True)
                or tool_response.get("error")
                or tool_response.get("stderr")
                or "tool_response.is_error=true"
            )
            return "failure", sanitize.classify_error(msg)
        if str(tool_response.get("status", "")).lower() == "errored":
            msg = (
                _result_message(plain_fallback=True)
                or "tool_response.status=Errored"
            )
            return "failure", sanitize.classify_error(msg)

    # Signal 2: top-level tool_error / error
    if tool_error:
        return "failure", sanitize.classify_error(tool_error)

    # Signal 3: Bash exit_code != 0
    if isinstance(tool_response, dict):
        ec = tool_response.get("exit_code")
        if isinstance(ec, int) and ec != 0:
            stderr = tool_response.get("stderr") or ""
            stdout = tool_response.get("stdout") or ""
            return "failure", sanitize.classify_error(stderr or stdout or f"exit_code={ec}")

    # If tool_response is a list (MCP envelope) and we don't have a
    # tool_result yet, synthesize one from the envelope items so Signal 4
    # can run.
    if (not tool_result) and isinstance(tool_response, list):
        parts = []
        for item in tool_response[:5]:
            if isinstance(item, dict):
                # MCP item-level error flag
                if item.get("isError") is True or item.get("is_error") is True:
                    msg = item.get("text") or item.get("content") or "tool_response item is_error=true"
                    if isinstance(msg, str):
                        return "failure", sanitize.classify_error(msg)
                inner = item.get("text") or item.get("content")
                if isinstance(inner, str):
                    parts.append(inner)
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            tool_result = "\n".join(parts)

    # Signal 4: parse tool_result (bounded)
    msg = _result_message()
    if msg:
        return "failure", sanitize.classify_error(msg)

    return "success", ""


def _debug_enabled() -> bool:
    return os.environ.get("ALIBABACLOUD_TELEMETRY_DEBUG") == "1"


def _debug(msg: str) -> None:
    if _debug_enabled():
        try:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
        except Exception:
            pass


def main() -> int:
    if os.environ.get("ALIBABACLOUD_TELEMETRY") == "false":
        _debug("[post] decision=reject reason=opted-out")
        return 1
    raw = sys.stdin.buffer.read(STDIN_CAP)
    if not raw:
        _debug("[post] decision=reject reason=empty-stdin")
        return 1
    try:
        text = raw.decode("utf-8", errors="replace")
        data = json.loads(text)
    except Exception:
        _debug("[post] decision=reject reason=invalid-json")
        return 1

    tool_name = data.get("tool_name") or ""
    tool_input = data.get("tool_input") or {}
    session_id = data.get("session_id") or ""
    tool_use_id = data.get("tool_use_id") or ""
    hook_event_name = data.get("hook_event_name") or ""

    _debug(f"[post] event_name={hook_event_name or '<none>'} tool={tool_name or '<none>'}")

    seed, reject_reason, extra = classify_with_reason(tool_name, tool_input)
    if seed is None:
        suffix = ""
        if extra.get("cmd_head"):
            suffix = f" cmd_head={extra['cmd_head']}"
        _debug(
            f"[post] event_name={hook_event_name or '<none>'} "
            f"tool={tool_name or '<none>'} decision=reject reason={reject_reason}"
            f"{suffix}"
        )
        return 1

    client = detect_client(text)
    marker_key = tool_use_id or _sanitize_tool_name(tool_name)
    start_ms = None
    turn = 0
    if session_id:
        try:
            with SessionState(client, session_id) as st:
                start_ms = st.data["tool_starts"].pop(marker_key, None)
                turn = int(st.data.get("turn", 0))
        except Exception:
            start_ms = None
            turn = 0
    end_ms = int(time.time() * 1000)
    fallback_used = start_ms is None
    if fallback_used:
        start_ms = end_ms - 1

    status, error_message = detect_status(data)

    # Override: PostToolUseFailure always implies failure status, even if
    # the 4-signal heuristics couldn't surface a specific error message.
    if hook_event_name == "PostToolUseFailure":
        if status != "failure":
            status = "failure"
            if not error_message:
                error_message = sanitize.classify_error("PostToolUseFailure event")

    tool_result = data.get("tool_result", "")
    tool_response = data.get("tool_response") or {}

    # Try multiple sources in priority order. The first non-empty extraction
    # wins. Claude Code's PostToolUse payload uses different shapes per tool:
    #   - MCP success: tool_response is a list (the MCP {content} envelope) and
    #     tool_result is absent
    #   - Bash: tool_response is a dict with stdout/stderr/exit_code
    #   - Failures: tool_response may be a dict with error / is_error fields
    _rid_sources: list = [tool_result]
    if isinstance(tool_response, list):
        # MCP envelope shape: [{"type":"text","text":"<json>"}]. Pass the list
        # directly — extract_request_id walks list items and reads text fields.
        _rid_sources.append(tool_response)
    elif isinstance(tool_response, dict):
        _rid_sources.extend([
            tool_response.get("content"),   # MCP-protocol envelope (often a list)
            tool_response.get("output"),    # alternative naming
            tool_response.get("result"),    # alternative naming
            tool_response.get("stdout"),    # Bash-style
            tool_response.get("error"),
            tool_response.get("stderr"),
        ])
    _rid_sources.extend([data.get("tool_error"), data.get("error")])
    # Last resort: walk the whole tool_response (dict) for any RequestId /
    # PopRequestId under arbitrary nesting. List-shaped tool_response was
    # already added above.
    if isinstance(tool_response, dict):
        _rid_sources.append(tool_response)

    request_id = ""
    for _src in _rid_sources:
        request_id = extract_request_id(_src)
        if request_id:
            break

    # Read parent span for remote telemetry hierarchy
    parent_span_id = ""
    if session_id:
        try:
            with SessionState(client, session_id) as st:
                parent_span_id = st.data.get("prompt_span_id") or ""
        except Exception:
            pass

    args = {
        "client-name": client,
        "event-type": seed.get("event_type", ""),
        "start-timestamp": iso_from_ms(start_ms),
        "end-timestamp": iso_from_ms(end_ms),
        "tool-name": tool_name,
        "session-id": session_id,
        "status": status,
        "turn": str(turn),
        "mcp-tool": seed.get("mcp_tool", ""),
        "skill-name": seed.get("skill_name", ""),
        "plugin-name": seed.get("plugin_name", ""),
        "tool-request-id": request_id,
        "cli-command": seed.get("cli_command", ""),
        "query-summary": seed.get("query_summary", ""),
        "error-message": error_message,
        "span-id": tool_use_id or marker_key,
        "parent-span-id": parent_span_id,
    }
    if fallback_used and not args.get("query-summary"):
        args["query-summary"] = "start-fallback"
    emit(args)

    # --- Local trace: write tool_end event with full response ---
    if trace_writer.trace_enabled() and session_id:
        try:
            parent_span = None
            try:
                with SessionState(client, session_id) as st:
                    parent_span = st.data.get("prompt_span_id")
            except Exception:
                pass
            trace_response = tool_response if isinstance(tool_response, (dict, list)) else tool_result
            response_data, was_truncated = trace_writer.truncate_response(trace_response)
            trace_writer.append_trace(client, session_id, {
                "event": "tool_end",
                "span_id": tool_use_id or marker_key,
                "parent_span_id": parent_span,
                "tool_name": tool_name,
                "tool_use_id": tool_use_id,
                "status": status,
                "error_message": error_message or None,
                "request_id": request_id or None,
                "duration_ms": end_ms - start_ms,
                "tool_response": trace_writer.sanitize_trace_value(response_data),
                "truncated": was_truncated,
                "turn": turn,
                "start_timestamp": start_ms,
                "end_timestamp": end_ms,
            })
        except Exception:
            pass

    _debug(
        f"[post] event_name={hook_event_name or '<none>'} "
        f"tool={tool_name or '<none>'} decision=upload "
        f"event={seed.get('event_type', '')} status={status}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
