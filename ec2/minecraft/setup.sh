#!/bin/env bash

cat <<'EOF'
======================================================================
This script performs instance-level setup for a Minecraft server host:
- Java Package: the package to install for Java on the server
- Server Version: used to name the downloaded server JAR on disk
- JAR URL: the URL from which the Minecraft server JAR file is downloaded
  (best-effort; skipped if the file already exists on the persistent volume)

Usage:
./ec2/minecraft/setup.sh --server-version=<version> --jar-url=<url> --java-package=<package>

The script is designed to be invoked as root via the UserData script of a
linux game server instance (defined in a CloudFormation template).

It expects to be executed from the repository root folder.

Server-specific provisioning (folders, systemd units, start/stop scripts,
server.properties) is delegated to provision_servers.py, which this script
invokes after completing instance-level setup.

======================================================================
EOF

if [ ! -d ./ec2/minecraft ]; then
  echo "setup must run from repository root (missing ec2/minecraft subdirectory)"
  exit 1
fi

# Initialize variables to hold the values of the arguments
serverVersion=""
jarUrl=""
javaPackage=""

# javaPackage annotation:
# Current (as of January 2026): java-21-amazon-corretto-devel
#   - Recommended for Minecraft 1.21.x on Amazon Linux 2023.
#   - Provides full JDK (includes devel tools); harmless overhead in prod, ensures all components available.
#   - Corretto 21 is LTS with support until ~2030; matches Minecraft's Java 21 requirement.
#
# Future:
#   - Monitor Minecraft releases. The shift to year-based versioning (26.x in 2026) introduces Java 25 requirement
#     starting with version 26.1 (planned 2026).
#   - Consider switching to java-25-amazon-corretto-devel in H2 2026 or early 2027, once 26.x is stable and
#     performance benefits (e.g., improved GC, compact headers) are validated for your workload.
#   - Corretto 25 packages are available on AL2023; transition will be straightforward via yum.

# Error function to display an error message and exit
error() {
  echo "Error: $1" >&2
  echo "Usage: $0 --server-version=<version> --jar-url=<url> --java-package=<package>" >&2
  exit 1
}

# Loop through arguments and process them
for arg in "$@"
do
    case $arg in
        --server-version=*)
        serverVersion="${arg#*=}"
        ;;
        --jar-url=*)
        jarUrl="${arg#*=}"
        ;;
        --java-package=*)
        javaPackage="${arg#*=}"
        ;;
        *)
        # Unknown option
        error "Unknown argument ${arg}"
        ;;
    esac
done

# Check if any of the required arguments are missing
if [ -z "$serverVersion" ]; then
    error "server-version argument is required"
fi
if [ -z "$jarUrl" ]; then
    error "jar-url argument is required"
fi
if [ -z "$javaPackage" ]; then
    error "java-package argument is required"
fi

# Verify persistent mount is available before proceeding
if ! mountpoint -q /mnt/persist; then
    echo "Error: /mnt/persist is not mounted. Check UserData bootstrap logs (/var/log/cloud-init-output.log)." >&2
    echo "Persistent storage setup failed — cannot continue." >&2
    exit 1
fi
echo "/mnt/persist is properly mounted."

echo "Server Version: $serverVersion"
echo "JAR URL: $jarUrl"
echo "Java package: $javaPackage"

echo "!! Install JDK"
yum update -q -y
yum install -q -y "$javaPackage"

echo "!! Install utility packages"
yum install -q -y htop

echo "!! Check Java version"
java -version

echo "!! Ensure /mnt/persist/minecraft exists and is owned by ec2-user"
mkdir -p /mnt/persist/minecraft
chown ec2-user:ec2-user /mnt/persist/minecraft

jarPath="/mnt/persist/minecraft/server_${serverVersion}.jar"
echo "!! Download Minecraft server JAR to $jarPath (skipped if already present)"
if [ -f "$jarPath" ]; then
    echo "JAR already exists at $jarPath — skipping download."
else
    wget -q -O "$jarPath" "$jarUrl" || { echo "Error: JAR download failed." >&2; rm -f "$jarPath"; exit 1; }
fi
chown ec2-user:ec2-user "$jarPath"

echo "!! Provision servers via provision_servers.py"
python3 ec2/minecraft/provision_servers.py --update --provision

echo "!! Install minecraft admin wrapper"
mkdir -p /home/ec2-user/bin
cp ec2/minecraft/minecraft /home/ec2-user/bin/minecraft
chmod 0755 /home/ec2-user/bin/minecraft
chown ec2-user:ec2-user /home/ec2-user/bin/minecraft

echo "!! Install bash completion for minecraft command"
cp ec2/minecraft/minecraft-completion.bash /etc/bash_completion.d/minecraft
chmod 0644 /etc/bash_completion.d/minecraft

echo "!! Install minecraft-autoshutdown script"
cp ec2/minecraft/minecraft-autoshutdown /usr/local/bin/minecraft-autoshutdown
chmod 0755 /usr/local/bin/minecraft-autoshutdown

echo "!! Install minecraft-autoshutdown systemd units"
cp ec2/minecraft/minecraft-autoshutdown.timer /etc/systemd/system/minecraft-autoshutdown.timer
cp ec2/minecraft/minecraft-autoshutdown.service /etc/systemd/system/minecraft-autoshutdown.service
chmod 0644 /etc/systemd/system/minecraft-autoshutdown.timer
chmod 0644 /etc/systemd/system/minecraft-autoshutdown.service

echo "!! Enable and start minecraft-autoshutdown timer"
systemctl daemon-reload
systemctl enable --now minecraft-autoshutdown.timer

# echo "!! start minecraft-server"
# systemctl start minecraft-server.service
# ? consider rebooting here..
