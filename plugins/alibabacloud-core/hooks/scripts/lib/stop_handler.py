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
import token_recorder  # noqa: E402

EMPTY_TOKENS = {
    "input_uncached": 0, "input_cached": 0, "input_creation": 0,
    "output": 0, "reasoning": 0,
}


def _add_tokens(a: dict, b: dict) -> dict:
    out = dict(a)
    for k in EMPTY_TOKENS:
        av = out.get(k) or 0
        bv = b.get(k) or 0
        out[k] = av + bv
    return out


def _walk_skill_ancestor(span_id: str, parent_map: dict, skill_set: set) -> str:
    seen = set()
    cur = span_id
    while cur and cur not in seen:
        seen.add(cur)
        if cur in skill_set:
            return cur
        cur = parent_map.get(cur)
    return ""


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

            # --- Token recorder: read transcript slice (always advance offset) ---
            transcript_path = data.get("transcript_path") or ""
            tokens_offset = int(st.data.get("tokens_offset", 0))
            tokens_call_index = int(st.data.get("tokens_call_index", 0))
            tokens_parser_state = st.data.get("tokens_parser_state") or {}
            fallback_turn_id = f"stop-{current_turn}"
            token_rows: list[dict] = []
            new_offset = tokens_offset
            new_call_index = tokens_call_index
            new_parser_state = tokens_parser_state
            try:
                token_rows, new_offset, new_call_index, new_parser_state = (
                    token_recorder.process_stop(
                        client, transcript_path, tokens_offset,
                        tokens_call_index, tokens_parser_state,
                        fallback_turn_id,
                    )
                )
            except Exception:
                token_rows = []

            # Always advance offsets, even when this turn is not traced —
            # otherwise the next traced turn would re-attribute these tokens.
            st.data["tokens_offset"] = new_offset
            st.data["tokens_call_index"] = new_call_index
            if isinstance(new_parser_state, dict):
                st.data["tokens_parser_state"] = new_parser_state

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

                # Aggregate token rows
                turn_spans = st.data.get("turn_spans") or []
                parent_map = {s["span_id"]: s.get("parent_span_id") for s in turn_spans}
                skill_set = {s["span_id"] for s in turn_spans if s.get("kind") == "skill_invocation"}
                # Reverse map: tool_use_id → span_id (they're equal in our pre_handler)
                tool_use_to_span = {
                    s["tool_use_id"]: s["span_id"]
                    for s in turn_spans
                    if s.get("tool_use_id")
                }

                turn_tokens = dict(EMPTY_TOKENS)
                tool_tokens: dict = {}
                skill_tokens: dict = {sid: dict(EMPTY_TOKENS) for sid in skill_set}

                for row in token_rows:
                    n = row.get("normalized") or {}
                    turn_tokens = _add_tokens(turn_tokens, n)
                    for tu_id in row.get("tool_use_ids") or []:
                        span_id = tool_use_to_span.get(tu_id) or tu_id
                        tool_tokens[span_id] = {
                            "call_index": row.get("call_index"),
                            "model": row.get("model"),
                            "llm_tokens": dict(n),
                        }
                        anc = _walk_skill_ancestor(span_id, parent_map, skill_set)
                        if anc:
                            skill_tokens[anc] = _add_tokens(skill_tokens[anc], n)

                # Update cumulative session total (only counts traced turns)
                session_total = st.data.get("aliyun_session_tokens") or dict(EMPTY_TOKENS)
                session_total = _add_tokens(session_total, turn_tokens)
                st.data["aliyun_session_tokens"] = session_total

                trace_writer.append_trace(client, session_id, {
                    "event": "turn_end",
                    "span_id": _uuid.uuid4().hex[:16],
                    "parent_span_id": prompt_span,
                    "stop_reason": hook_event_name,
                    "turn": current_turn,
                    "start_timestamp": stop_ts,
                    "end_timestamp": stop_ts,
                    "turn_tokens": turn_tokens,
                    "aliyun_session_tokens": session_total,
                    "tool_tokens": tool_tokens,
                    "skill_tokens": skill_tokens,
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
                st.data["current_skill_span_id"] = None
                st.data["turn_spans"] = []

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
