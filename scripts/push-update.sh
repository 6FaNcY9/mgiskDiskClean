#!/usr/bin/env bash
# push-update.sh — Dump the local MariaDB archive + attachments and push to the DO relay.
#
# Usage:
#   ./scripts/push-update.sh
#   ./scripts/push-update.sh --check
#   ./scripts/push-update.sh --dry-run
#   KEEP_UPDATE_ARTIFACTS=1 ./scripts/push-update.sh --dry-run
#   ATTACH_ARCHIVE_PATH=data/update-cache/mrija-attachments-<ts>.tar.zst ./scripts/push-update.sh
#
# Requires:
#   - Docker Compose stack running locally when DUMP_VIA=compose
#   - Local devenv MariaDB running (`db-start`) when DUMP_VIA=devenv
#   - DO_RELAY_HOST set in environment or .env.push (e.g. "root@<ip>")
#   - ~/.ssh/do_mrija SSH key for the droplet
#   - rsync / ssh on PATH
#   - zstd for lossless attachment archive compression

set -euo pipefail

MODE="publish"
case "${1:-}" in
    --check) MODE="check" ;;
    --dry-run) MODE="dry-run" ;;
    -h|--help)
        sed -n '1,20p' "$0"
        exit 0
        ;;
    "")
        ;;
    *)
        echo "ERROR: unknown argument: $1" >&2
        exit 1
        ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load .env.push if present (keys: DO_RELAY_HOST, DO_SSH_KEY)
