#!/usr/bin/env bash

# Script to check and perform Amazon Linux 2023 release version upgrade
# Run as ec2-user

set -euo pipefail

echo "Checking for new Amazon Linux 2023 release version..."

# Fetch latest available version
latest_version=$(sudo dnf check-release-update --latest-only --version-only 2>/dev/null || true)

if [ -z "$latest_version" ]; then
    echo "No newer release version available."
    exit 0
fi

current_version=$(cat /etc/os-release | grep VERSION_ID | cut -d= -f2 | tr -d '"')

echo "Current version: $current_version"
echo "Latest available version: $latest_version"
echo
echo "Upgrade command will be: sudo dnf upgrade --releasever=$latest_version -y"
echo

read -p "Proceed with upgrade to $latest_version? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
    echo "Upgrade cancelled."
    exit 0
fi

echo "Starting upgrade..."
sudo dnf upgrade --releasever="$latest_version" -y

echo "Upgrade completed. A reboot is recommended to apply the new release."
