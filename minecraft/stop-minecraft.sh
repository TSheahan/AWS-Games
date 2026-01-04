#!/usr/bin/env bash
/usr/bin/screen -S minecraft -p 0 -X stuff "/say systemd is shutting down this service.^M"
sleep 5
/usr/bin/screen -S minecraft -p 0 -X stuff "/stop^M"
