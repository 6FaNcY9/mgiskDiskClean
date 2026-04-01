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

    # ── Existing pipeline script (preserved) ─────────────────────────────
    scan-mailbox.exec = ''
      if [ -z "$1" ]; then
        echo "Usage: scan-mailbox <mailbox>"
        echo "  e.g. scan-mailbox gabriel.hangel"
        exit 1
      fi
      MAILBOX="$1"
      LOCAL_MAIL=/tmp/mrija_maildir/$MAILBOX
      mkdir -p $LOCAL_MAIL

      echo "==> Pulling maildir from server (may take a while)..."
      rsync -az --info=progress2 \
        mrija_org@s16.thehost.com.ua:email/mrija.org/$MAILBOX/.maildir/ \
        $LOCAL_MAIL/.maildir/ \
        || { echo "ERROR: rsync failed"; exit 1; }

      echo "==> Generating report..."
      PYTHONPATH=$DEVENV_ROOT/src python -m maildir_report \
        $LOCAL_MAIL/.maildir \
        $DEVENV_ROOT/reports \
        || { echo "ERROR: report generation failed"; exit 1; }

      echo "==> Done. Outputs written to $DEVENV_ROOT/reports/"
    '';

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

    # ── store-mailbox: rsync + pre-store dedup hook + pipeline ────────────
    store-mailbox.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: store-mailbox <mailbox> [--src <rsync-source>]"
        echo ""
        echo "  Store a remote mailbox locally under \$DEVENV_ROOT/data/mailboxes/<mailbox>/."
        echo "  Layout created:"
        echo "    maildir/.maildir/   rsync target (Maildir format)"
        echo "    reports/            pipeline outputs (PDF, manifest, decisions CSV)"
        echo "    attachments/        extracted attachment files (Task 2b)"
        echo "    index.sqlite        per-mailbox index DB (Task 3/4)"
        echo ""
        echo "  Workflow:"
        echo "    1. rsync remote mailbox into maildir/.maildir/"
        echo "    2. Run pre-store dedup (quarantine-only) on local copy only"
        echo "    3. Run maildir_report pipeline into reports/"
        echo ""
        echo "  Scope guardrail: dedup operates ONLY on local data/ copy."
        echo "  No changes are made to the remote server mailbox."
        echo ""
        echo "Options:"
        echo "  --src <rsync-source>   Override rsync source URL"
        echo "                         Default: mrija_org@s16.thehost.com.ua:email/mrija.org/<mailbox>/.maildir/"
        echo "  --help                 Show this help message and exit"
        exit 0
      fi

      if [ -z "$1" ]; then
        echo "ERROR: mailbox name required"
        echo "Run: store-mailbox --help"
        exit 1
      fi

      MAILBOX="$1"
      shift
      DATA_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX"
      MAILDIR_DST="$DATA_ROOT/maildir/.maildir"
      REPORTS_DST="$DATA_ROOT/reports"
      ATTACHMENTS_DST="$DATA_ROOT/attachments"

      # Parse optional --src override
      RSYNC_SRC="mrija_org@s16.thehost.com.ua:email/mrija.org/$MAILBOX/.maildir/"
      while [ $# -gt 0 ]; do
        case "$1" in
          --src) RSYNC_SRC="$2"; shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done

      # 1. Create stable local folder layout
      mkdir -p "$MAILDIR_DST" "$REPORTS_DST" "$ATTACHMENTS_DST"

      echo "==> [store-mailbox] rsync '$RSYNC_SRC' -> '$MAILDIR_DST'"
      rsync -az --info=progress2 \
        "$RSYNC_SRC" \
        "$MAILDIR_DST/" \
        || { echo "ERROR: rsync failed"; exit 1; }

      # 2. Pre-store dedup (quarantine-only; scope: local data/ only)
      #    SCOPE GUARDRAIL: only path $DATA_ROOT is ever touched.
      echo "==> [store-mailbox] Running pre-store dedup on local copy..."
      if command -v python3 >/dev/null 2>&1 && \
         python3 -c "import maildir_report.pre_store_dedup" 2>/dev/null; then
        PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.pre_store_dedup \
          --maildir-root "$MAILDIR_DST" \
          --quarantine-root "$DATA_ROOT/quarantine" \
          || { echo "ERROR: pre-store dedup failed; aborting store"; exit 1; }
      else
        echo "  (pre-store dedup module not yet available; skipping — Task 2a)"
      fi

      # 3. Run pipeline: generate PDF/manifest/decisions CSV into reports/
      echo "==> [store-mailbox] Generating pipeline outputs..."
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report \
        "$MAILDIR_DST" \
        "$REPORTS_DST" \
        || { echo "ERROR: report generation failed"; exit 1; }

      echo "==> Done."
      echo "  maildir   : $MAILDIR_DST"
      echo "  reports   : $REPORTS_DST"
      echo "  attachments: $ATTACHMENTS_DST"
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
        echo "  Requires: store-mailbox to have run first."
        echo "  Implemented fully in Task 2b."
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
        echo "  Implemented fully in Task 2b (optional global index)."
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

    # ── review-start: start PHP built-in server for local QA ─────────────
    review-start.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: review-start [--port <port>] [--host <host>]"
        echo ""
        echo "  Start the PHP built-in server for local QA."
        echo "  Serves web/public/ with local config pointing at \$DEVENV_ROOT/data/."
        echo ""
        echo "Options:"
        echo "  --port <port>   Listen port (default: 8000)"
        echo "  --host <host>   Listen host (default: 127.0.0.1)"
        echo "  --help          Show this help message and exit"
        echo ""
        echo "  Requires web/config/local.php to exist."
        echo "  Copy web/config/local.php.example -> web/config/local.php and edit."
        exit 0
      fi
      HOST="127.0.0.1"
      PORT="8000"
      while [ $# -gt 0 ]; do
        case "$1" in
          --port) PORT="$2"; shift 2 ;;
          --host) HOST="$2"; shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done
      CONFIG="$DEVENV_ROOT/web/config/local.php"
      if [ ! -f "$CONFIG" ]; then
        echo "ERROR: $CONFIG not found."
        echo "  Copy: cp web/config/local.php.example web/config/local.php"
        echo "  Then edit it with your local settings."
        exit 1
      fi
      echo "==> Starting PHP built-in server at http://$HOST:$PORT"
      echo "    Document root: $DEVENV_ROOT/web/public"
      echo "    Config: $CONFIG"
      php -S "$HOST:$PORT" -t "$DEVENV_ROOT/web/public"
    '';

    # ── apply-decisions: invoke local Python decisions apply tool ─────────
    apply-decisions.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: apply-decisions <mailbox> <reviewed_decisions.csv> [--mode quarantine|delete] [--dry-run]"
        echo ""
        echo "  Apply a reviewed decisions CSV to the local stored mailbox copy."
        echo "  Operates ONLY on: \$DEVENV_ROOT/data/mailboxes/<mailbox>/maildir/.maildir/"
        echo ""
        echo "  Workflow (plan-then-apply):"
        echo "    1. plan  — writes cleanup_plan.json with candidate list + candidate_set_hash"
        echo "    2. apply — requires --plan cleanup_plan.json --confirm <hash-prefix>"
        echo ""
        echo "Options:"
        echo "  --mode quarantine|delete   Action for 'delete' rows (default: quarantine)"
        echo "  --dry-run                  Print plan; write cleanup_plan.json; no file moves"
        echo "  --help                     Show this help message and exit"
        echo ""
        echo "  Implemented fully in Task 12."
        exit 0
      fi
      if [ -z "$1" ] || [ -z "$2" ]; then
        echo "ERROR: mailbox and decisions CSV path are required"
        echo "Run: apply-decisions --help"
        exit 1
      fi
      MAILBOX="$1"
      DECISIONS_CSV="$2"
      shift 2
      MAILDIR_ROOT="$DEVENV_ROOT/data/mailboxes/$MAILBOX/maildir/.maildir"
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.apply_decisions \
        --maildir-root "$MAILDIR_ROOT" \
        --decisions-csv "$DECISIONS_CSV" \
        "$@"
    '';

    # ── fetch-imap: fetch IMAP mailbox and materialise local Maildir ─────────
    fetch-imap.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: fetch-imap <mailbox> <data-dir> [--since YYYY-MM-DD]"
        echo ""
        echo "  Fetch messages from an IMAP server (IMAPS/TLS, port 993) and"
        echo "  materialise a local Maildir under:"
        echo "    <data-dir>/imap/<mailbox>/INBOX/Maildir/cur/{uidvalidity}.{uid}.eml"
        echo ""
        echo "  Credentials must be set as environment variables:"
        echo "    IMAP_SERVER   IMAP server hostname"
        echo "    IMAP_USER     IMAP login username"
        echo "    IMAP_PASS     IMAP password or app password"
        echo ""
        echo "  The operation is READ-ONLY: no server-side mutations are performed."
        echo "  Re-running is idempotent: same messages produce the same file list."
        echo ""
        echo "  After fetching, the local Maildir is ready for:"
        echo "    PYTHONPATH=\$DEVENV_ROOT/src python3 -m maildir_report \\"
        echo "      <data-dir>/imap/<mailbox>/INBOX/Maildir \\"
        echo "      \$DEVENV_ROOT/reports"
        echo ""
        echo "Options:"
        echo "  --since YYYY-MM-DD   Fetch only messages on or after this date"
        echo "  --help               Show this help message and exit"
        exit 0
      fi
      if [ -z "$1" ] || [ -z "$2" ]; then
        echo "ERROR: mailbox name and data-dir are required"
        echo "Run: fetch-imap --help"
        exit 1
      fi
      MAILBOX="$1"
      DATA_DIR="$2"
      shift 2
      SINCE_ARG=""
      while [ $# -gt 0 ]; do
        case "$1" in
          --since) SINCE_ARG="--since $2"; shift 2 ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done
      if [ -z "''${IMAP_SERVER:-}" ] || [ -z "''${IMAP_USER:-}" ] || [ -z "''${IMAP_PASS:-}" ]; then
        echo "ERROR: IMAP_SERVER, IMAP_USER, and IMAP_PASS must all be set."
        echo "  Example: export IMAP_SERVER=imap.gmail.com IMAP_USER=you@example.com IMAP_PASS=apppassword"
        exit 1
      fi
      echo "==> [fetch-imap] Fetching '$MAILBOX' from $IMAP_SERVER (IMAPS)..."
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.imap_ingest $SINCE_ARG \
        "$MAILBOX" "$DATA_DIR" \
        || { echo "ERROR: IMAP fetch failed"; exit 1; }
      echo "==> [fetch-imap] Done."
      echo "  Maildir: $DATA_DIR/imap/$MAILBOX/INBOX/Maildir/"
    '';



  };

  # ── Shell welcome message ─────────────────────────────────────────────────
  enterShell = ''
    echo ""
    echo "  maildir-pdf-report devenv"
    echo "  ──────────────────────────────────────────────────────"
    echo "  scan-mailbox <mailbox>         rsync maildir, generate PDF/manifest/decisions"
    echo "  store-mailbox <mailbox>        rsync + dedup + pipeline into data/mailboxes/"
    echo "  fetch-imap <mailbox> <dir>     fetch IMAP mailbox to local Maildir (read-only)"
    echo "  index-mailbox <mailbox>        (re)build per-mailbox SQLite index"
    echo "  index-all                      (re)build global index across all mailboxes"
    echo "  db-start                       start local MariaDB dev server"
    echo "  db-migrate                     run SQL migrations"
    echo "  review-start                   start PHP dev server at http://127.0.0.1:8000"
    echo "  apply-decisions <mb> <csv>     apply reviewed decisions locally"
    echo "  ──────────────────────────────────────────────────────"
    echo "  reports : $DEVENV_ROOT/reports/"
    echo "  data    : $DEVENV_ROOT/data/"
    echo "  logs    : $DEVENV_ROOT/logs/"
    echo ""
  '';
}
