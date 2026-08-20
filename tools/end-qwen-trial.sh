#!/usr/bin/env bash
# Revert the 2026-08-20 qwen3.8-27b-q4 trial: put Hermes back on deepseek-v4-flash
# and make DS4 resident again.
#
# The trial changed exactly TWO keys in ~/.hermes/config.yaml (model.default and
# the Local Models provider-level model). The check-in crons and OpenWebUI were
# deliberately LEFT on deepseek-v4-flash, so nothing else needs reverting.
#
# Backup of the pre-trial config: ~/.hermes/config.yaml.bak-20260820-pre-qwen-trial
set -euo pipefail

CFG="$HOME/.hermes/config.yaml"

python3 - "$CFG" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
n = 0
for old, new in (
    ("model:\n  default: qwen3.8-27b-q4\n",
     "model:\n  default: deepseek-v4-flash\n"),
    ("    api_key: unused-llama-router-direct\n    model: qwen3.8-27b-q4\n",
     "    api_key: unused-llama-router-direct\n    model: deepseek-v4-flash\n"),
):
    if old in s:
        s = s.replace(old, new); n += 1
open(p, "w").write(s)
print(f"config.yaml: {n}/2 keys reverted"
      + ("" if n == 2 else "  <- CHECK MANUALLY, config may have changed since"))
PY

echo "Restoring deepseek-v4-flash (this evicts qwen; expect 3-11 min)..."
bash "$(dirname "$0")/swap-model.sh" ds4

echo
echo "Done. Reminders:"
echo "  - A RUNNING Hermes session keeps its pinned model; start a NEW session."
echo "  - Verify: hermes config get model.default  -> deepseek-v4-flash"
