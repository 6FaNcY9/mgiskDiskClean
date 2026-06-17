#!/usr/bin/env bash
# dev/droplet/status.sh — show mrija-client service + API status on DO droplet
set -euo pipefail

DROPLET="root@104.248.242.243"
SSH_KEY="$HOME/.ssh/digitalOcean"
API_URL="http://104.248.242.243:8080"
ENV_FILE="/opt/mrija/mrija.env"

KEY=$(SSH_AUTH_SOCK="" ssh -i "$SSH_KEY" "$DROPLET" \
    "grep MRIJA_API_KEY $ENV_FILE | cut -d= -f2" 2>/dev/null)

echo "=== systemd ==="
SSH_AUTH_SOCK="" ssh -i "$SSH_KEY" "$DROPLET" \
    "systemctl status mrija-client --no-pager | head -8"

echo ""
echo "=== API /api/status ==="
curl -sf -H "X-Api-Key: $KEY" "$API_URL/api/status" | python3 -m json.tool \
    || echo "  (unreachable or no DB loaded)"

echo ""
echo "=== updates dir ==="
SSH_AUTH_SOCK="" ssh -i "$SSH_KEY" "$DROPLET" \
    "ls -lh /var/www/mrija/updates/ 2>/dev/null || echo '  (empty)'"
