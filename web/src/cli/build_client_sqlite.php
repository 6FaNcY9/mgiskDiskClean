<?php
/**
 * Build a Docker-free client SQLite database for the existing PHP UI.
 *
 * Source SQLite: data/index/mail_index.sqlite from the Python indexer
 * Output SQLite: data/client/mail_archive.sqlite with archive_* tables
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "This script must be run from the command line.\n");
    exit(1);
}

$opts = getopt('', ['source:', 'output:', 'help', 'h']);
if (isset($opts['help']) || isset($opts['h'])) {
    fwrite(STDOUT, <<<USAGE
Usage:
  php web/src/cli/build_client_sqlite.php \\
    --source data/index/mail_index.sqlite \\
    --output data/client/mail_archive.sqlite

Creates a SQLite database that the web UI can read directly, without Docker or
MariaDB. Existing output is replaced atomically.
USAGE);
    exit(0);
}

$sourcePath = (string)($opts['source'] ?? '');
$outputPath = (string)($opts['output'] ?? '');
if ($sourcePath === '' || $outputPath === '') {
    fwrite(STDERR, "ERROR: --source and --output are required.\n");
    exit(1);
}
if (!is_file($sourcePath)) {
    fwrite(STDERR, "ERROR: source SQLite not found: $sourcePath\n");
    exit(1);
}

$outputDir = dirname($outputPath);
if (!is_dir($outputDir) && !mkdir($outputDir, 0755, true)) {
    fwrite(STDERR, "ERROR: could not create output directory: $outputDir\n");
    exit(1);
}

$tmpPath = $outputPath . '.tmp';
@unlink($tmpPath);

try {
    $src = new PDO('sqlite:' . $sourcePath, '', '', [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    $dst = new PDO('sqlite:' . $tmpPath, '', '', [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);

    $dst->exec('PRAGMA journal_mode=DELETE');
    $dst->exec('PRAGMA foreign_keys=ON');
    $dst->exec(<<<SQL
CREATE TABLE archive_emails (
    mailbox          TEXT NOT NULL,
    stable_id        TEXT NOT NULL,
    filepath         TEXT NOT NULL,
    folder           TEXT NOT NULL DEFAULT '',
    date             TEXT NOT NULL DEFAULT '',
    from_addr        TEXT NOT NULL DEFAULT '',
    to_addrs         TEXT NOT NULL DEFAULT '',
    cc_addrs         TEXT NOT NULL DEFAULT '',
    subject          TEXT NOT NULL DEFAULT '',
    body_text        TEXT NOT NULL DEFAULT '',
    total_size_bytes INTEGER NOT NULL DEFAULT 0,
    imported_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (mailbox, stable_id)
);
CREATE INDEX idx_archive_emails_mailbox_date ON archive_emails (mailbox, date);
CREATE INDEX idx_archive_emails_date ON archive_emails (date);

CREATE TABLE archive_attachments (
    mailbox           TEXT NOT NULL,
    email_stable_id   TEXT NOT NULL,
    stored_path       TEXT NOT NULL,
    sha256            TEXT NOT NULL,
    size              INTEGER NOT NULL DEFAULT 0,
    mime              TEXT NOT NULL DEFAULT '',
    original_filename TEXT NOT NULL DEFAULT '',
    imported_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (mailbox, email_stable_id, sha256)
);
CREATE INDEX idx_archive_attachments_email ON archive_attachments (mailbox, email_stable_id);
CREATE INDEX idx_archive_attachments_sha256 ON archive_attachments (sha256);

CREATE TABLE vt_cache (
    sha256     TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    scan_id    TEXT NOT NULL DEFAULT '',
    positives  INTEGER NOT NULL DEFAULT 0,
    scanned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE review_decisions (
    mailbox          TEXT NOT NULL,
    email_stable_id  TEXT NOT NULL,
    decision         TEXT NOT NULL CHECK (decision IN ('keep', 'delete', 'unsure')),
    notes            TEXT NOT NULL DEFAULT '',
    reviewer_role    TEXT NOT NULL DEFAULT '',
    reviewer_name    TEXT NOT NULL DEFAULT '',
    decided_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (mailbox, email_stable_id)
);
CREATE INDEX idx_review_decisions_decision ON review_decisions (decision);
CREATE INDEX idx_review_decisions_decided_at ON review_decisions (decided_at);
SQL);

    $emailInsert = $dst->prepare(
        'INSERT OR IGNORE INTO archive_emails
         (mailbox, stable_id, filepath, folder, date, from_addr, to_addrs,
          cc_addrs, subject, body_text, total_size_bytes)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'
    );
    $attInsert = $dst->prepare(
        'INSERT OR IGNORE INTO archive_attachments
         (mailbox, email_stable_id, stored_path, sha256, size, mime, original_filename)
         VALUES (?, ?, ?, ?, ?, ?, ?)'
    );

    $dst->beginTransaction();
    $emailCount = 0;
    foreach ($src->query(
        'SELECT mailbox, stable_id, filepath, folder, date, from_addr,
                to_addrs, cc_addrs, subject, body_text, total_size_bytes
         FROM emails
         ORDER BY mailbox, date, stable_id'
    ) as $row) {
        $emailInsert->execute([
            $row['mailbox'],
            $row['stable_id'],
            $row['filepath'] ?? '',
            $row['folder'] ?? '',
            $row['date'] ?? '',
            $row['from_addr'] ?? '',
            $row['to_addrs'] ?? '',
            $row['cc_addrs'] ?? '',
            $row['subject'] ?? '',
            $row['body_text'] ?? '',
            (int)($row['total_size_bytes'] ?? 0),
        ]);
        $emailCount++;
    }

    $attCount = 0;
    foreach ($src->query(
        'SELECT e.mailbox, a.email_stable_id, a.stored_path, a.sha256,
                a.size, a.mime, a.original_filename
         FROM attachments a
         JOIN emails e ON e.stable_id = a.email_stable_id
         ORDER BY e.mailbox, a.email_stable_id, a.original_filename'
    ) as $row) {
        $attInsert->execute([
            $row['mailbox'],
            $row['email_stable_id'],
            $row['stored_path'] ?? '',
            $row['sha256'] ?? '',
            (int)($row['size'] ?? 0),
            $row['mime'] ?? '',
            $row['original_filename'] ?? '',
        ]);
        $attCount++;
    }
    $dst->commit();
    $dst = null;
    $src = null;

    if (!copy($tmpPath, $outputPath)) {
        throw new RuntimeException("could not copy $tmpPath to $outputPath");
    }
    @unlink($tmpPath);

    fwrite(STDOUT, "Built client SQLite: $outputPath\n");
    fwrite(STDOUT, "Emails: $emailCount  Attachments: $attCount\n");
} catch (Throwable $e) {
    if (isset($dst) && $dst instanceof PDO && $dst->inTransaction()) {
        $dst->rollBack();
    }
    @unlink($tmpPath);
    fwrite(STDERR, "ERROR: " . $e->getMessage() . "\n");
    exit(1);
}
