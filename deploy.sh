#!/usr/bin/env bash
# build and (re)start inkpad on alexandria. restarting drops connected devices for a
# few seconds (they reconnect on their own), so don't run it mid-lesson.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
host=${INKPAD_HOST:-alexandria}
ssh "$host" 'mkdir -p ~/inkpad/data'
scp -q "$here"/{server.py,index.html,Dockerfile} "$host":inkpad/
ssh "$host" 'cd ~/inkpad && docker build -q -t inkpad . >/dev/null \
  && docker rm -f inkpad >/dev/null 2>&1; docker run -d --name inkpad --restart unless-stopped \
     -p 5155:8765 -v ~/inkpad/data:/data inkpad >/dev/null \
  && docker image prune -f >/dev/null && echo "inkpad up: http://alexandria.local/inkpad/"'
