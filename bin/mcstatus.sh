#!/usr/bin/env bash
# ~/mcstatus  –  shows status of ALL minecraft-*.service files (even disabled/inactive/unloaded)

set -euo pipefail

# List all discovered .service files matching the glob (from filesystem)
systemctl list-unit-files --no-legend 'minecraft-*.service' \
  | while read -r unit state; do
      # unit is e.g. "minecraft-famine.service"
      # Query current runtime status (loads it if needed)
      status=$(systemctl is-active "$unit" 2>/dev/null || echo "unknown")

      case "$status" in
        active)   mark="🟢 running" ;;
        inactive) mark="⚪ stopped" ;;
        failed)   mark="🔴 FAILED"  ;;
        *)        mark="❓ $status" ;;
      esac

      # Print aligned output
      printf "%-28s %s\n" "$unit" "$mark"
    done \
  | sort -k1
