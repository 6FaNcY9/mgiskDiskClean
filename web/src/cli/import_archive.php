<?php
/**
 * web/src/cli/import_archive.php — Import global SQLite index into MySQL.
 *
 * Reads all rows from the global mail_index.sqlite and upserts them into
 * archive_emails and archive_attachments in MySQL.
 *
 * Idempotent: re-running is safe (INSERT ... ON DUPLICATE KEY UPDATE).
 * Chunked commits every 5000 rows.
 *
 * Usage: php import_archive.php [--sqlite <path>] [--config <path>] [--help]
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "CLI only.\n");
    exit(1);
}

$opts = getopt('', ['sqlite:', 'config:', 'help', 'h']);

if (isset($opts['help']) || isset($opts['h'])) {
    fwrite(STDOUT, <<<USAGE
    Usage: php import_archive.php [OPTIONS]

      Import the global SQLite mail index into MySQL archive tables.
      Idempotent — safe to re-run.

    Options:
      --sqlite <path>   Path to global SQLite index
                        (default: <data_dir>/index/mail_index.sqlite)
      --config <path>   Path to local.php config
                        (default: web/config/local.php)
      --help            Show this message

    Exit codes:
      0  Success
      1  Error

    USAGE);
    exit(0);
}

$scriptDir  = dirname(__DIR__, 2); // web/
$configPath = $opts['config'] ?? ($scriptDir . '/config/local.php');

if (!is_file($configPath)) {
    fwrite(STDERR, "ERROR: Config not found: $configPath\n");
    fwrite(STDERR, "  Copy web/config/local.php.example -> web/config/local.php\n");
    exit(1);
}

/** @var array<string,mixed> $config */
$config  = require $configPath;
$dbCfg   = $config['db']       ?? [];
$dataDir = rtrim($config['data_dir'] ?? '', '/');

$sqlitePath = $opts['sqlite'] ?? ($dataDir . '/index/mail_index.sqlite');

if (!file_exists($sqlitePath)) {
    fwrite(STDERR, "ERROR: SQLite index not found: $sqlitePath\n");
    fwrite(STDERR, "  Run: sync-all (or index-mailbox) first.\n");
    exit(1);
}

// ── MySQL connection ───────────────────────────────────────────────────────
$socket = $dbCfg['socket'] ?? '';
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

// ── SQLite (read-only) ─────────────────────────────────────────────────────
$sqlite = new SQLite3($sqlitePath, SQLITE3_OPEN_READONLY);
$sqlite->busyTimeout(5000);

// ── Import emails ──────────────────────────────────────────────────────────
$stmtEmail = $pdo->prepare(<<<SQL
    INSERT INTO archive_emails
        (mailbox, stable_id, filepath, folder, date, from_addr,
         to_addrs, cc_addrs, subject, body_text, total_size_bytes)
    VALUES (?,?,?,?,?,?,?,?,?,?,?)
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

$chunk       = 0;
$totalEmails = 0;
$pdo->beginTransaction();

$res = $sqlite->query(
    "SELECT mailbox, stable_id, filepath, folder, date, from_addr,
            to_addrs, cc_addrs, subject, body_text, total_size_bytes
     FROM emails"
);
while ($row = $res->fetchArray(SQLITE3_ASSOC)) {
    $stmtEmail->execute([
        $row['mailbox'],
        $row['stable_id'],
        $row['filepath'],
        $row['folder'],
        $row['date'],
        $row['from_addr'],
        $row['to_addrs']   ?? '',
        $row['cc_addrs']   ?? '',
        $row['subject'],
        $row['body_text']  ?? '',
        (int) $row['total_size_bytes'],
    ]);
    $chunk++;
    $totalEmails++;
    if ($chunk >= 5000) {
        $pdo->commit();
        $pdo->beginTransaction();
        $chunk = 0;
        fwrite(STDOUT, "  ... $totalEmails emails\n");
    }
}
$pdo->commit();
fwrite(STDOUT, "Emails imported: $totalEmails\n");

// ── Import attachments (JOIN emails to resolve mailbox) ────────────────────
$stmtAtt = $pdo->prepare(<<<SQL
    INSERT INTO archive_attachments
        (mailbox, email_stable_id, sha256, size, mime, original_filename, stored_path)
    VALUES (?,?,?,?,?,?,?)
    ON DUPLICATE KEY UPDATE
        stored_path       = VALUES(stored_path),
        size              = VALUES(size),
        mime              = VALUES(mime),
        original_filename = VALUES(original_filename)
SQL);

$chunk    = 0;
$totalAtt = 0;
$pdo->beginTransaction();

$res2 = $sqlite->query(<<<SQL
    SELECT e.mailbox,
           a.email_stable_id,
           a.sha256,
           a.size,
           a.mime,
           a.original_filename,
           a.stored_path
    FROM attachments a
    JOIN emails e ON e.stable_id = a.email_stable_id
SQL);
while ($row = $res2->fetchArray(SQLITE3_ASSOC)) {
    $stmtAtt->execute([
        $row['mailbox'],
        $row['email_stable_id'],
        $row['sha256'],
        (int) $row['size'],
        $row['mime'],
        $row['original_filename'],
        $row['stored_path'],
    ]);
    $chunk++;
    $totalAtt++;
    if ($chunk >= 5000) {
        $pdo->commit();
        $pdo->beginTransaction();
        $chunk = 0;
        fwrite(STDOUT, "  ... $totalAtt attachments\n");
    }
}
$pdo->commit();
fwrite(STDOUT, "Attachments imported: $totalAtt\n");

$sqlite->close();
fwrite(STDOUT, "==> Import complete.\n");
exit(0);
