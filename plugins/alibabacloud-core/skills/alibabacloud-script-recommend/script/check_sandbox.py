#!/usr/bin/env python3
"""
Lightweight regex-based RunScript sandbox pre-checker.
Catches common agent coding mistakes before remote validation.
Zero dependencies — pure stdlib regex only.

Usage:
    python3 check_sandbox.py <script.py> [...]
    python3 check_sandbox.py --dir <dir>
"""
import re
import sys
from pathlib import Path

# ── Rules ────────────────────────────────────────────────────────────

IMPORT_WHITELIST = {
    "asyncio", "collections", "csv", "dataclasses", "datetime", "decimal",
    "enum", "fractions", "functools", "itertools", "json", "math", "re",
    "statistics", "string", "time", "typing", "uuid",
}

FORBIDDEN_FUNCS = [
    "eval", "exec", "compile", "__import__",
    "getattr", "setattr", "hasattr", "delattr",
    "globals", "locals", "vars",
]


def check(source: str) -> list[dict]:
    """Return list of {rule_id, line, message} violations."""
    violations = []
    lines = source.splitlines()

    has_result = False

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Skip comments and blank lines
        if not stripped or stripped.startswith("#"):
            continue

        # ── SEC-4001: forbidden import ──
        m = re.match(r'^(?:import|from)\s+([\w.]+)', stripped)
        if m:
            mod = m.group(1).split(".")[0]
            if mod not in IMPORT_WHITELIST:
                violations.append({
                    "rule_id": "SEC-4001", "line": i,
                    "message": f"Forbidden import: {mod}",
                })

        # ── OBF-3018: print() ──
        if re.search(r'\bprint\s*\(', stripped):
            violations.append({
                "rule_id": "OBF-3018", "line": i,
                "message": "print() disallowed; use result",
            })

        # ── SEC-4002: eval / exec / reflection ──
        for fn in FORBIDDEN_FUNCS:
            if re.search(rf'\b{fn}\s*\(', stripped):
                violations.append({
                    "rule_id": "SEC-4002", "line": i,
                    "message": f"Forbidden call: {fn}()",
                })

        # ── SEC-4002: input() ──
        if re.search(r'\binput\s*\(', stripped):
            violations.append({
                "rule_id": "SEC-4002", "line": i,
                "message": "input() disallowed in sandbox",
            })

        # ── SLP-3001: time.sleep > 30 ──
        m_sleep = re.search(r'time\.sleep\s*\(\s*(\d+)', stripped)
        if m_sleep and int(m_sleep.group(1)) > 30:
            violations.append({
                "rule_id": "SLP-3001", "line": i,
                "message": f"time.sleep({m_sleep.group(1)}) > 30s",
            })

        # ── result assignment ──
        if re.match(r'^\s*result\s*=', line):
            has_result = True

    if not has_result:
        violations.append({
            "rule_id": "OUT-3001", "line": 0,
            "message": "No 'result = ...' assignment found",
        })

    return violations


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: check_sandbox.py <script.py> [...] | --dir <dir>")
        sys.exit(1)

    files = []
    if sys.argv[1] == "--dir":
        files = sorted(Path(sys.argv[2]).glob("*.py"))
    else:
        files = [Path(f) for f in sys.argv[1:]]

    passed = failed = 0
    for f in files:
        vs = check(f.read_text())
        if not vs:
            print(f"  PASS  {f.name}")
            passed += 1
        else:
            print(f"  FAIL  {f.name} ({len(vs)})")
            for v in vs:
                ln = f"L{v['line']}" if v["line"] else "   "
                print(f"        {ln}  [{v['rule_id']}] {v['message']}")
            failed += 1

    total = passed + failed
    print(f"\n  {passed}/{total} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
