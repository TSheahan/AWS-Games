#!/usr/bin/env python3
"""
provision_servers.py

Provisions and manages per-server Minecraft systemd units and wrapper scripts
based on the configuration in minecraft-servers.yaml pulled from a remote repo.

This script is intended to be run as root (typically from setup.sh or manually
via the update wrapper script).

Requires:
- PyYAML (from requirements.txt)
- Access to /mnt/persist (must be mounted)
- git installed (from UserData bootstrap)

Usage:
    python3 provision_servers.py [--update] [--read-only]

    --update      Pull latest config from remote repo before processing
    --read-only   Validate and log without writing files or changing system state
"""

import argparse
import json
import logging
import os
import pathlib
import subprocess
import sys
from datetime import datetime
from typing import Dict, Any, List

import yaml

# ------------------------------------------------------------------------------
# Configuration constants
# ------------------------------------------------------------------------------

CONFIG_REPO_URL = "https://github.com/TSheahan/AWS-Games-Config.git"
CONFIG_LOCAL_DIR = "/home/ec2-user/minecraft-config"
CONFIG_FILE_NAME = "minecraft-servers.yaml"
CONFIG_PATH = os.path.join(CONFIG_LOCAL_DIR, CONFIG_FILE_NAME)

PERSIST_ROOT = "/mnt/persist"
MINECRAFT_ROOT = os.path.join(PERSIST_ROOT, "minecraft")
PORTS_JSON_PATH = "/home/ec2-user/game-ports.json"
LOG_FILE = "/var/log/minecraft-provision.log"

EC2_USER = "ec2-user"

# ------------------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def run_cmd(cmd: List[str], check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess:
    """Run a shell command and log it. capture_output=True returns stdout."""
    logger.debug("Running command: %s", " ".join(cmd))
    result = subprocess.run(
        cmd,
        check=check,
        capture_output=capture_output,
        text=True
    )
    if capture_output and result.stdout:
        logger.debug("Command output: %s", result.stdout.strip())
    return result


# Ensure log file is present and readable by non-root users
log_path = pathlib.Path(LOG_FILE)
if not log_path.exists():
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch(mode=0o644)
    run_cmd(["chown", "root:root", str(log_path)], check=False)
    logger.info("Created log file %s (mode 0644)", LOG_FILE)
else:
    current_mode = log_path.stat().st_mode & 0o777
    if (current_mode & 0o004) == 0:
        log_path.chmod(current_mode | 0o004)
        logger.info("Made log file %s world-readable", LOG_FILE)


# ------------------------------------------------------------------------------
# Guarded operations (skipped in --read-only mode)
# ------------------------------------------------------------------------------

def guarded_mkdir(path: pathlib.Path, read_only: bool) -> None:
    if read_only:
        logger.info("[READ-ONLY] Would create directory %s", path)
        return
    path.mkdir(parents=True, exist_ok=True)
    run_cmd(["chown", f"{EC2_USER}:{EC2_USER}", str(path)])
    logger.info("Created directory %s", path)


def guarded_write_text(path: pathlib.Path, content: str, mode: int = 0o644, read_only: bool = False) -> None:
    if read_only:
        logger.info("[READ-ONLY] Would write to %s", path)
        return
    path.write_text(content)
    path.chmod(mode)
    logger.info("Wrote file %s", path)


def guarded_chmod_chown(path: pathlib.Path, mode: int, read_only: bool) -> None:
    if read_only:
        logger.info("[READ-ONLY] Would chmod/chown %s", path)
        return
    path.chmod(mode)
    run_cmd(["chown", f"{EC2_USER}:{EC2_USER}", str(path)])
    logger.debug("Set permissions/ownership on %s", path)


def guarded_systemctl(commands: List[str], read_only: bool) -> None:
    if read_only:
        logger.info("[READ-ONLY] Would run: systemctl %s", " ".join(commands))
        return
    run_cmd(["systemctl"] + commands)
    logger.info("Executed: systemctl %s", " ".join(commands))