if [[ -f "$REPO_ROOT/.env.push" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$REPO_ROOT/.env.push"; set +a
fi

DO_RELAY_HOST="${DO_RELAY_HOST:-}"
DO_SSH_KEY="${DO_SSH_KEY:-$HOME/.ssh/do_mrija}"
KEEP_DUMPS="${KEEP_DUMPS:-3}"
PUSH_ATTACHMENTS="${PUSH_ATTACHMENTS:-1}"
ZSTD_LEVEL="${ZSTD_LEVEL:-19}"
KEEP_UPDATE_ARTIFACTS="${KEEP_UPDATE_ARTIFACTS:-0}"
ATTACH_CACHE_DIR="${ATTACH_CACHE_DIR:-$REPO_ROOT/data/update-cache}"
ATTACH_ARCHIVE_PATH="${ATTACH_ARCHIVE_PATH:-}"

if [[ -z "$DO_RELAY_HOST" ]]; then
    echo "ERROR: DO_RELAY_HOST is not set. Export it or add to .env.push." >&2
    exit 1
fi

# Load DB creds from .env
if [[ -f "$REPO_ROOT/.env" ]]; then
    # shellcheck disable=SC1090
    set -a; source "$REPO_ROOT/.env"; set +a
fi

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-3306}"
DB_NAME="${MRIJA_DB_NAME:-mailreview}"
DB_USER="${MRIJA_DB_USER:-mailreview}"
DB_PASS="${MRIJA_DB_PASSWORD:-}"
DUMP_VIA="${DUMP_VIA:-compose}"
DB_SOCKET="${DB_SOCKET:-${MYSQL_UNIX_PORT:-${DEVENV_STATE:-}/mysql.sock}}"

check_requirements() {
    local missing=0
    for cmd in gzip sha256sum rsync ssh du; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            echo "ERROR: missing command: $cmd" >&2
            missing=1
        fi
    done
    if [[ "$PUSH_ATTACHMENTS" == "1" ]]; then
        for cmd in tar zstd mktemp; do
            if ! command -v "$cmd" >/dev/null 2>&1; then
                echo "ERROR: missing command: $cmd" >&2
                missing=1
            fi
        done
        if [[ ! -f "$REPO_ROOT/data/mailboxes.txt" ]]; then
            echo "ERROR: data/mailboxes.txt not found; cannot choose attachment mailboxes" >&2
            missing=1
        fi
    fi
    if [[ "$DUMP_VIA" == "devenv" ]]; then
        for cmd in mariadb mariadb-dump; do
            if ! command -v "$cmd" >/dev/null 2>&1; then
                echo "ERROR: missing command: $cmd (run through: devenv shell -- ./scripts/push-update.sh)" >&2
                missing=1
            fi
        done
        if [[ -z "${DEVENV_STATE:-}" && -z "${MYSQL_UNIX_PORT:-}" && -z "${DB_SOCKET:-}" ]]; then
            echo "ERROR: DB socket unknown. Run through: devenv shell -- ./scripts/push-update.sh" >&2
            missing=1
        elif [[ ! -S "$DB_SOCKET" ]]; then
            echo "ERROR: MariaDB socket not found: $DB_SOCKET" >&2
            echo "       Run: devenv shell -- db-start" >&2
            missing=1
        elif ! mariadb --user="$DB_USER" --socket="$DB_SOCKET" "$DB_NAME" -e "SELECT 1" >/dev/null 2>&1; then
            echo "ERROR: cannot connect to $DB_NAME through socket: $DB_SOCKET" >&2
            echo "       Run: devenv shell -- db-start" >&2
            missing=1
        fi
    elif [[ "$DUMP_VIA" == "compose" ]]; then
        if ! command -v docker >/dev/null 2>&1; then
            echo "ERROR: missing command: docker" >&2
            missing=1
        fi
    elif [[ "$DUMP_VIA" == "tcp" ]]; then
        if ! command -v mysqldump >/dev/null 2>&1; then
            echo "ERROR: missing command: mysqldump" >&2
            missing=1
        fi
    else
        echo "ERROR: unsupported DUMP_VIA=$DUMP_VIA (use devenv, compose, or tcp)" >&2
        missing=1
    fi
    if [[ ! -f "$DO_SSH_KEY" ]]; then
        echo "ERROR: SSH key not found: $DO_SSH_KEY" >&2
        missing=1
    fi
    if [[ -z "$DB_PASS" ]]; then
        echo "WARN: MRIJA_DB_PASSWORD is empty; this is only valid for socket/auth-local setups." >&2
    fi
    if [[ "$missing" -ne 0 ]]; then
        exit 1
    fi
    echo "OK: requirements present"
    echo "    relay: $DO_RELAY_HOST"
    echo "    ssh key: $DO_SSH_KEY"
    echo "    database: $DB_NAME via $DUMP_VIA as $DB_USER"
    if [[ "$DUMP_VIA" == "devenv" ]]; then
        echo "    socket: $DB_SOCKET"
    fi
    if [[ "$PUSH_ATTACHMENTS" == "1" ]]; then
        echo "    attachments: enabled (tar.zst, zstd level $ZSTD_LEVEL)"
        if [[ -n "$ATTACH_ARCHIVE_PATH" ]]; then
            echo "    attachment archive: reuse $ATTACH_ARCHIVE_PATH"
        elif [[ "$KEEP_UPDATE_ARTIFACTS" == "1" ]]; then
            echo "    artifact cache: $ATTACH_CACHE_DIR"
        fi
    else
        echo "    attachments: disabled"
    fi
}

if [[ "$MODE" == "check" ]]; then
    check_requirements
    exit 0
fi

check_requirements

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_NAME="mrija-${TIMESTAMP}.sql.gz"
TMP_DUMP="/tmp/${DUMP_NAME}"
ATTACH_NAME="mrija-attachments-${TIMESTAMP}.tar.zst"
TMP_ATTACH="/tmp/${ATTACH_NAME}"
TMP_ATTACH_LIST="$(mktemp)"
ATTACH_SIZE=""
ATTACH_SHA256=""
ATTACH_KEEP=0

cleanup() {
    rm -f "$TMP_DUMP" "$TMP_ATTACH_LIST"
    if [[ "$ATTACH_KEEP" != "1" ]]; then
        rm -f "$TMP_ATTACH"
    fi
}
trap cleanup EXIT

echo "==> Dumping ${DB_NAME} via ${DUMP_VIA}…"
if [[ "$DUMP_VIA" == "devenv" ]]; then
    mariadb-dump \
        --user="$DB_USER" \
        --socket="$DB_SOCKET" \
        --single-transaction \
        --skip-lock-tables \
        --no-tablespaces \
        "$DB_NAME" \
      | gzip -9 > "$TMP_DUMP"
elif [[ "$DUMP_VIA" == "compose" ]]; then
    docker compose exec -T db mariadb-dump \
        --user="$DB_USER" \
        --password="$DB_PASS" \
        --single-transaction \
        --skip-lock-tables \
        --no-tablespaces \
        "$DB_NAME" \
      | gzip -9 > "$TMP_DUMP"
else
    mysqldump \
        --host="$DB_HOST" \
        --port="$DB_PORT" \
        --user="$DB_USER" \
        --password="$DB_PASS" \
        --single-transaction \
        --skip-lock-tables \
        --no-tablespaces \
        "$DB_NAME" \
      | gzip -9 > "$TMP_DUMP"
fi

DUMP_SIZE="$(du -sh "$TMP_DUMP" | cut -f1)"
SHA256="$(sha256sum "$TMP_DUMP" | awk '{print $1}')"
echo "    Size: $DUMP_SIZE  SHA-256: $SHA256"

if [[ "$PUSH_ATTACHMENTS" == "1" ]]; then
    if [[ -n "$ATTACH_ARCHIVE_PATH" ]]; then
        if [[ ! -f "$ATTACH_ARCHIVE_PATH" ]]; then
            echo "ERROR: ATTACH_ARCHIVE_PATH not found: $ATTACH_ARCHIVE_PATH" >&2
            exit 1
        fi
        ATTACH_NAME="$(basename "$ATTACH_ARCHIVE_PATH")"
        if ! printf '%s' "$ATTACH_NAME" | grep -qE '^mrija-attachments-[0-9T]+Z\.tar\.zst$'; then
            echo "ERROR: reused attachment archive has invalid filename: $ATTACH_NAME" >&2
            exit 1
        fi
        TMP_ATTACH="$ATTACH_ARCHIVE_PATH"
        ATTACH_KEEP=1
        echo "==> Reusing attachment archive: $TMP_ATTACH"
    else
        if [[ "$KEEP_UPDATE_ARTIFACTS" == "1" ]]; then
            mkdir -p "$ATTACH_CACHE_DIR"
            TMP_ATTACH="$ATTACH_CACHE_DIR/$ATTACH_NAME"
            ATTACH_KEEP=1
        fi
        echo "==> Packaging attachments losslessly with zstd level ${ZSTD_LEVEL}…"
        : > "$TMP_ATTACH_LIST"
        while IFS= read -r mailbox || [[ -n "$mailbox" ]]; do
            mailbox="$(printf '%s' "$mailbox" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
            case "$mailbox" in
                ""|"#"*) continue ;;
            esac
            if ! printf '%s' "$mailbox" | grep -qE '^[a-zA-Z0-9._-]+$'; then
                echo "ERROR: invalid mailbox in data/mailboxes.txt: $mailbox" >&2
                exit 1
            fi
            if [[ -d "$REPO_ROOT/data/mailboxes/$mailbox/attachments" ]]; then
                printf 'mailboxes/%s/attachments\n' "$mailbox" >> "$TMP_ATTACH_LIST"
            fi
        done < "$REPO_ROOT/data/mailboxes.txt"
        if [[ ! -s "$TMP_ATTACH_LIST" ]]; then
            echo "ERROR: no attachment directories found for data/mailboxes.txt" >&2
            exit 1
        fi
        tar -C "$REPO_ROOT/data" -cf - -T "$TMP_ATTACH_LIST" \
          | zstd -T0 "-${ZSTD_LEVEL}" -q -o "$TMP_ATTACH"
    fi
    ATTACH_SIZE="$(du -sh "$TMP_ATTACH" | cut -f1)"
    ATTACH_SHA256="$(sha256sum "$TMP_ATTACH" | awk '{print $1}')"
    echo "    Attachments: $ATTACH_SIZE  SHA-256: $ATTACH_SHA256"
    if [[ "$ATTACH_KEEP" == "1" ]]; then
        echo "    Kept attachment archive: $TMP_ATTACH"
    fi
