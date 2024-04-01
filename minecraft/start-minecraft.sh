#!/bin/bash

if [ ! -e server.properties ]; then
  echo "server.properties not found, aborting."
  exit 1
fi

# Flag indicating if the installed version of screen is < 4.06
# that does not support the -Logfile argument.
# Set legacyScreen=1 for true, and legacyScreen=0 for false.
legacyScreen=1

logFile="console_$(date +"%Y-%m-%d_%H-%M-%S").log"
# TODO:
#   - investigate how closely this mirrors the minecraft logs in /logs
#   - verify the stop workflow vs instance shutdown - does the minecraft server stop properly?

echo "logfile is $logFile"

if [ "$legacyScreen" -eq 1 ]; then
  cat << EOF >/tmp/screenrc.$$
logfile $logFile
EOF
  /usr/bin/screen -DmS minecraft -L -c /tmp/screenrc.$$ java -Xmx4092M -Xms4092M -jar minecraft_server.jar
  rm /tmp/screenrc.$$
else
  /usr/bin/screen -DmS minecraft -L -Logfile "$logFile" java -Xmx4092M -Xms4092M -jar minecraft_server.jar
fi
