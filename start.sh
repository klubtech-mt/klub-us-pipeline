#!/bin/sh
if [ "$SERVICE_ROLE" = "tracker" ]; then
  exec python3 20250528_us_tracker_server_v10.py
else
  exec tail -f /dev/null
fi
