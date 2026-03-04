#!/usr/bin/env bash

if [ ! -e server.properties ]; then
  echo "server.properties not found, aborting."
  exit 1
fi

# Check EULA acceptance to prevent server from failing to start properly
if [ ! -e eula.txt ] || ! grep -iq '^eula=true' eula.txt; then
  echo "EULA not accepted: eula.txt is missing or does not contain 'eula=true'."
  echo "Please SSH into the instance, edit eula.txt in the server directory,"
  echo "set 'eula=true' (after reading https://aka.ms/MinecraftEULA), and save."
  echo "Then restart the minecraft-server service."
  exit 1
fi

# numeric boolean flag to work around screen < 4.06 lacking -Logfile argument
# legacyScreen=0  # (handler omitted in this commit)

logFile="console_$(date +"%Y-%m-%d_%H-%M-%S").log"

echo "logfile is $logFile"

/usr/bin/screen -DmS minecraft -L -Logfile "$logFile" java -Xmx4092M -Xms4092M -Djava.net.preferIPv4Stack=true -jar minecraft_server.jar nogui
