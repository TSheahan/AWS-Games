#!/usr/bin/env bash
# Workstation checkout setup for this repository.
# Creates venv/ at the repo root and installs [project].dependencies from pyproject.toml.
# Include paths in [tool.project-setup].pythonpath are written into the venv as a .pth.
#
# This is not the EC2 instance provisioner. That script is ec2/minecraft/setup.sh.

set -euo pipefail

cd "$(dirname "$0")"

pick_python() {
  local cmd ver bin
  for cmd in python3.13 python3; do
    if command -v "$cmd" >/dev/null 2>&1 \
      && "$cmd" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null; then
      command -v "$cmd"
      return 0
    fi
  done
  # pyenv shims exist on PATH even when the version is not selected.
  if command -v pyenv >/dev/null 2>&1; then
    while IFS= read -r ver; do
      bin="$(pyenv root)/versions/${ver}/bin/python"
      if [[ -x "$bin" ]] \
        && "$bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)'; then
        echo "$bin"
        return 0
      fi
    done < <(pyenv versions --bare | sort -V)
  fi
  echo "Python >= 3.13 is required (python3.13, python3, or a pyenv version)." >&2
  return 1
}

PYTHON="$(pick_python)"

if [[ ! -d venv ]]; then
  "$PYTHON" -m venv venv
fi

venv/bin/python -B - <<'PY'
import subprocess
import sys
import tomllib
from pathlib import Path
from sysconfig import get_path

root = Path.cwd()
cfg = tomllib.loads((root / "pyproject.toml").read_text())
deps = cfg["project"]["dependencies"]
subprocess.check_call([sys.executable, "-m", "pip", "install", "-U", "pip", *deps])
paths = cfg.get("tool", {}).get("project-setup", {}).get("pythonpath", [])
pth = Path(get_path("purelib")) / "project-setup.pth"
pth.write_text("".join(str((root / p).resolve()) + "\n" for p in paths))
print("pth", pth)
print(pth.read_text(), end="")
PY

echo "Invoke workstation tools with: venv/bin/python -B bin/<script>"
