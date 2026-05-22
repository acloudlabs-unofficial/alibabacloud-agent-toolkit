#!/usr/bin/env python3
"""Stop / StopFailure hook handler.

Increments per-session turn counter. When the turn involved alibabacloud
tools (turn_has_trace flag), emits a `user_prompt_turn_start` event to
stdout for remote telemetry upload, and writes local trace events.

Exit codes:
    0 — event emitted to stdout (caller should upload)
    1 — no event emitted (nothing to upload)
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid as _uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state import SessionState, cleanup_stale_sessions  # noqa: E402
import trace_writer  # noqa: E402

DEBUG = os.environ.get("ALIBABACLOUD_TELEMETRY_DEBUG") == "1"

_EMIT_ORDER = [
    "client-name", "event-type", "start-timestamp", "end-timestamp",
    "tool-name", "session-id", "status", "turn",
    "mcp-tool", "skill-name", "plugin-name", "tool-request-id",
    "cli-command", "query-summary", "error-message",
    "span-id", "parent-span-id",
]


def _detect_client(payload_str: str) -> str:
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


def _iso_from_ms(ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ms / 1000.0))


def _emit(args: dict) -> None:
    for key in _EMIT_ORDER:
        v = args.get(key)
        if v is None or v == "":
            continue
        print(f"--{key}")
        print(v)


def _debug(msg: str) -> None:
    if DEBUG:
        try:
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
        except Exception:
            pass


def main() -> int:
    if os.environ.get("ALIBABACLOUD_TELEMETRY") == "false":
        _debug("[stop] decision=skip reason=opted-out")
        return 1
    raw = sys.stdin.buffer.read(65536)
    if not raw:
        _debug("[stop] decision=skip reason=empty-stdin")
        return 1
    text = raw.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except Exception:
        data = {}
    session_id = data.get("session_id") or ""
    if not session_id:
        _debug("[stop] decision=skip reason=no-session-id")
        return 1
    client = _detect_client(text)
    hook_event_name = data.get("hook_event_name") or "Stop"

    new_turn = 0
    should_emit = False
    emit_args: dict = {}
    try:
        with SessionState(client, session_id) as st:
            turn_has_trace = st.data.get("turn_has_trace", False)
            prompt_span = st.data.get("prompt_span_id") or ""
            pending_prompt_ts = st.data.get("pending_prompt_ts")
            current_turn = int(st.data.get("turn", 0))
            stop_ts = int(time.time() * 1000)

            # --- Local trace: backfill prompt and write turn_end ---
            if trace_writer.trace_enabled() and turn_has_trace:
                pending = st.data.get("pending_prompt")
                if pending:
                    trace_writer.append_trace(client, session_id, {
                        "event": "prompt",
                        "span_id": prompt_span,
                        "parent_span_id": None,
                        "prompt": trace_writer.sanitize_trace_value(pending),
                        "turn": current_turn,
                        "start_timestamp": pending_prompt_ts,
                        "end_timestamp": stop_ts,
                    })
                trace_writer.append_trace(client, session_id, {
                    "event": "turn_end",
                    "span_id": _uuid.uuid4().hex[:16],
                    "parent_span_id": prompt_span,
                    "stop_reason": hook_event_name,
                    "turn": current_turn,
                    "start_timestamp": stop_ts,
                    "end_timestamp": stop_ts,
                })

            # --- Remote telemetry: emit user_prompt_turn_start ---
            if turn_has_trace and prompt_span:
                start_ts = pending_prompt_ts or stop_ts
                emit_args = {
                    "client-name": client,
                    "event-type": "user_prompt_turn_start",
                    "start-timestamp": _iso_from_ms(start_ts),
                    "end-timestamp": _iso_from_ms(stop_ts),
                    "tool-name": "user_prompt",
                    "session-id": session_id,
                    "status": "success",
                    "turn": str(current_turn),
                    "span-id": prompt_span,
                }
                should_emit = True

            # Reset trace state for next turn
            if trace_writer.trace_enabled() or turn_has_trace:
                st.data.pop("turn_has_trace", None)
                st.data.pop("pending_prompt", None)
                st.data.pop("pending_prompt_ts", None)
                st.data.pop("prompt_span_id", None)

            # Increment turn (existing behavior)
            st.data["turn"] = int(st.data.get("turn", 0)) + 1
            new_turn = st.data["turn"]
    except Exception:
        pass

    if should_emit:
        _emit(emit_args)
        _debug(
            f"[stop] turn={new_turn} session={session_id} client={client} "
            f"decision=upload event=user_prompt_turn_start"
        )
        # Opportunistic cleanup (cheap)
        try:
            cleanup_stale_sessions(client)
            trace_writer.cleanup_stale_traces()
        except Exception:
            pass
        return 0

    _debug(f"[stop] turn={new_turn} session={session_id} client={client} decision=no-emit")
    try:
        cleanup_stale_sessions(client)
        trace_writer.cleanup_stale_traces()
    except Exception:
        pass
    return 1


if __name__ == "__main__":
    sys.exit(main())
