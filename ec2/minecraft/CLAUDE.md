# ec2/minecraft/ — EC2-Side Minecraft Scripts

Scripts in this directory run on the **EC2 instance**. `ec2/minecraft/setup.sh` is invoked by UserData during first-boot provisioning. The start/stop wrappers are copied by `setup.sh` into each server's working directory under `/mnt/persist/minecraft/<folder>/`.

**Target environment:** Amazon Linux 2023, ARM64 (Graviton). Do not introduce x86-specific packages, paths, or assumptions.

---

## `start-minecraft.sh`

Launches the Minecraft JVM in a detached screen session.

Pre-launch validation (script exits non-zero if either check fails):
- `server.properties` must exist in the working directory
- `eula.txt` must contain `eula=true` (EULA must be explicitly accepted)

Launch details:
- Screen session name: `minecraft`
- Command: `screen -DmS minecraft java ...`
- JVM flags: `-Xmx4092M -Xms4092M -Djava.net.preferIPv4Stack=true`
- Console output redirected to `console_YYYY-MM-DD_HH-MM-SS.log` in the server folder
- JAR name: `minecraft_server.jar` (symlink created by `setup.sh`)

---

## `stop-minecraft.sh`

Graceful shutdown via screen session.

Procedure:
1. Sends `/say systemd is shutting down this service.` as an in-game broadcast
2. Waits 5 seconds
3. Sends `/stop`

The systemd unit allows 120 seconds for the server to finish writing chunks and exit. The screen session terminates when the JVM exits.

---

## Systemd integration

These scripts are called by the `minecraft-server.service` unit (created by `setup.sh`). In the multi-server case, `provision_servers.py` generates per-server variants of these scripts with server-specific values substituted.

Attach to a running console: `screen -r minecraft`
Detach without stopping: `Ctrl+A, D`

---

## `provision_servers.py`

Multi-server systemd provisioner.

**Runs on:** EC2 instance, as root
**Config source:** `https://github.com/TSheahan/AWS-Games-Config.git`
**Port bounds source:** `/home/ec2-user/game-ports.json` (written by CloudFormation UserData)
**Log file:** `/var/log/minecraft-provision.log` (world-readable)

Modes (use together or separately):
- `--update` — clone or pull the config repo
- `--read-only` — validate config without writing any files
- `--provision` — full write: systemd units, start/stop scripts, server.properties, enabled/disabled state

Generates per-server files under `/mnt/persist/minecraft/<folder>/` and systemd units under `/etc/systemd/system/minecraft-<server_id>.service`. Cleans up stale units for servers removed from config.

---

## `mcstatus.sh`

Quick status display for all `minecraft-*.service` units. Runs on EC2 as any user.
