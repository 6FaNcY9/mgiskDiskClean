<?php
/**
 * web/src/cli/import_archive.php — Import SQLite archive index into MySQL.
 *
 * Reads from a SQLite index file (global or per-mailbox) and upserts all
 * rows into MySQL archive_emails and archive_attachments tables.
 *
 * Designed to run after index-mailbox / index-all. Idempotent (uses
 * INSERT ... ON DUPLICATE KEY UPDATE). Chunk size: 5000 rows.
 *
 * Usage:
 *   php web/src/cli/import_archive.php \
 *     --sqlite <path/to/mail_index.sqlite> \
 *     [--config <path/to/local.php>] \
 *     [--socket <mariadb-socket>] \
 *     [--chunk <n>] \
 *     [--quiet]
 */

declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "This script must be run from the command line.\n");
    exit(1);
}

// ── Parse args ───────────────────────────────────────────────────────────────
$opts = getopt('', ['sqlite:', 'config:', 'socket:', 'chunk:', 'quiet', 'help', 'h']);

if (isset($opts['help']) || isset($opts['h'])) {
    fwrite(STDOUT, <<<USAGE
    Usage: php web/src/cli/import_archive.php [OPTIONS]

      Import a SQLite archive index into MySQL archive_emails and
      archive_attachments tables. Idempotent: safe to run multiple times.

    Options:
      --sqlite <path>   Path to SQLite index file (required)
                        Use data/index/mail_index.sqlite for global index
      --config <path>   Path to local.php config (default: web/config/local.php)
      --socket <path>   MariaDB Unix socket (overrides config)
      --chunk <n>       Rows per transaction (default: 5000)
      --quiet           Suppress progress output
      --help            Show this message and exit

    Exit codes:
      0  Success
      1  Error

    USAGE);
    exit(0);
}

if (empty($opts['sqlite'])) {
    fwrite(STDERR, "ERROR: --sqlite <path> is required.\n");
    fwrite(STDERR, "Run: php web/src/cli/import_archive.php --help\n");
    exit(1);
}

$sqlitePath = $opts['sqlite'];
$chunkSize  = (int)($opts['chunk'] ?? 5000);
$quiet      = isset($opts['quiet']);

if (!is_file($sqlitePath)) {
    fwrite(STDERR, "ERROR: SQLite file not found: $sqlitePath\n");
    exit(1);
}

// ── Load config ──────────────────────────────────────────────────────────────
$scriptDir  = dirname(__DIR__, 2); // web/
$configPath = $opts['config'] ?? ($scriptDir . '/config/local.php');

if (!is_file($configPath)) {
    fwrite(STDERR, "ERROR: Config not found: $configPath\n");
    fwrite(STDERR, "  Copy web/config/local.php.example -> web/config/local.php\n");
    exit(1);
}

/** @var array<string,mixed> $config */
$config = require $configPath;
$dbCfg  = $config['db'] ?? [];

$socket = $opts['socket'] ?? $dbCfg['socket'] ?? (getenv('DEVENV_STATE') . '/mysql.sock');

// ── Connect to MySQL ─────────────────────────────────────────────────────────
if ($socket && file_exists($socket)) {
    $dsn = "mysql:unix_socket=$socket;dbname={$dbCfg['dbname']};charset={$dbCfg['charset']}";
} else {
    $host = $dbCfg['host'] ?? '127.0.0.1';
    $port = $dbCfg['port'] ?? 3306;
    $dsn  = "mysql:host=$host;port=$port;dbname={$dbCfg['dbname']};charset={$dbCfg['charset']}";
}

try {
    $pdo = new PDO($dsn, $dbCfg['user'] ?? '', $dbCfg['password'] ?? '', [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES   => false,
    ]);
} catch (PDOException $e) {
    fwrite(STDERR, "ERROR: MySQL connection failed: " . $e->getMessage() . "\n");
    exit(1);
}

