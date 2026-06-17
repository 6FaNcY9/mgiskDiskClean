#!/usr/bin/env bash
# dev/droplet/deploy.sh — pull latest main on droplet and restart service
set -euo pipefail

DROPLET="root@104.248.242.243"
SSH_KEY="$HOME/.ssh/digitalOcean"

echo "=== pulling latest main on droplet ==="
SSH_AUTH_SOCK="" ssh -i "$SSH_KEY" "$DROPLET" "
    cd /opt/mrija/repo
    git fetch origin
    git checkout main
    git pull origin main
    echo 'pull done'
"

echo ""
echo "=== restarting mrija-client ==="
SSH_AUTH_SOCK="" ssh -i "$SSH_KEY" "$DROPLET" \
    "systemctl restart mrija-client && sleep 2 && systemctl status mrija-client --no-pager | head -6"

echo ""
echo "deploy complete"