# ------------------------------------------------------------------------------
# Config & validation
# ------------------------------------------------------------------------------

def ensure_config_repo(read_only: bool) -> None:
    """Clone or update the config repository."""
    config_dir = pathlib.Path(CONFIG_LOCAL_DIR)
    if not config_dir.exists():
        if read_only:
            logger.info("[READ-ONLY] Would clone config repo to %s", config_dir)
            return
        logger.info("Cloning config repo: %s", CONFIG_REPO_URL)
        config_dir.parent.mkdir(parents=True, exist_ok=True)
        run_cmd(["git", "clone", CONFIG_REPO_URL, str(config_dir)])
    else:
        logger.info("Updating config repo at %s", config_dir)
        run_cmd(["git", "-C", str(config_dir), "pull"])


def load_config() -> Dict[str, Any]:
    if not os.path.isfile(CONFIG_PATH):
        raise FileNotFoundError(f"Config file not found: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if "servers" not in config or not isinstance(config["servers"], dict):
        raise ValueError("Config missing or invalid 'servers' mapping")
    if "provisioned" not in config or not isinstance(config["provisioned"], list):
        raise ValueError("Config missing or invalid 'provisioned' list")

    return config


def load_port_range() -> tuple[int, int]:
    if not os.path.isfile(PORTS_JSON_PATH):
        raise FileNotFoundError(f"Ports config not found: {PORTS_JSON_PATH}")
    with open(PORTS_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return int(data["start"]), int(data["end"])


def validate_server(server_id: str, data: Dict[str, Any], port_min: int, port_max: int) -> None:
    required = {"folder", "start_command", "port"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Server {server_id} missing required keys: {', '.join(missing)}")

    port = data["port"]
    if not isinstance(port, int) or not (port_min <= port <= port_max):
        raise ValueError(
            f"Server {server_id} has invalid port {port} "
            f"(must be integer in range {port_min}-{port_max})"
        )

    folder = data["folder"]
    if not isinstance(folder, str) or ".." in folder or "/" in folder:
        raise ValueError(f"Server {server_id} has unsafe folder name: {folder}")


# ------------------------------------------------------------------------------
# Server provisioning helpers
# ------------------------------------------------------------------------------

def update_server_properties(folder_path: pathlib.Path, port: int, read_only: bool) -> None:
    """Update or create server.properties, setting server-port and query.port."""
    props_path = folder_path / "server.properties"
    props: Dict[str, str] = {}

    if props_path.exists():
        with open(props_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        props[k.strip()] = v.strip()

    props["server-port"] = str(port)
    props["query.port"] = str(port)

    content = f"# Updated by provision_servers.py on {datetime.now():%Y-%m-%d %H:%M:%S}\n"
    for key in sorted(props):
        content += f"{key}={props[key]}\n"
    content += "\n"

    guarded_write_text(props_path, content, read_only=read_only)


def ensure_eula_accepted(folder_path: pathlib.Path, server_id: str, read_only: bool) -> None:
    """Create eula.txt with eula=true if missing; warn if present but not accepted."""
    eula_path = folder_path / "eula.txt"

    if not eula_path.exists():
        content = (
            "# Generated by provision_servers.py\n"
            "# Please read and accept the Minecraft EULA: https://aka.ms/MinecraftEULA\n"
            "eula=true\n"
        )
        guarded_write_text(eula_path, content, mode=0o644, read_only=read_only)
        if not read_only:
            logger.info("Created eula.txt with eula=true for server %s", server_id)
        return

    # Exists → check content (do not overwrite)
    with open(eula_path, "r", encoding="utf-8") as f:
        text = f.read().lower()
    if "eula=true" not in text:
        logger.warning(
            "EULA not accepted for %s: %s exists but does not contain 'eula=true'. "
            "Start wrapper will block until fixed.",
            server_id, eula_path
        )
    else:
        logger.debug("EULA already accepted for %s", server_id)


def generate_start_script(folder_path: pathlib.Path, server_id: str, start_cmd: str, read_only: bool) -> None:
    """Generate start-minecraft.sh with substitutions."""
    content = f"""#!/usr/bin/env bash
# start-minecraft.sh
# Auto-generated by provision_servers.py for server '{server_id}'
# Placed in {folder_path}

set -euo pipefail

# Pre-launch validation
if [ ! -f server.properties ]; then
    echo "Error: server.properties not found in $(pwd). Aborting." >&2
    exit 1
fi

if [ ! -f eula.txt ] || ! grep -iq '^eula=true' eula.txt; then
    echo "Error: EULA not accepted in $(pwd)/eula.txt" >&2
    echo "Please read https://aka.ms/MinecraftEULA, then:" >&2
    echo "  1. cd $(pwd)" >&2
    echo "  2. Set 'eula=true' in eula.txt" >&2
    echo "  3. sudo systemctl restart minecraft-{server_id}" >&2
    exit 1
fi

# Logging
LOG_FILE="console_$(date +"%Y-%m-%d_%H-%M-%S").log"
echo "Launching server. Console log will be written to: $LOG_FILE"

# Screen flags
if [ -t 0 ]; then
    SCREEN_FLAGS="-dmS"
else
    SCREEN_FLAGS="-DmS"
fi

# Launch
SESSION_NAME="minecraft-{server_id}"

/usr/bin/screen ${{SCREEN_FLAGS}} "${{SESSION_NAME}}" \\
    -L -Logfile "${{LOG_FILE}}" \\
    {start_cmd}

echo "Server started in screen session: ${{SESSION_NAME}}"
echo "To reattach: screen -r ${{SESSION_NAME}}"
"""

    script_path = folder_path / "start-minecraft.sh"
    guarded_write_text(script_path, content, mode=0o755, read_only=read_only)
    if not read_only:
        guarded_chmod_chown(script_path, 0o755, read_only=read_only)


def generate_stop_script(folder_path: pathlib.Path, server_id: str, read_only: bool) -> None:
    """Generate stop-minecraft.sh with session name substitution."""
    content = f"""#!/usr/bin/env bash
# stop-minecraft.sh
# Auto-generated by provision_servers.py for server '{server_id}'

set -euo pipefail

SESSION_NAME="minecraft-{server_id}"

if ! screen -list | grep -q "${{SESSION_NAME}}"; then
    echo "No screen session '${{SESSION_NAME}}' found. Server may already be stopped."
    exit 0
fi

echo "Initiating graceful shutdown of ${{SESSION_NAME}}..."

/usr/bin/screen -S "${{SESSION_NAME}}" -p 0 -X stuff "/say Server is restarting in 20 seconds (systemd shutdown).^M"
/bin/sleep 10

/usr/bin/screen -S "${{SESSION_NAME}}" -p 0 -X stuff "/say Server is restarting in 10 seconds.^M"
/bin/sleep 10

/usr/bin/screen -S "${{SESSION_NAME}}" -p 0 -X stuff "/stop^M"

echo "Stop command sent. Waiting for server to exit..."
"""

    script_path = folder_path / "stop-minecraft.sh"
    guarded_write_text(script_path, content, mode=0o755, read_only=read_only)
    if not read_only:
        guarded_chmod_chown(script_path, 0o755, read_only=read_only)


def generate_systemd_unit(server_id: str, data: Dict[str, Any], read_only: bool) -> None:
    """Generate systemd service file."""
    friendly = data.get("friendly_name", server_id)
    folder = data["folder"]
    content = f"""[Unit]
Description=Minecraft Server - {friendly} ({server_id})
After=network.target

[Service]
User={EC2_USER}
WorkingDirectory={MINECRAFT_ROOT}/{folder}
ExecStart={MINECRAFT_ROOT}/{folder}/start-minecraft.sh
ExecStop={MINECRAFT_ROOT}/{folder}/stop-minecraft.sh
TimeoutStopSec=90
KillSignal=SIGTERM
KillMode=process
Restart=on-failure

[Install]
WantedBy=multi-user.target
"""

    unit_path = pathlib.Path(f"/etc/systemd/system/minecraft-{server_id}.service")
    guarded_write_text(unit_path, content, read_only=read_only)


def cleanup_stale_services(current_provisioned: set[str], read_only: bool) -> None:
    """Disable and remove units for servers no longer provisioned."""
    unit_dir = pathlib.Path("/etc/systemd/system")
    for unit_file in unit_dir.glob("minecraft-*.service"):
        stem = unit_file.stem
        if not stem.startswith("minecraft-"):
            continue
        srv_id = stem[len("minecraft-"):]
        if srv_id not in current_provisioned:
            logger.info("Found stale service: %s → cleaning up", unit_file)
            if read_only:
                logger.info("[READ-ONLY] Would disable --now and remove %s", unit_file)
                continue
            run_cmd(["systemctl", "disable", "--now", stem], check=False)
            try:
                unit_file.unlink()
                logger.info("Removed stale unit %s", unit_file)
            except Exception as exc:
                logger.warning("Failed to remove %s: %s", unit_file, exc)


def provision_server(server_id: str, data: Dict[str, Any], read_only: bool) -> None:
    """Provision one server: folder, properties, eula, scripts, unit."""
    folder_name = data["folder"]
    port = data["port"]
    start_cmd = data["start_command"]

    folder_path = pathlib.Path(MINECRAFT_ROOT) / folder_name

    if read_only:
        logger.info("[READ-ONLY] Would provision server %s in %s (port %d)", server_id, folder_path, port)
    else:
        logger.info("Provisioning server %s in %s (port %d)", server_id, folder_path, port)

    guarded_mkdir(folder_path, read_only)

    update_server_properties(folder_path, port, read_only)
    ensure_eula_accepted(folder_path, server_id, read_only)

    generate_start_script(folder_path, server_id, start_cmd, read_only)
    generate_stop_script(folder_path, server_id, read_only)
    generate_systemd_unit(server_id, data, read_only)


def main() -> int:
    parser = argparse.ArgumentParser(description="Provision Minecraft server services")
    parser.add_argument("--update", action="store_true", help="Pull latest config from repo first")
    parser.add_argument("--read-only", action="store_true",
                        help="Validate and log without writing files or changing system state")
    args = parser.parse_args()

    # Fail fast if not root
    if os.geteuid() != 0:
        logger.error("This script must run as root (required for systemd unit management, file ownership, etc.)")
        logger.error("Try: sudo python3 provision_servers.py [options]")
        return 1
    logger.info("Running as root (uid=%d) - proceeding", os.geteuid())

    try:
        # Early mount check for persistent volume
        if not pathlib.Path(PERSIST_ROOT).is_mount():
            logger.error("Persistent volume does not appear to be mounted at %s", PERSIST_ROOT)
            logger.error("Check 'mount | grep persist' — cannot proceed safely.")
            return 1
        logger.debug("Persistent volume is mounted at %s", PERSIST_ROOT)

        if args.update:
            ensure_config_repo(args.read_only)

        config = load_config()
        port_min, port_max = load_port_range()

        provisioned = set(config["provisioned"])
        seen_ports: set[int] = set()

        for server_id in provisioned:
            if server_id not in config["servers"]:
                logger.error("Provisioned server %s not defined in 'servers'", server_id)
                continue

            data = config["servers"][server_id]
            try:
                validate_server(server_id, data, port_min, port_max)
            except ValueError as exc:
                logger.error("%s — skipping %s", exc, server_id)
                continue

            port = data["port"]
            if port in seen_ports:
                logger.error("Duplicate port %d used by %s — skipping", port, server_id)
                continue
            seen_ports.add(port)

            provision_server(server_id, data, args.read_only)

        if not args.read_only:
            cleanup_stale_services(provisioned, args.read_only)
            guarded_systemctl(["daemon-reload"], args.read_only)

        logger.info("Provisioning run complete.")
        return 0

    except Exception:
        logger.exception("Fatal error during provisioning")
        return 1


if __name__ == "__main__":
    sys.exit(main())