// ── Connect to SQLite ────────────────────────────────────────────────────────
try {
    $sqlite = new PDO('sqlite:' . $sqlitePath, '', '', [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
} catch (PDOException $e) {
    fwrite(STDERR, "ERROR: SQLite open failed: " . $e->getMessage() . "\n");
    exit(1);
}

// ── Prepared statements ──────────────────────────────────────────────────────
$upsertEmail = $pdo->prepare(<<<SQL
    INSERT INTO archive_emails
        (mailbox, stable_id, filepath, folder, date, from_addr,
         to_addrs, cc_addrs, subject, body_text, total_size_bytes)
    VALUES
        (:mailbox, :stable_id, :filepath, :folder, :date, :from_addr,
         :to_addrs, :cc_addrs, :subject, :body_text, :total_size_bytes)
    ON DUPLICATE KEY UPDATE
        filepath         = VALUES(filepath),
        folder           = VALUES(folder),
        date             = VALUES(date),
        from_addr        = VALUES(from_addr),
        to_addrs         = VALUES(to_addrs),
        cc_addrs         = VALUES(cc_addrs),
        subject          = VALUES(subject),
        body_text        = VALUES(body_text),
        total_size_bytes = VALUES(total_size_bytes)
SQL);

$upsertAttachment = $pdo->prepare(<<<SQL
    INSERT INTO archive_attachments
        (mailbox, email_stable_id, stored_path, sha256, size, mime, original_filename)
    VALUES
        (:mailbox, :email_stable_id, :stored_path, :sha256, :size, :mime, :original_filename)
    ON DUPLICATE KEY UPDATE
        stored_path       = VALUES(stored_path),
        size              = VALUES(size),
        mime              = VALUES(mime),
        original_filename = VALUES(original_filename)
SQL);

// ── Import emails ────────────────────────────────────────────────────────────
$totalEmails = (int)$sqlite->query("SELECT COUNT(*) FROM emails")->fetchColumn();
if (!$quiet) {
    fwrite(STDOUT, "==> Importing $totalEmails emails from $sqlitePath\n");
}

$emailsImported = 0;
$offset = 0;

while ($offset < $totalEmails) {
    $rows = $sqlite->query(
        "SELECT mailbox, stable_id, filepath, folder, date, from_addr,
                to_addrs, cc_addrs, subject, body_text, total_size_bytes
         FROM emails
         LIMIT $chunkSize OFFSET $offset"
    )->fetchAll();

    if (empty($rows)) {
        break;
    }

    $pdo->beginTransaction();
    try {
        foreach ($rows as $row) {
            $upsertEmail->execute([
                ':mailbox'          => $row['mailbox'],
                ':stable_id'        => $row['stable_id'],
                ':filepath'         => $row['filepath'],
                ':folder'           => $row['folder'],
                ':date'             => $row['date'],
                ':from_addr'        => $row['from_addr'],
                ':to_addrs'         => $row['to_addrs'] ?? '',
                ':cc_addrs'         => $row['cc_addrs'] ?? '',
                ':subject'          => $row['subject'],
                ':body_text'        => $row['body_text'] ?? '',
                ':total_size_bytes' => (int)($row['total_size_bytes'] ?? 0),
            ]);
        }
        $pdo->commit();
    } catch (PDOException $e) {
        $pdo->rollBack();
        fwrite(STDERR, "ERROR: Email import failed at offset $offset: " . $e->getMessage() . "\n");
        exit(1);
    }

    $emailsImported += count($rows);
    $offset += $chunkSize;

    if (!$quiet) {
        fwrite(STDOUT, "  emails: $emailsImported / $totalEmails\n");
    }
}

// ── Import attachments ────────────────────────────────────────────────────────
$totalAtts = (int)$sqlite->query("SELECT COUNT(*) FROM attachments")->fetchColumn();
if (!$quiet) {
    fwrite(STDOUT, "==> Importing $totalAtts attachments\n");
}

// We need mailbox for each attachment — join emails table
$attsImported = 0;
$offset = 0;

while ($offset < $totalAtts) {
    $rows = $sqlite->query(
        "SELECT e.mailbox, a.email_stable_id, a.stored_path,
                a.sha256, a.size, a.mime, a.original_filename
         FROM attachments a
         JOIN emails e ON e.stable_id = a.email_stable_id
         LIMIT $chunkSize OFFSET $offset"
    )->fetchAll();

    if (empty($rows)) {
        break;
    }

    $pdo->beginTransaction();
    try {
        foreach ($rows as $row) {
            $upsertAttachment->execute([
                ':mailbox'           => $row['mailbox'],
                ':email_stable_id'   => $row['email_stable_id'],
                ':stored_path'       => $row['stored_path'],
                ':sha256'            => $row['sha256'],
                ':size'              => (int)($row['size'] ?? 0),
                ':mime'              => $row['mime'],
                ':original_filename' => $row['original_filename'],
            ]);
        }
        $pdo->commit();
    } catch (PDOException $e) {
        $pdo->rollBack();
        fwrite(STDERR, "ERROR: Attachment import failed at offset $offset: " . $e->getMessage() . "\n");
        exit(1);
    }

    $attsImported += count($rows);
    $offset += $chunkSize;

    if (!$quiet) {
        fwrite(STDOUT, "  attachments: $attsImported / $totalAtts\n");
    }
}

if (!$quiet) {
    fwrite(STDOUT, "==> Done. emails=$emailsImported attachments=$attsImported\n");
}
exit(0);
