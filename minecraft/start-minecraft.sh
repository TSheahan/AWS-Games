#!/usr/bin/env bash

if [ ! -e server.properties ]; then
  echo "server.properties not found, aborting."
  exit 1
fi

# numeric boolean flag to work around screen < 4.06 lacking -Logfile argument
# legacyScreen=0  # (handler omitted in this commit)

logFile="console_$(date +"%Y-%m-%d_%H-%M-%S").log"

echo "logfile is $logFile"

/usr/bin/screen -DmS minecraft -L -Logfile "$logFile" java -Xmx4092M -Xms4092M -Djava.net.preferIPv4Stack=true -jar minecraft_server.jar
