#!/bin/bash
# Integration test for Codex client trace flow (token aggregates + span tree).
set -e

scriptDir="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$(cd "$scriptDir/../../plugins/alibabacloud-core/hooks/scripts" && pwd)"
fixturesDir="$scriptDir/test-fixtures/trace"

stateDir="$(mktemp -d)"
traceDir="$(mktemp -d)"
transcriptPath="$(mktemp)"
trap 'rm -rf "$stateDir" "$traceDir" "$transcriptPath"' EXIT

# Use Codex transcript fixture as the live transcript
cp "$fixturesDir/codex-transcript-sample.jsonl" "$transcriptPath"

# Materialize the stop payload with the real path
sed "s#__TRANSCRIPT_PATH__#$transcriptPath#" "$fixturesDir/codex-stop.json" > "$stateDir/codex-stop.json"

export ALIBABACLOUD_TELEMETRY_STATE_DIR="$stateDir"
export ALIBABACLOUD_TRACE_DIR="$traceDir"
export ALIBABACLOUD_TELEMETRY="true"
export ALIBABACLOUD_TRACE="true"
export CODEX_CLI=1

echo "=== Codex trace flow ==="

python3 "$HOOKS_DIR/lib/prompt_handler.py" < "$fixturesDir/codex-prompt.json"   > /dev/null 2>&1 || true
python3 "$HOOKS_DIR/lib/pre_handler.py"    < "$fixturesDir/codex-pre.json"      > /dev/null 2>&1 || true
python3 "$HOOKS_DIR/lib/post_handler.py"   < "$fixturesDir/codex-post.json"     > /dev/null 2>&1 || true
python3 "$HOOKS_DIR/lib/stop_handler.py"   < "$stateDir/codex-stop.json"        > /dev/null 2>&1 || true

traceFile="$traceDir/sess-codex.jsonl"
if [ ! -f "$traceFile" ]; then
    echo "FAIL: trace file missing"
    exit 1
fi

python3 - <<PY
import json, sys
events = [json.loads(l) for l in open("$traceFile") if l.strip()]
kinds = [e["event"] for e in events]
assert "tool_start" in kinds, kinds
assert "tool_end" in kinds, kinds
assert "turn_end" in kinds, kinds
turn_end = next(e for e in events if e["event"] == "turn_end")
assert "turn_tokens" in turn_end, turn_end
assert turn_end["turn_tokens"]["input_uncached"] == 15151 - 11648, turn_end["turn_tokens"]
assert "tool_tokens" in turn_end, turn_end
assert "call_x" in turn_end["tool_tokens"], turn_end["tool_tokens"]
print("PASS: codex trace flow")
PY
