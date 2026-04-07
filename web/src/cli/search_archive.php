<?php
/**
 * web/src/cli/search_archive.php — Search the mail archive via MySQL FULLTEXT.
 *
 * Usage: php search_archive.php --query <text> [--mailbox <name>] [--limit <n>]
 */
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    fwrite(STDERR, "CLI only.\n");
    exit(1);
}

$opts = getopt('', ['query:', 'mailbox:', 'limit:', 'config:', 'help', 'h']);

if (isset($opts['help']) || isset($opts['h']) || !isset($opts['query'])) {
    fwrite(STDOUT, <<<USAGE
    Usage: php search_archive.php --query <text> [OPTIONS]

      Search all archived emails using MySQL FULLTEXT (subject, from, to, cc, body).

    Options:
      --query <text>      Search terms (required). Supports MySQL boolean operators.
      --mailbox <name>    Restrict to one mailbox (optional).
      --limit <n>         Maximum results to show (default: 50).
      --config <path>     Path to local.php (default: web/config/local.php).
      --help              Show this message.

    Examples:
      php search_archive.php --query "invoice"
      php search_archive.php --query "invoice" --mailbox gabriel.hangel --limit 20

    USAGE);
    exit(isset($opts['query']) ? 1 : 0);
}

$scriptDir  = dirname(__DIR__, 2);
$configPath = $opts['config'] ?? ($scriptDir . '/config/local.php');

if (!is_file($configPath)) {
    fwrite(STDERR, "ERROR: Config not found: $configPath\n");
    exit(1);
}

/** @var array<string,mixed> $config */
$config   = require $configPath;
$dbCfg    = $config['db'] ?? [];
$query    = (string) $opts['query'];
$mailbox  = isset($opts['mailbox']) ? (string) $opts['mailbox'] : null;
$limit    = max(1, (int) ($opts['limit'] ?? 50));

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
    ]);
} catch (PDOException $e) {
    fwrite(STDERR, "ERROR: DB connection failed: " . $e->getMessage() . "\n");
    exit(1);
}

// ── Build and execute query ────────────────────────────────────────────────
$sql = <<<SQL
    SELECT
        e.mailbox,
        e.stable_id,
        e.date,
        e.from_addr,
        e.to_addrs,
        e.subject,
        LEFT(e.body_text, 200)   AS body_preview,
        GROUP_CONCAT(
            a.original_filename
            ORDER BY a.original_filename
            SEPARATOR '; '
        )                        AS attachments
    FROM archive_emails e
    LEFT JOIN archive_attachments a
           ON a.mailbox = e.mailbox
          AND a.email_stable_id = e.stable_id
    WHERE MATCH(e.subject, e.from_addr, e.to_addrs, e.cc_addrs, e.body_text)
          AGAINST (? IN BOOLEAN MODE)
SQL;

$params = [$query];
if ($mailbox !== null) {
    $sql    .= " AND e.mailbox = ?";
    $params[] = $mailbox;
}
$sql .= " GROUP BY e.mailbox, e.stable_id ORDER BY e.date DESC LIMIT ?";
$params[] = $limit;

$stmt = $pdo->prepare($sql);
$stmt->execute($params);
$rows = $stmt->fetchAll();

// ── Print results ──────────────────────────────────────────────────────────
if (!$rows) {
    fwrite(STDOUT, "No results for: $query\n");
    exit(0);
}

foreach ($rows as $row) {
    $preview = trim(preg_replace('/\s+/', ' ', $row['body_preview'] ?? ''));
    fwrite(STDOUT, "[{$row['mailbox']}] {$row['date']}  From: {$row['from_addr']}\n");
    fwrite(STDOUT, "  Subject: {$row['subject']}\n");
    if ($row['to_addrs']) {
        fwrite(STDOUT, "  To: {$row['to_addrs']}\n");
    }
    if ($row['attachments']) {
        fwrite(STDOUT, "  Attachments: {$row['attachments']}\n");
    }
    if ($preview) {
        fwrite(STDOUT, "  Preview: " . mb_substr($preview, 0, 120) . "\n");
    }
    fwrite(STDOUT, "\n");
}

fwrite(STDOUT, count($rows) . " result(s) for: $query\n");
exit(0);
