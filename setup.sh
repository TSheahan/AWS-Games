#!/bin/env bash

cat <<'EOF'
======================================================================
This script sets up a Minecraft server with the following configurations:
- Server Folder: The directory where the server files are stored.
- Server Version: The version of the Minecraft server to be deployed.
  - used to compose jar filename upon download
- JAR URL: The URL from which the Minecraft server JAR file is downloaded.
- Java Package: the package to install for java on the server

Usage:
setup.sh --server-folder=<path> --server-version=<version> --jar-url=<url> --java-package=<package>

The script is designed to be invoked as root via the UserData script of a
linux game server instance (defined in a CloudFormation template).

It expects to be executed from the repository root folder.

The SetupCommand parameter must include the needed arguments.

======================================================================
EOF

if [ ! -d ./minecraft ]; then
  echo "setup must run from repository root (missing minecraft subdirectory)"
  exit 1
fi

# Initialize variables to hold the values of the arguments
serverFolder=""
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
  echo "Usage: $0 --server-folder=<path> --server-version=<version> --jar-url=<url> --java-package=<package>" >&2
  exit 1
}

# Loop through arguments and process them
for arg in "$@"
do
    case $arg in
        --server-folder=*)
        serverFolder="${arg#*=}"
        ;;
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
if [ -z "$serverFolder" ]; then
    error "server-folder argument is required"
fi
if [ -z "$serverVersion" ]; then
    error "server-version argument is required"
fi
if [ -z "$jarUrl" ]; then
    error "jar-url argument is required"
fi
if [ -z "$javaPackage" ]; then
    error "java-package argument is required"
fi

# If all arguments are provided, proceed with the rest of the script
echo "Server Folder: $serverFolder"
echo "Server Version: $serverVersion"
echo "JAR URL: $jarUrl"
echo "Java package: $javaPackage"

echo "!! Install JDK"
yum update -y
yum install -y "$javaPackage"

echo "!! Check Java version"
java -version

echo "!! Ensure /mnt/persist/minecraft exists and is owned by ec2-user"
mkdir -p /mnt/persist/minecraft
chown ec2-user:ec2-user /mnt/persist/minecraft

echo "!! Ensure the server folder exists and is owned by ec2-user"
mkdir -p "/mnt/persist/minecraft/${serverFolder}"
chown ec2-user:ec2-user "/mnt/persist/minecraft/${serverFolder}"

echo "!! Write /etc/systemd/system/minecraft-server.service"
cat << EOF > /etc/systemd/system/minecraft-server.service
[Unit]
Description=Minecraft Server
After=network.target

[Service]
User=ec2-user
WorkingDirectory=/mnt/persist/minecraft/${serverFolder}
ExecStart=/mnt/persist/minecraft/${serverFolder}/start-minecraft.sh
ExecStop=/mnt/persist/minecraft/${serverFolder}/stop-minecraft.sh
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF

startScriptPath="/mnt/persist/minecraft/${serverFolder}/start-minecraft.sh"
echo "!! Install the start-minecraft.sh wrapper script at $startScriptPath"
cp minecraft/start-minecraft.sh "$startScriptPath"
# Make the script executable
chmod +x "$startScriptPath"
# Ensure the script is owned by ec2-user
chown ec2-user:ec2-user "$startScriptPath"

stopScriptPath="/mnt/persist/minecraft/${serverFolder}/stop-minecraft.sh"
echo "!! Install the stop-minecraft.sh wrapper script at $stopScriptPath"
cp minecraft/stop-minecraft.sh "$stopScriptPath"
# Make the script executable
chmod +x "$stopScriptPath"
# Ensure the script is owned by ec2-user
chown ec2-user:ec2-user "$stopScriptPath"

jarPath="/home/ec2-user/minecraft_server_${serverVersion}.jar"
echo "!! Download Minecraft server JAR to $jarPath"
sudo -u ec2-user wget -O "$jarPath" "$jarUrl"

symlinkPath="/mnt/persist/minecraft/${serverFolder}/minecraft_server.jar"
echo "!! Symlink the jar to $symlinkPath"
sudo -u ec2-user ln -s "$jarPath" "$symlinkPath"

echo "!! reload systemd"
# Reload systemd to recognize the new service and enable it to start on boot
systemctl daemon-reload
systemctl enable minecraft-server.service

# echo "!! start minecraft-server"
# systemctl start minecraft-server.service
# ? consider rebooting here..