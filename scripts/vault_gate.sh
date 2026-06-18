#!/usr/bin/env bash
# Blocking Obsidian vault ontology gate — run BEFORE any code locate/edit session.
set -euo pipefail
QUERY="${1:?usage: vault_gate.sh '<query>'}"
PURPOSE="${2:-code-edit}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
VAULT_ROOT="${HFT3_VAULT_ROOT:-$HOME/Desktop/Obsidian Vault From VPS/hft3}"
STAMP_DIR="$REPO_ROOT/runtime/vault-gate"
STAMP="$STAMP_DIR/.last-vault-gate.json"
mkdir -p "$STAMP_DIR"
for f in wiki/hot.md Home.md "Memory Stack.md"; do
  test -f "$VAULT_ROOT/$f" || { echo "VaultGate required file missing: $VAULT_ROOT/$f" >&2; exit 1; }
done
HITS=""
if command -v rg >/dev/null 2>&1; then
  HITS=$(rg -n -i --max-count 8 "$QUERY" "$VAULT_ROOT" -g '*.md' 2>/dev/null || true)
fi
python - <<PY
import json, datetime, pathlib
vault = pathlib.Path(r"$VAULT_ROOT")
hot = (vault / "wiki/hot.md").read_text(encoding="utf-8")[:3000]
mem = (vault / "Memory Stack.md").read_text(encoding="utf-8")[:2000]
waiver = "waived-by-owner-2026-06-16" if "Temporary graph waiver" in hot else "active"
payload = {
    "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "purpose": "$PURPOSE",
    "query": """$QUERY""",
    "vault_root": str(vault),
    "required_reads": ["wiki/hot.md", "Home.md", "Memory Stack.md"],
    "hot_excerpt": hot,
    "memory_excerpt": mem,
    "search_hits": """$HITS""".splitlines(),
    "graph_gates": waiver,
    "repo_root": r"$REPO_ROOT",
}
pathlib.Path(r"$STAMP").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print("Wrote", r"$STAMP")
if waiver.startswith("waived"):
    print("NOTE: graph gates waived-by-owner-2026-06-16")
print("OK: vault ontology consult recorded.")
PY
