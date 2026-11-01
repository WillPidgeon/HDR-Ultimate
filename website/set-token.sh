#!/bin/bash
# Reads a Cloudflare tunnel token on stdin, saves it in .env and restarts the tunnel.
# The token never appears on a command line. Pasting the whole "docker run ... --token ..." command also works.
#   Usage, on the Docker host:  bash set-token.sh   (then paste the token and press Enter)
set -e
cd "$(dirname "$0")"
IFS= read -r input
token=$(printf '%s' "$input" | grep -oE 'eyJ[A-Za-z0-9+/=_-]+' | head -1 || true)
[ -n "$token" ] || { echo "That doesn't look like a tunnel token (it starts with eyJ). Nothing was changed."; exit 1; }
T="$token" awk 'BEGIN { t = ENVIRON["T"] }
  /^TUNNEL_TOKEN=/ { print "TUNNEL_TOKEN=" t; done = 1; next }
  { print }
  END { if (!done) print "TUNNEL_TOKEN=" t }' .env > .env.new
chmod 600 .env.new
mv .env.new .env
echo "Saved the new token (${#token} characters). Restarting the tunnel..."
docker compose up -d tunnel