fi

if [[ "$MODE" == "dry-run" ]]; then
    echo "==> Dry run: dump created and verified locally, not uploading."
    echo "    Would upload: ${DUMP_NAME}"
    if [[ "$PUSH_ATTACHMENTS" == "1" ]]; then
        echo "    Would upload: ${ATTACH_NAME}"
    fi
    exit 0
fi

echo "==> Uploading to ${DO_RELAY_HOST}…"
rsync -az --partial --append-verify -e "ssh -i $DO_SSH_KEY" \
    "$TMP_DUMP" "${DO_RELAY_HOST}:/var/www/mrija/updates/${DUMP_NAME}.part"
ssh -i "$DO_SSH_KEY" "$DO_RELAY_HOST" \
    "mv /var/www/mrija/updates/${DUMP_NAME}.part /var/www/mrija/updates/${DUMP_NAME}"
if [[ "$PUSH_ATTACHMENTS" == "1" ]]; then
    rsync -az --partial --append-verify --info=progress2 -e "ssh -i $DO_SSH_KEY" \
        "$TMP_ATTACH" "${DO_RELAY_HOST}:/var/www/mrija/updates/${ATTACH_NAME}.part"
    ssh -i "$DO_SSH_KEY" "$DO_RELAY_HOST" \
        "mv /var/www/mrija/updates/${ATTACH_NAME}.part /var/www/mrija/updates/${ATTACH_NAME}"
