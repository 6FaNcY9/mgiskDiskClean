{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:
{
  # ── dotenv integration ────────────────────────────────────────────────────
  dotenv.enable = true;

  # ── Python pipeline ───────────────────────────────────────────────────────
  languages.python = {
    enable = true;
    venv = {
      enable = true;
      requirements = ''
        pytest>=8.0
        reportlab>=4.0
        imap-tools>=1.6
        fastapi>=0.110
        uvicorn>=0.29
        jinja2>=3.1
        rich>=13.7
        textual>=0.61
        httpx>=0.27
        python-multipart>=0.0.9
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
    clamav   # malware scanning for mail attachments
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
        mariadb -u mailreview --socket="''${MYSQL_UNIX_PORT:-$DEVENV_STATE/mysql.sock}" -e "SELECT 1" mailreview >/dev/null 2>&1 && break
        sleep 1
      done
      mariadb -u mailreview --socket="''${MYSQL_UNIX_PORT:-$DEVENV_STATE/mysql.sock}" -e "SELECT VERSION();" mailreview \
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
      SOCK="''${DB_SOCKET:-''${MYSQL_UNIX_PORT:-$DEVENV_STATE/mysql.sock}}"
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
        echo "Usage: sync-all [--mailbox <name>] [--skip-rsync] [--skip-extract] [--skip-index] [--skip-import] [--verify]"
        echo ""
        echo "  Sync all mailboxes listed in data/mailboxes.txt:"
        echo "    1. rsync each mailbox from the remote server"
        echo "    3. index-mailbox (per-mailbox + global SQLite index)"
        echo "    4. import into MariaDB (archive_emails + archive_attachments)"
        echo ""
        echo "  Remote source: mrija_org@s16.thehost.com.ua:email/mrija.org/<mailbox>/.maildir/"
        echo "  SSH key: ''${MRIJA_REMOTE_SSH_KEY:-$HOME/.ssh/thehost_mrija}"
        echo "  Override key with: MRIJA_REMOTE_SSH_KEY=/path/to/key sync-all"
        echo "  Password-in-.env support was removed on purpose."
        echo ""
        echo "Options:"
        echo "  --mailbox <name>   Sync only this mailbox (overrides mailboxes.txt)"
        echo "  --skip-rsync       Skip rsync step (use existing local data)"
        echo "  --skip-index       Skip SQLite indexing step"
        echo "  --skip-import      Skip MariaDB import step"
        echo "  --verify           After sync, verify integrity + scan for malware (requires freshclam-update once)"
        echo "  --help             Show this help message and exit"
        exit 0
      fi

      MAILBOXES_FILE="$DEVENV_ROOT/data/mailboxes.txt"
      GLOBAL_INDEX_DIR="$DEVENV_ROOT/data/index"
      REMOTE_BASE="mrija_org@s16.thehost.com.ua:email/mrija.org"
      REMOTE_SSH_KEY="''${MRIJA_REMOTE_SSH_KEY:-$HOME/.ssh/thehost_mrija}"

      SINGLE_MAILBOX=""
      SKIP_RSYNC=0
      SKIP_EXTRACT=0
      SKIP_INDEX=0
      SKIP_IMPORT=0
      VERIFY=0
      while [ $# -gt 0 ]; do
        case "$1" in
          --mailbox)      SINGLE_MAILBOX="$2"; shift 2 ;;
          --skip-rsync)   SKIP_RSYNC=1; shift ;;
          --skip-extract) SKIP_EXTRACT=1; shift ;;
          --skip-index)   SKIP_INDEX=1; shift ;;
          --skip-import)  SKIP_IMPORT=1; shift ;;
          --verify)       VERIFY=1; shift ;;
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

      if [ "$SKIP_RSYNC" -eq 0 ]; then
        if [ ! -r "$REMOTE_SSH_KEY" ]; then
          echo "ERROR: SSH key not readable: $REMOTE_SSH_KEY"
          echo "  Set MRIJA_REMOTE_SSH_KEY=/path/to/key or create ~/.ssh/thehost_mrija"
          exit 1
        fi
        SSH_CMD="ssh -i $REMOTE_SSH_KEY -o IdentitiesOnly=yes"
      fi

      if [ "$SKIP_IMPORT" -eq 0 ]; then
        IMPORT_SOCKET="''${MYSQL_UNIX_PORT:-$DEVENV_STATE/mysql.sock}"
        if ! mariadb -u mailreview --socket="$IMPORT_SOCKET" -e "SELECT 1" mailreview >/dev/null 2>&1; then
          echo "ERROR: MariaDB is not running or not reachable at: $IMPORT_SOCKET"
          echo "  Run: db-start"
          echo "  Then: db-migrate"
          echo "  Then rerun: sync-all"
          exit 1
        fi
      fi

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
            -e "$SSH_CMD" \
            "$REMOTE_BASE/$MAILBOX/.maildir/" \
            "$MAILDIR_DST/" \
            || { echo "ERROR: rsync failed for $MAILBOX (configure SSH keys for $REMOTE_BASE)"; exit 1; }
          echo "    rsync done."
        else
          echo "    [skip-rsync] skipping rsync for $MAILBOX"
        fi

        # Step 2: extract attachments
        if [ "$SKIP_EXTRACT" -eq 0 ]; then
          echo "    extracting attachments..."
          PYTHONPATH="$DEVENV_ROOT/src" python3 -m maildir_report.extract_attachments \
            --maildir-root "$MAILDIR_DST" --output-root "$DATA_ROOT/attachments" \
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

        # Step 4: import into MariaDB
        if [ "$SKIP_IMPORT" -eq 0 ]; then
          echo "    importing into MariaDB..."
          SQLITE_PATH="$DATA_ROOT/index.sqlite"
          php "$DEVENV_ROOT/web/src/cli/import_archive.php" \
            --sqlite "$SQLITE_PATH" \
            --socket "$IMPORT_SOCKET" \
            || { echo "ERROR: MariaDB import failed for $MAILBOX"; exit 1; }
          echo "    import done."
        else
          echo "    [skip-import] skipping MariaDB import for $MAILBOX"
        fi

        # Step 5: verify integrity + malware scan (optional)
        if [ "$VERIFY" -eq 1 ]; then
          echo "    verifying attachments (integrity + malware)..."
          CLAM_DB="$DEVENV_STATE/clamav"
          ATTACH_DIR="$DATA_ROOT/attachments"
          VERIFY_ERRORS=0
          if [ -d "$ATTACH_DIR" ]; then
            # SHA256 integrity check
            find "$ATTACH_DIR" -type f | while read -r FILEPATH; do
              FILENAME=$(basename "$FILEPATH")
              EXPECTED_HASH=$(echo "$FILENAME" | cut -d_ -f1)
              if [ ''${#EXPECTED_HASH} -eq 64 ]; then
                ACTUAL_HASH=$(sha256sum "$FILEPATH" | cut -d' ' -f1)
                if [ "$ACTUAL_HASH" != "$EXPECTED_HASH" ]; then
                  echo "    [CORRUPT] $FILENAME (hash mismatch)"
                  VERIFY_ERRORS=$((VERIFY_ERRORS + 1))
                fi
              fi
            done
            # ClamAV scan (only if DB exists)
            if [ -n "$(ls -A $CLAM_DB 2>/dev/null)" ]; then
              CLAM_LOG="/tmp/clamscan-sync-$MAILBOX-$$.log"
              clamscan --recursive --quiet \
                --database="$CLAM_DB" \
                --log="$CLAM_LOG" \
                "$ATTACH_DIR" 2>&1
              CLAM_EXIT=$?
              if [ $CLAM_EXIT -eq 1 ]; then
                INFECTED=$(grep 'FOUND' "$CLAM_LOG" | wc -l)
                echo "    [WARNING] ClamAV: $INFECTED infected file(s) in $MAILBOX!"
                grep 'FOUND' "$CLAM_LOG" | sed 's/^/      /'
              elif [ $CLAM_EXIT -eq 0 ]; then
                echo "    ClamAV: clean"
              fi
              rm -f "$CLAM_LOG"
            else
              echo "    [SKIP] ClamAV DB not found — run freshclam-update once to enable malware scan"
            fi
            [ $VERIFY_ERRORS -eq 0 ] && echo "    integrity: OK"
          else
            echo "    [SKIP] No attachments dir yet for $MAILBOX"
          fi
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

      SOCK="''${MYSQL_UNIX_PORT:-$DEVENV_STATE/mysql.sock}"
      WHERE="MATCH(subject, from_addr, to_addrs, cc_addrs, body_text) AGAINST('$QUERY' IN BOOLEAN MODE)"
      if [ -n "$MAILBOX_FILTER" ]; then
        WHERE="$WHERE AND mailbox = '$MAILBOX_FILTER'"
      fi

      echo "==> Searching archive for: $QUERY"
      mariadb -u mailreview --socket="$SOCK" mailreview \
        --table \
        -e "SELECT mailbox, date, from_addr, subject,
                   LEFT(body_text, 120) AS body_preview
            FROM archive_emails
            WHERE $WHERE
            ORDER BY date DESC
            LIMIT $LIMIT;" \
        || { echo "ERROR: search failed. Is db-start running?"; exit 1; }
    '';

    # ── mail-browser: launch the full-featured TUI ─────────────────────────
    mail-browser.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: mail-browser"
        echo ""
        echo "  Launch the mail archive terminal UI."
        echo "  Auto-connects to MariaDB (starts db service if needed)."
        echo ""
        echo "  Keybindings:"
        echo "    M          switch mailbox (fuzzy picker)"
        echo "    /          search (full-text)"
        echo "    D          date range filter"
        echo "    F          sender filter"
        echo "    R          reset all filters"
        echo "    Enter      open email detail"
        echo "    E          export selected email as .eml to ~/Downloads"
        echo "    Ctrl+S     sync selected mailbox"
        echo "    Ctrl+A     sync ALL mailboxes"
        echo "    F1/F2/F3   switch log panel (Sync / DB / App)"
        echo "    Q          quit"
        exit 0
      fi
      PYTHONPATH="$DEVENV_ROOT/src" python3 -m tui.main
    '';

    # ── freshclam-update: download/refresh ClamAV virus signature database ──
    freshclam-update.exec = ''
      CLAM_DB="$DEVENV_STATE/clamav"
      mkdir -p "$CLAM_DB"
      echo "==> Updating ClamAV virus signatures -> $CLAM_DB"
      freshclam --datadir="$CLAM_DB" --quiet --no-warnings \
        || freshclam --datadir="$CLAM_DB" \
        || { echo "ERROR: freshclam failed. Check network connectivity."; exit 1; }
      echo "    Signatures updated."
      echo "    DB files: $(ls -1 $CLAM_DB/*.cvd $CLAM_DB/*.cld 2>/dev/null | wc -l) files"
    '';

    # ── verify-archive: integrity + malware scan of ALL stored attachments ────
    verify-archive.exec = ''
      if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
        echo "Usage: verify-archive [--mailbox <name>] [--no-clam] [--quarantine <dir>]"
        echo ""
        echo "  Scan all stored attachment files for:"
        echo "    1. SHA256 integrity  — filename must match computed hash"
        echo "    2. Malware           — ClamAV scan of each file"
        echo ""
        echo "Options:"
        echo "  --mailbox <name>      Scan only one mailbox (default: all)"
        echo "  --no-clam             Skip ClamAV scan (integrity check only)"
        echo "  --quarantine <dir>    Move infected files here (default: warn only)"
        echo "  --update-sigs         Run freshclam-update before scanning"
        echo "  --help                Show this help"
        exit 0
      fi

      SINGLE_MAILBOX=""
      SKIP_CLAM=0
      QUARANTINE_DIR=""
      UPDATE_SIGS=0
      while [ $# -gt 0 ]; do
        case "$1" in
          --mailbox)     SINGLE_MAILBOX="$2"; shift 2 ;;
          --no-clam)     SKIP_CLAM=1; shift ;;
          --quarantine)  QUARANTINE_DIR="$2"; shift 2 ;;
          --update-sigs) UPDATE_SIGS=1; shift ;;
          *) echo "Unknown option: $1"; exit 1 ;;
        esac
      done

      CLAM_DB="$DEVENV_STATE/clamav"
      DATA_ROOT="$DEVENV_ROOT/data/mailboxes"

      # Optionally refresh signatures
      if [ "$UPDATE_SIGS" -eq 1 ]; then
        freshclam-update || exit 1
      fi

      # Check ClamAV DB exists
      if [ "$SKIP_CLAM" -eq 0 ]; then
        if [ -z "$(ls -A $CLAM_DB 2>/dev/null)" ]; then
          echo "WARNING: ClamAV signature database not found at $CLAM_DB"
          echo "  Run: freshclam-update   (downloads signatures ~200MB)"
          echo "  Or:  verify-archive --no-clam   (skip malware scan)"
          exit 1
        fi
      fi

      # Build mailbox list
      if [ -n "$SINGLE_MAILBOX" ]; then
        MAILBOX_LIST="$SINGLE_MAILBOX"
      else
        MAILBOX_LIST="$(ls -1 $DATA_ROOT/ | grep -v '^qa-archive' | grep -v '^testbox' | tr '\n' ' ')"
      fi

      TOTAL_FILES=0
      TOTAL_ERRORS=0
      TOTAL_INFECTED=0
      FAIL_LOG="$DEVENV_ROOT/logs/verify-archive-$(date +%Y%m%d-%H%M%S).log"
      mkdir -p "$DEVENV_ROOT/logs"

      echo "==> verify-archive started: $(date)"
      echo "    Mailboxes: $MAILBOX_LIST"
      [ "$SKIP_CLAM" -eq 1 ] && echo "    Mode: integrity only (ClamAV disabled)"
      [ "$SKIP_CLAM" -eq 0 ] && echo "    Mode: integrity + ClamAV malware scan"
      echo "    Log: $FAIL_LOG"
      echo ""

      for MAILBOX in $MAILBOX_LIST; do
        ATTACH_DIR="$DATA_ROOT/$MAILBOX/attachments"
        if [ ! -d "$ATTACH_DIR" ]; then
          continue
        fi

        FILE_COUNT=$(find "$ATTACH_DIR" -type f | wc -l)
        echo "--- $MAILBOX: $FILE_COUNT files"

        # ── Step 1: SHA256 integrity check ─────────────────────────────────
        INTEGRITY_ERRORS=0
        find "$ATTACH_DIR" -type f | while read -r FILEPATH; do
          FILENAME=$(basename "$FILEPATH")
          # Filename format: <sha256>_<size>.<ext>  — extract expected hash
          EXPECTED_HASH=$(echo "$FILENAME" | cut -d_ -f1)
          if [ ''${#EXPECTED_HASH} -ne 64 ]; then
            echo "  [WARN] Unexpected filename format (no hash prefix): $FILENAME" | tee -a "$FAIL_LOG"
            continue
          fi
          ACTUAL_HASH=$(sha256sum "$FILEPATH" | cut -d' ' -f1)
          if [ "$ACTUAL_HASH" != "$EXPECTED_HASH" ]; then
            echo "  [CORRUPT] $MAILBOX/$FILENAME" | tee -a "$FAIL_LOG"
            echo "    expected: $EXPECTED_HASH" | tee -a "$FAIL_LOG"
            echo "    actual:   $ACTUAL_HASH" | tee -a "$FAIL_LOG"
            INTEGRITY_ERRORS=$((INTEGRITY_ERRORS + 1))
          fi
        done
        TOTAL_FILES=$((TOTAL_FILES + FILE_COUNT))
        [ $INTEGRITY_ERRORS -gt 0 ] && TOTAL_ERRORS=$((TOTAL_ERRORS + INTEGRITY_ERRORS))

        # ── Step 2: ClamAV malware scan ─────────────────────────────────────
        if [ "$SKIP_CLAM" -eq 0 ]; then
          CLAM_LOG="/tmp/clamscan-$MAILBOX-$$.log"
          if [ -n "$QUARANTINE_DIR" ]; then
            mkdir -p "$QUARANTINE_DIR"
            clamscan --recursive --quiet \
              --database="$CLAM_DB" \
              --move="$QUARANTINE_DIR" \
              --log="$CLAM_LOG" \
              "$ATTACH_DIR" 2>&1
          else
            clamscan --recursive --quiet \
              --database="$CLAM_DB" \
              --log="$CLAM_LOG" \
              "$ATTACH_DIR" 2>&1
          fi
          CLAM_EXIT=$?
          if [ $CLAM_EXIT -eq 1 ]; then
            INFECTED=$(grep 'FOUND' "$CLAM_LOG" | wc -l)
            echo "  [INFECTED] $MAILBOX: $INFECTED file(s) found by ClamAV" | tee -a "$FAIL_LOG"
            grep 'FOUND' "$CLAM_LOG" | sed 's/^/    /' | tee -a "$FAIL_LOG"
            TOTAL_INFECTED=$((TOTAL_INFECTED + INFECTED))
          elif [ $CLAM_EXIT -eq 2 ]; then
            echo "  [CLAM_ERROR] $MAILBOX: ClamAV error — check $CLAM_LOG" | tee -a "$FAIL_LOG"
          else
            echo "  [OK] $MAILBOX: ClamAV clean"
          fi
          rm -f "$CLAM_LOG"
        fi
      done

      echo ""
      echo "==> verify-archive summary: $(date)"
      echo "    Files checked : $TOTAL_FILES"
      echo "    Corrupt files : $TOTAL_ERRORS"
      echo "    Infected files: $TOTAL_INFECTED"
      if [ $TOTAL_ERRORS -eq 0 ] && [ $TOTAL_INFECTED -eq 0 ]; then
        echo "    Result: ALL CLEAN"
        exit 0
      else
        echo "    Result: ISSUES FOUND — see $FAIL_LOG"
        exit 1
      fi
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
  echo "  mail-browser                   launch terminal UI (auto-starts DB)"
  echo "  freshclam-update               download/refresh ClamAV virus signatures"
  echo "  verify-archive [--mailbox x]   integrity + malware scan of all attachments"
    echo "  ──────────────────────────────────────────────────────"
    echo "  data    : $DEVENV_ROOT/data/"
    echo "  logs    : $DEVENV_ROOT/logs/"
    echo ""
  '';
}
