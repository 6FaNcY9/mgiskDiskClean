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
      '';
    };
  };

  # ── PHP 8.3 (frameworkless) ───────────────────────────────────────────────
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
  ];

  # ── devenv scripts ────────────────────────────────────────────────────────
  scripts = {

    # ── db-start: start the local MariaDB dev server ──────────────────────
    db-start.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: db-start"
        echo "  Start the local MariaDB dev server managed by devenv."
        exit 0
      fi
      echo "==> Starting MariaDB via devenv process manager..."
      devenv up &
      echo "==> Waiting for MariaDB socket..."
      for i in $(seq 1 30); do
        mysql -u mailreview --socket="$DEVENV_STATE/mysql.sock" \
          -e "SELECT 1" mailreview >/dev/null 2>&1 && break
        sleep 1
      done
      mysql -u mailreview --socket="$DEVENV_STATE/mysql.sock" \
        -e "SELECT VERSION();" mailreview \
        || { echo "ERROR: MariaDB not responding after 30s"; exit 1; }
      echo "==> MariaDB ready."
    '';

    # ── db-migrate: run SQL migrations ────────────────────────────────────
    db-migrate.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: db-migrate [--socket <path>]"
        echo "  Run pending SQL migrations against MariaDB."
        exit 0
      fi
      SOCK="''${DB_SOCKET:-$DEVENV_STATE/mysql.sock}"
      if [ "''${1:-}" = "--socket" ] && [ -n "''${2:-}" ]; then
        SOCK="$2"
      fi
      php "$DEVENV_ROOT/web/src/cli/migrate.php" --socket "$SOCK"
    '';

    # ── extract-attachments: extract MIME attachments for one mailbox ──────
    extract-attachments.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: extract-attachments <mailbox>"
        echo "  Extract MIME attachments from the stored Maildir to attachments/."
        exit 0
      fi
      if [ -z "''${1:-}" ]; then
        echo "ERROR: mailbox name required"
        echo "Run: extract-attachments --help"
        exit 1
      fi
      MAILBOX="$1"
      DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
      echo "==> [extract-attachments] $MAILBOX"
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
        --maildir-root "$DATA_ROOT/maildir/.maildir" \
        --output-root  "$DATA_ROOT/attachments" \
        || { echo "ERROR: extraction failed for $MAILBOX"; exit 1; }
      echo "==> Done."
    '';

    # ── index-mailbox: (re)build per-mailbox SQLite index ─────────────────
    index-mailbox.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: index-mailbox <mailbox> [--global-index <path>]"
        echo "  Build the SQLite index for a stored mailbox."
        exit 0
      fi
      if [ -z "''${1:-}" ]; then
        echo "ERROR: mailbox name required"; exit 1
      fi
      MAILBOX="$1"
      shift
      GLOBAL_ARG=""
      while [ $# -gt 0 ]; do
        case "$1" in
          --global-index) GLOBAL_ARG="--global-index $2"; shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done
      DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
      echo "==> [index-mailbox] $MAILBOX"
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
        --mailbox "$MAILBOX" \
        --data-root "$DATA_ROOT" \
        $GLOBAL_ARG \
        || { echo "ERROR: indexing failed for $MAILBOX"; exit 1; }
      echo "==> Done."
    '';

    # ── index-all: (re)build global index across all mailboxes ────────────
    index-all.exec = ''
      GLOBAL_INDEX="$DEVENV_ROOT/data/index/mail_index.sqlite"
      mkdir -p "$DEVENV_ROOT/data/index"
      echo "==> [index-all] Building global index..."
      for MAILBOX_DIR in "$DEVENV_ROOT/data/mailboxes"/*/; do
        MAILBOX="$(basename "$MAILBOX_DIR")"
        echo "    indexing: $MAILBOX"
        PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
          --mailbox "$MAILBOX" \
          --data-root "$MAILBOX_DIR" \
          --global-index "$GLOBAL_INDEX" 2>/dev/null \
          || echo "    WARNING: index failed for $MAILBOX (skipping)"
      done
      echo "==> Done. Global index: $GLOBAL_INDEX"
    '';

    # ── sync-all: full pipeline — rsync + extract + index + MySQL import ──
    sync-all.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: sync-all [OPTIONS]"
        echo ""
        echo "  Download all mailboxes, extract attachments, build SQLite indexes,"
        echo "  then import into MySQL archive tables."
        echo ""
        echo "  READ-ONLY: no changes are made to the remote server."
        echo ""
        echo "Options:"
        echo "  --mailboxes-file <path>   Use a local mailbox list instead of fetching"
        echo "                            from the server (useful for testing)."
        echo "  --src-base <base>         Override rsync source base URL."
        echo "                            Default: mrija_org@s16.thehost.com.ua:email/mrija.org"
        echo "                            Use a local path for fixture testing."
        echo "  --skip-import             Skip the MySQL import step."
        echo "  --mailbox <name>          Sync a single mailbox only."
        echo "  --help                    Show this message."
        exit 0
      fi

      MAILBOXES_FILE=""
      SRC_BASE="mrija_org@s16.thehost.com.ua:email/mrija.org"
      SKIP_IMPORT=0
      SINGLE_MAILBOX=""

      while [ $# -gt 0 ]; do
        case "$1" in
          --mailboxes-file) MAILBOXES_FILE="$2"; shift 2 ;;
          --src-base)       SRC_BASE="$2";       shift 2 ;;
          --skip-import)    SKIP_IMPORT=1;        shift   ;;
          --mailbox)        SINGLE_MAILBOX="$2";  shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done

      # Fetch mailbox list from server if not provided locally
      if [ -z "$MAILBOXES_FILE" ]; then
        MAILBOXES_FILE="$DEVENV_ROOT/data/mailboxes.txt"
        mkdir -p "$DEVENV_ROOT/data"
        echo "==> [sync-all] Fetching mailbox list from server..."
        rsync -az "$SRC_BASE/mailboxes.txt" "$MAILBOXES_FILE" \
          || { echo "ERROR: Could not fetch mailboxes.txt from $SRC_BASE"; exit 1; }
      fi

      if [ ! -f "$MAILBOXES_FILE" ]; then
        echo "ERROR: mailboxes file not found: $MAILBOXES_FILE"; exit 1
      fi

      # Parse and validate mailbox names
      if [ -n "$SINGLE_MAILBOX" ]; then
        MAILBOXES="$SINGLE_MAILBOX"
      else
        MAILBOXES=$(grep -v '^\s*#' "$MAILBOXES_FILE" \
                    | grep -v '^\s*$' \
                    | grep -E '^[A-Za-z0-9._-]+$' || true)
      fi

      if [ -z "$MAILBOXES" ]; then
        echo "ERROR: No valid mailbox names found in $MAILBOXES_FILE"; exit 1
      fi

      FAILED=""
      GLOBAL_INDEX="$DEVENV_ROOT/data/index/mail_index.sqlite"
      mkdir -p "$DEVENV_ROOT/data/index"

      for MAILBOX in $MAILBOXES; do
        echo ""
        echo "==> [sync-all] [$MAILBOX] Starting..."
        DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
        MAILDIR_DST="$DATA_ROOT/maildir/.maildir"
        ATTACHMENTS_DST="$DATA_ROOT/attachments"
        mkdir -p "$MAILDIR_DST" "$ATTACHMENTS_DST"

        # Step 1: rsync (read-only)
        echo "  [1/3] rsync $SRC_BASE/$MAILBOX/.maildir/ -> $MAILDIR_DST/"
        rsync -az --info=progress2 \
          "$SRC_BASE/$MAILBOX/.maildir/" \
          "$MAILDIR_DST/" \
          || { echo "  ERROR: rsync failed for $MAILBOX"; FAILED="$FAILED $MAILBOX"; continue; }

        # Step 2: extract attachments
        echo "  [2/3] extracting attachments..."
        PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
          --maildir-root "$MAILDIR_DST" \
          --output-root  "$ATTACHMENTS_DST" \
          || { echo "  ERROR: extraction failed for $MAILBOX"; FAILED="$FAILED $MAILBOX"; continue; }

        # Step 3: index (per-mailbox + global)
        echo "  [3/3] indexing..."
        PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.index_mailbox \
          --mailbox "$MAILBOX" \
          --data-root "$DATA_ROOT" \
          --global-index "$GLOBAL_INDEX" \
          || { echo "  ERROR: indexing failed for $MAILBOX"; FAILED="$FAILED $MAILBOX"; continue; }

        echo "  Done: $MAILBOX"
      done

      # Step 4: MySQL import
      if [ "$SKIP_IMPORT" = "0" ]; then
        echo ""
        echo "==> [sync-all] Importing to MySQL..."
        php "$DEVENV_ROOT/web/src/cli/import_archive.php" \
          --sqlite "$GLOBAL_INDEX" \
          || { echo "ERROR: MySQL import failed"; FAILED="$FAILED MYSQL_IMPORT"; }
      fi

      echo ""
      if [ -n "$FAILED" ]; then
        echo "==> [sync-all] COMPLETED WITH FAILURES:$FAILED"
        exit 1
      fi
      echo "==> [sync-all] All done."
    '';

    # ── search-archive: search the MySQL archive ──────────────────────────
    search-archive.exec = ''
      if [ "''${1:-}" = "--help" ] || [ "''${1:-}" = "-h" ]; then
        echo "Usage: search-archive <query> [--mailbox <name>] [--limit <n>]"
        echo "  Search archived emails via MySQL FULLTEXT."
        exit 0
      fi
      if [ -z "''${1:-}" ]; then
        echo "ERROR: search query required"
        echo "Run: search-archive --help"
        exit 1
      fi
      QUERY="$1"
      shift
      MAILBOX_ARG=""
      LIMIT_ARG=""
      while [ $# -gt 0 ]; do
        case "$1" in
          --mailbox) MAILBOX_ARG="--mailbox $2"; shift 2 ;;
          --limit)   LIMIT_ARG="--limit $2";    shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done
      php "$DEVENV_ROOT/web/src/cli/search_archive.php" \
        --query "$QUERY" \
        $MAILBOX_ARG \
        $LIMIT_ARG
    '';

  };

  # ── Shell welcome message ─────────────────────────────────────────────────
  enterShell = ''
    echo ""
    echo "  mailbox-archive devenv"
    echo "  ──────────────────────────────────────────────────────────"
    echo "  sync-all [--mailboxes-file f] [--src-base b] [--skip-import]"
    echo "           download all mailboxes + index + MySQL import"
    echo "  extract-attachments <mailbox>   extract MIME attachments"
    echo "  index-mailbox <mailbox>         rebuild SQLite index"
    echo "  index-all                       rebuild global SQLite index"
    echo "  db-start                        start local MariaDB"
    echo "  db-migrate                      run SQL migrations"
    echo "  search-archive <query>          search the archive"
    echo "  ──────────────────────────────────────────────────────────"
    echo "  data    : $DEVENV_ROOT/data/"
    echo ""
  '';
}