fi

echo "==> Updating manifest and rotating old dumps…"
# shellcheck disable=SC2087
ssh -i "$DO_SSH_KEY" "$DO_RELAY_HOST" bash <<EOF
set -e
cd /var/www/mrija/updates

# Write fresh manifest.json
if [[ "${PUSH_ATTACHMENTS}" == "1" ]]; then
    cat > manifest.json <<JSON
{
  "version":    "${TIMESTAMP}",
  "filename":   "${DUMP_NAME}",
  "sha256":     "${SHA256}",
  "url":        "/updates/${DUMP_NAME}",
  "database": {
    "filename": "${DUMP_NAME}",
    "sha256":   "${SHA256}",
    "url":      "/updates/${DUMP_NAME}"
  },
  "attachments": {
    "filename": "${ATTACH_NAME}",
    "sha256":   "${ATTACH_SHA256}",
    "url":      "/updates/${ATTACH_NAME}",
    "format":   "tar.zst"
  },
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
else
    cat > manifest.json <<JSON
{
  "version":    "${TIMESTAMP}",
  "filename":   "${DUMP_NAME}",
  "sha256":     "${SHA256}",
  "url":        "/updates/${DUMP_NAME}",
  "database": {
    "filename": "${DUMP_NAME}",
    "sha256":   "${SHA256}",
    "url":      "/updates/${DUMP_NAME}"
  },
  "created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
JSON
fi

# Keep only the $KEEP_DUMPS newest dumps (by filename, which is date-sorted)
ls -1t mrija-*.sql.gz 2>/dev/null | tail -n +$((KEEP_DUMPS + 1)) | xargs -r rm -f
ls -1t mrija-attachments-*.tar.zst 2>/dev/null | tail -n +$((KEEP_DUMPS + 1)) | xargs -r rm -f

echo "Manifest updated. Current dumps:"
ls -lh mrija-*.sql.gz 2>/dev/null || echo "(none)"
ls -lh mrija-attachments-*.tar.zst 2>/dev/null || echo "(no attachments)"
EOF

echo "==> Done. Manifest at http://${DO_RELAY_HOST##*@}/updates/manifest.json"
