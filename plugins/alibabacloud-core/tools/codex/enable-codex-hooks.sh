#!/bin/bash
# Enable Codex plugin hooks for alibabacloud-core (idempotent).
#
# Patches ~/.codex/config.toml:
#   [features] hooks=true, plugin_hooks=true
#   [hooks.state."<marketplace>:hooks/codex-hooks.json:<event>:<i>:<j>"] enabled=true
#
# The marketplace name is auto-detected from existing
# [plugins."alibabacloud-core@<NAME>"] entries. Falls back to
# "alibabacloud-agent-toolkit".
#
# Runs without `tomllib` (Python 3.10 compatibility): bash + sed/awk.
set -eu

CONFIG="${HOME}/.codex/config.toml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOKS_JSON="$PLUGIN_ROOT/hooks/codex-hooks.json"

if [ ! -f "$HOOKS_JSON" ]; then
    echo "FAIL: $HOOKS_JSON not found" >&2
    exit 2
fi

mkdir -p "$(dirname "$CONFIG")"
[ -f "$CONFIG" ] || printf '' > "$CONFIG"

ts=$(date +%s).$$
cp "$CONFIG" "$CONFIG.bak.$ts"
echo "Backup: $CONFIG.bak.$ts"

# Detect marketplace name from existing plugin entry
marketplace=$(grep -oE '\[plugins\."alibabacloud-core@[^"]+"\]' "$CONFIG" 2>/dev/null \
    | head -1 \
    | sed -E 's/.*@([^"]+).*/\1/')
marketplace="${marketplace:-alibabacloud-agent-toolkit}"
echo "Marketplace: $marketplace"

# --- Helper: ensure a [section] exists with key=value (idempotent) ---
ensure_kv_in_section() {
    local section="$1" key="$2" value="$3"
    python3 - "$CONFIG" "$section" "$key" "$value" <<'PY'
import re, sys
path, section, key, value = sys.argv[1:]
with open(path) as f: text = f.read()
header = f"[{section}]"
# Strip existing key inside this section
def strip_key(block):
    return re.sub(rf'(?m)^{re.escape(key)}\s*=.*\n?', '', block)
if header in text:
    # Operate only on this section's body (until next [ at line start or EOF)
    pat = re.compile(rf'(\[{re.escape(section)}\][ \t]*\n)(.*?)(?=\n\[|\Z)', re.S)
    m = pat.search(text)
    body = strip_key(m.group(2))
    new_body = body.rstrip() + f"\n{key} = {value}\n"
    text = text[:m.start(2)] + new_body + text[m.end(2):]
else:
    sep = "" if text.endswith("\n") or text == "" else "\n"
    text += f"{sep}{header}\n{key} = {value}\n"
open(path, "w").write(text)
PY
}

ensure_kv_in_section "features" "hooks" "true"
ensure_kv_in_section "features" "plugin_hooks" "true"

# --- For each hook entry in codex-hooks.json, set enabled=true + trusted_hash ---
python3 - "$CONFIG" "$HOOKS_JSON" "$marketplace" <<'PY'
import hashlib, json, re, sys
config_path, hooks_path, marketplace = sys.argv[1:]
hooks = json.load(open(hooks_path))
text = open(config_path).read()

# event-name → snake_case mapping observed in Codex config.toml
EVENT_MAP = {
    "PreToolUse": "pre_tool_use",
    "PostToolUse": "post_tool_use",
    "UserPromptSubmit": "user_prompt_submit",
    "Stop": "stop",
}

def upsert_section(text, header, kv_pairs):
    pat = re.compile(rf'(\[{re.escape(header)}\][ \t]*\n)(.*?)(?=\n\[|\Z)', re.S)
    m = pat.search(text)
    if m:
        body = m.group(2)
        for k, _ in kv_pairs:
            body = re.sub(rf'(?m)^{re.escape(k)}\s*=.*\n?', '', body)
        body = body.rstrip()
        addition = "".join(f"{k} = {v}\n" for k, v in kv_pairs)
        new_body = (body + "\n" if body else "") + addition
        return text[:m.start(2)] + new_body + text[m.end(2):]
    sep = "" if text.endswith("\n") or text == "" else "\n"
    body = "".join(f"{k} = {v}\n" for k, v in kv_pairs)
    return text + f"{sep}[{header}]\n{body}"

for evt_name, groups in hooks.get("hooks", {}).items():
    snake = EVENT_MAP.get(evt_name, evt_name.lower())
    for i, group in enumerate(groups or []):
        for j, h in enumerate(group.get("hooks") or []):
            cmd = h.get("command", "")
            if not cmd:
                continue
            digest = "sha256:" + hashlib.sha256(cmd.encode("utf-8")).hexdigest()
            section = f'hooks.state."{marketplace}:hooks/codex-hooks.json:{snake}:{i}:{j}"'
            text = upsert_section(text, section, [
                ("enabled", "true"),
                ("trusted_hash", f'"{digest}"'),
            ])

open(config_path, "w").write(text)
print("Updated:", config_path)
PY

echo "Done. Restart Codex CLI for changes to take effect."
