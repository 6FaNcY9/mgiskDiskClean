{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:
{
  # ── Python pipeline ───────────────────────────────────────────────────────
  languages.python = {
    enable = true;
    venv = {
      enable = true;
      requirements = ''
        pytest>=8.0
        reportlab>=4.0
        imap-tools>=1.6
      '';
    };
  };

  # ── PHP 8.3 (frameworkless; no Composer required) ─────────────────────────
  languages.php = {
    enable = true;
    package = pkgs.php83;
  };

  # ── MariaDB (MySQL-compatible) for local dev ──────────────────────────────
  services.mysql = {
    enable = true;
    package = pkgs.mariadb;
    initialDatabases = [{ name = "mailreview"; }];
    ensureUsers = [
      {
        name = "mailreview";
        ensurePermissions = {
          "mailreview.*" = "ALL PRIVILEGES";
        };
      }
    ];
  };

  # ── Extra CLI tools ───────────────────────────────────────────────────────
  packages = with pkgs; [
    jq
    curl
    rsync
    tex-fmt
  ];

  # ── devenv scripts ────────────────────────────────────────────────────────
  scripts = {

    # ── db-start: start/verify the local MariaDB dev server ──────────────
    db-start.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: db-start"
        echo ""
        echo "  Start the local MariaDB dev server (managed by devenv services.mysql)."
        echo "  Creates the 'mailreview' schema and user if they do not yet exist."
        echo ""
        echo "Options:"
        echo "  --help    Show this help message and exit"
        exit 0
      fi
      echo "==> Starting MariaDB via devenv process manager..."
      devenv up &
      echo "==> Waiting for MariaDB socket..."
      for i in $(seq 1 30); do
        mysql -u mailreview --socket="$DEVENV_STATE/mysql.sock" -e "SELECT 1" mailreview >/dev/null 2>&1 && break
        sleep 1
      done
      mysql -u mailreview --socket="$DEVENV_STATE/mysql.sock" -e "SELECT VERSION();" mailreview \
        || { echo "ERROR: MariaDB not responding after 30s"; exit 1; }
      echo "==> MariaDB ready. DB: mailreview, user: mailreview (socket auth)"
    '';

    # ── db-migrate: run SQL migrations against configured MySQL ──────────
    db-migrate.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: db-migrate [--socket <path>]"
        echo ""
        echo "  Run pending SQL migrations in web/migrations/ against the"
        echo "  configured MariaDB instance."
        echo ""
        echo "Options:"
        echo "  --socket <path>   MariaDB socket path (default: \$DEVENV_STATE/mysql.sock)"
        echo "  --help            Show this help message and exit"
        echo ""
        echo "Environment:"
        echo "  DB_SOCKET   Override socket path (alternative to --socket)"
        exit 0
      fi
      SOCK="''${DB_SOCKET:-$DEVENV_STATE/mysql.sock}"
      if [ "$1" = "--socket" ] && [ -n "$2" ]; then
        SOCK="$2"
      fi
      php "$DEVENV_ROOT/web/src/cli/migrate.php" --socket "$SOCK"
    '';

    # ── index-mailbox: (re)build per-mailbox index DB ────────────────────
    index-mailbox.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: index-mailbox <mailbox>"
        echo ""
        echo "  (Re)build the per-mailbox SQLite index from stored maildir and"
        echo "  extracted attachments."
        echo ""
        echo "  Index written to:"
        echo "    \$DEVENV_ROOT/data/mailboxes/<mailbox>/index.sqlite"
        echo ""
        echo "  Requires: sync-all to have run first."
        echo ""
        echo "Options:"
        echo "  --help    Show this help message and exit"
        exit 0
      fi
      if [ -z "$1" ]; then
        echo "ERROR: mailbox name required"
        echo "Run: index-mailbox --help"
        exit 1
      fi
      MAILBOX="$1"
      DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
      echo "==> [index-mailbox] Indexing '$MAILBOX'..."
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
        --mailbox "$MAILBOX" \
        --data-root "$DATA_ROOT" \
        || { echo "ERROR: index-mailbox failed"; exit 1; }
      echo "==> Done. Index: $DATA_ROOT/index.sqlite"
    '';

    # ── index-all: (re)build global index across all mailboxes ───────────
    index-all.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: index-all"
        echo ""
        echo "  (Re)build the global SQLite index across all stored mailboxes."
        echo ""
        echo "  Global index written to:"
        echo "    \$DEVENV_ROOT/data/index/mail_index.sqlite"
        echo ""
        echo "Options:"
        echo "  --help    Show this help message and exit"
        exit 0
      fi
      GLOBAL_INDEX_DIR="$DEVENV_ROOT/data/index"
      mkdir -p "$GLOBAL_INDEX_DIR"
      echo "==> [index-all] Building global index..."
      for MAILBOX_DIR in "$DEVENV_ROOT/data/mailboxes"/*/; do
        MAILBOX="$(basename "$MAILBOX_DIR")"
        echo "    indexing: $MAILBOX"
        PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
          --mailbox "$MAILBOX" \
          --data-root "$MAILBOX_DIR" \
          --global-index "$GLOBAL_INDEX_DIR/mail_index.sqlite" 2>/dev/null \
          || echo "    WARNING: index failed for $MAILBOX (skipping)"
      done
      echo "==> Done. Global index: $GLOBAL_INDEX_DIR/mail_index.sqlite"
    '';

    # ── extract-attachments: extract MIME attachments from stored maildir ──
    extract-attachments.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: extract-attachments <mailbox>"
        echo ""
        echo "  Extract MIME attachments from the stored Maildir for <mailbox>."
        echo "  Reads from:  \$DEVENV_ROOT/data/mailboxes/<mailbox>/maildir/.maildir/"
        echo "  Writes to:   \$DEVENV_ROOT/data/mailboxes/<mailbox>/attachments/"
        echo ""
        echo "  Idempotent: re-running skips already-extracted files."
        echo ""
        echo "Options:"
        echo "  --help    Show this help message and exit"
        exit 0
      fi
      if [ -z "$1" ]; then
        echo "ERROR: mailbox name required"
        echo "Run: extract-attachments --help"
        exit 1
      fi
      MAILBOX="$1"
      DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
      MAILDIR="$DATA_ROOT/maildir/.maildir"
      ATTACHMENTS="$DATA_ROOT/attachments"
      if [ ! -d "$MAILDIR" ]; then
        echo "ERROR: Maildir not found: $MAILDIR"
        echo "Run sync-all first."
        exit 1
      fi
      mkdir -p "$ATTACHMENTS"
      echo "==> [extract-attachments] Extracting attachments for '$MAILBOX'..."
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
        "$MAILDIR" "$ATTACHMENTS" \
        || { echo "ERROR: extract-attachments failed"; exit 1; }
      echo "==> Done. Attachments: $ATTACHMENTS"
    '';

    # ── sync-all: weekly full archive sync for all mailboxes ─────────────
    sync-all.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: sync-all [--mailbox <name>] [--skip-rsync] [--skip-extract] [--skip-index]"
        echo ""
        echo "  Sync all mailboxes listed in data/mailboxes.txt:"
        echo "    1. rsync each mailbox from the remote server"
        echo "    2. extract-attachments for each mailbox"
        echo "    3. index-mailbox (per-mailbox + global SQLite index)"
        echo ""
        echo "  Remote source: mrija_org@s16.thehost.com.ua:email/mrija.org/<mailbox>/.maildir/"
        echo ""
        echo "Options:"
        echo "  --mailbox <name>   Sync only this mailbox (overrides mailboxes.txt)"
        echo "  --skip-rsync       Skip rsync step (use existing local data)"
        echo "  --skip-extract     Skip attachment extraction step"
        echo "  --skip-index       Skip SQLite indexing step"
        echo "  --help             Show this help message and exit"
        exit 0
      fi

      MAILBOXES_FILE="$DEVENV_ROOT/data/mailboxes.txt"
      GLOBAL_INDEX_DIR="$DEVENV_ROOT/data/index"
      REMOTE_BASE="mrija_org@s16.thehost.com.ua:email/mrija.org"

      SINGLE_MAILBOX=""
      SKIP_RSYNC=0
      SKIP_EXTRACT=0
      SKIP_INDEX=0
      while [ $# -gt 0 ]; do
        case "$1" in
          --mailbox) SINGLE_MAILBOX="$2"; shift 2 ;;
          --skip-rsync) SKIP_RSYNC=1; shift ;;
          --skip-extract) SKIP_EXTRACT=1; shift ;;
          --skip-index) SKIP_INDEX=1; shift ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done

      if [ -n "$SINGLE_MAILBOX" ]; then
        MAILBOX_LIST="$SINGLE_MAILBOX"
      else
        if [ ! -f "$MAILBOXES_FILE" ]; then
          echo "ERROR: $MAILBOXES_FILE not found."
          echo "  Create it with one mailbox name per line."
          exit 1
        fi
        # Read mailboxes: strip comments (#) and blank lines, validate names
        MAILBOX_LIST=""
        while IFS= read -r line || [ -n "$line" ]; do
          # Strip leading/trailing whitespace
          line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
          # Skip comments and empty lines
          case "$line" in
            "#"*|"") continue ;;
          esac
          # Validate: only [a-zA-Z0-9._-]
          if ! echo "$line" | grep -qE '^[a-zA-Z0-9._-]+$'; then
            echo "ERROR: Invalid mailbox name in $MAILBOXES_FILE: '$line'"
            echo "  Allowed characters: letters, digits, dots, hyphens, underscores"
            exit 1
          fi
          MAILBOX_LIST="$MAILBOX_LIST $line"
        done < "$MAILBOXES_FILE"
        MAILBOX_LIST="$(echo "$MAILBOX_LIST" | sed 's/^[[:space:]]*//')"
        if [ -z "$MAILBOX_LIST" ]; then
          echo "ERROR: No mailboxes found in $MAILBOXES_FILE"
          exit 1
        fi
      fi

      mkdir -p "$GLOBAL_INDEX_DIR"

      for MAILBOX in $MAILBOX_LIST; do
        echo ""
        echo "==> [sync-all] Processing mailbox: $MAILBOX"
        DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
        MAILDIR_DST="$DATA_ROOT/maildir/.maildir"

        # Step 1: rsync
        if [ "$SKIP_RSYNC" -eq 0 ]; then
          mkdir -p "$MAILDIR_DST"
          echo "    rsync from $REMOTE_BASE/$MAILBOX/.maildir/..."
          rsync -az --info=progress2 \
            "$REMOTE_BASE/$MAILBOX/.maildir/" \
            "$MAILDIR_DST/" \
            || { echo "ERROR: rsync failed for $MAILBOX"; exit 1; }
          echo "    rsync done."
        else
          echo "    [skip-rsync] skipping rsync for $MAILBOX"
        fi

        # Step 2: extract attachments
        if [ "$SKIP_EXTRACT" -eq 0 ]; then
          echo "    extracting attachments..."
          PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
            "$MAILDIR_DST" "$DATA_ROOT/attachments" \
            || { echo "ERROR: extract-attachments failed for $MAILBOX"; exit 1; }
          echo "    extraction done."
        else
          echo "    [skip-extract] skipping extraction for $MAILBOX"
        fi

        # Step 3: index
        if [ "$SKIP_INDEX" -eq 0 ]; then
          echo "    indexing (per-mailbox + global)..."
          PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
            --mailbox "$MAILBOX" \
            --data-root "$DATA_ROOT" \
            --global-index "$GLOBAL_INDEX_DIR/mail_index.sqlite" \
            || { echo "ERROR: index-mailbox failed for $MAILBOX"; exit 1; }
          echo "    indexing done."
        else
          echo "    [skip-index] skipping indexing for $MAILBOX"
        fi

        echo "    $MAILBOX: done"
      done

      echo ""
      echo "==> [sync-all] All mailboxes processed."
      echo "    Global index: $GLOBAL_INDEX_DIR/mail_index.sqlite"
    '';

    # ── search-archive: full-text search across archived emails ──────────────
    search-archive.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: search-archive <query> [--mailbox <name>] [--limit <n>]"
        echo ""
        echo "  Full-text search across archived emails in MySQL."
        echo "  Uses MariaDB FULLTEXT MATCH...AGAINST on subject, from_addr,"
        echo "  to_addrs, cc_addrs, body_text."
        echo ""
        echo "Options:"
        echo "  --mailbox <name>   Restrict search to one mailbox"
        echo "  --limit <n>        Max results (default: 20)"
        echo "  --help             Show this help message and exit"
        exit 0
      fi
      if [ -z "''${1:-}" ]; then
        echo "ERROR: search query required"
        echo "Run: search-archive --help"
        exit 1
      fi

      QUERY="$1"
      shift
      MAILBOX_FILTER=""
      LIMIT=20
      while [ $# -gt 0 ]; do
        case "$1" in
          --mailbox) MAILBOX_FILTER="$2"; shift 2 ;;
          --limit)   LIMIT="$2"; shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done

      SOCK="$DEVENV_STATE/mysql.sock"
      WHERE="MATCH(subject, from_addr, to_addrs, cc_addrs, body_text) AGAINST('$QUERY' IN BOOLEAN MODE)"
      if [ -n "$MAILBOX_FILTER" ]; then
        WHERE="$WHERE AND mailbox = '$MAILBOX_FILTER'"
      fi

      echo "==> Searching archive for: $QUERY"
      mysql -u mailreview --socket="$SOCK" mailreview \
        --table \
        -e "SELECT mailbox, date, from_addr, subject,
                   LEFT(body_text, 120) AS body_preview
            FROM archive_emails
            WHERE $WHERE
            ORDER BY date DESC
            LIMIT $LIMIT;" \
        || { echo "ERROR: search failed. Is db-start running?"; exit 1; }
    '';

  };

  # ── Shell welcome message ─────────────────────────────────────────────────
  enterShell = ''
    echo ""
    echo "  mailbox-archive devenv"
    echo "  ──────────────────────────────────────────────────────"
    echo "  sync-all                       rsync all mailboxes, extract, index, import"
    echo "  extract-attachments <mailbox>  extract MIME attachments for one mailbox"
    echo "  index-mailbox <mailbox>        (re)build per-mailbox SQLite index"
    echo "  index-all                      (re)build global index across all mailboxes"
    echo "  search-archive <query>         FULLTEXT search across archived emails"
    echo "  db-start                       start local MariaDB dev server"
    echo "  db-migrate                     run SQL migrations"
    echo "  ──────────────────────────────────────────────────────"
    echo "  data    : $DEVENV_ROOT/data/"
    echo "  logs    : $DEVENV_ROOT/logs/"
    echo ""
  '';
}
