<?php
/**
 * web/public/download.php — Secure attachment download endpoint.
 *
 * Usage: GET /download.php?mailbox=<name>&sha256=<hex64>
 *
 * Looks up the attachment in MariaDB, resolves the file path under
 * data/mailboxes/<mailbox>/attachments/, and streams it to the browser.
 */
declare(strict_types=1);

// ── Config + DB ───────────────────────────────────────────────────────────────
$cfgPath = __DIR__ . '/../config/local.php';
if (!is_file($cfgPath)) { http_response_code(500); die('Config missing.'); }
$config = require $cfgPath;

$db     = $config['db'] ?? [];
$socket = $db['socket'] ?? '';
if ($socket && file_exists($socket)) {
    $dsn = "mysql:unix_socket=$socket;dbname={$db['dbname']};charset={$db['charset']}";
} else {
    $host = $db['host'] ?? '127.0.0.1';
    $port = $db['port'] ?? 3306;
    $dsn  = "mysql:host=$host;port=$port;dbname={$db['dbname']};charset={$db['charset']}";
}
try {
    $pdo = new PDO($dsn, $db['user'] ?? '', $db['password'] ?? '', [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
} catch (PDOException $e) {
    http_response_code(503); die('Database unavailable.');
}

// ── Input validation ──────────────────────────────────────────────────────────
$mailbox = trim((string)($_GET['mailbox'] ?? ''));
$sha256  = trim((string)($_GET['sha256']  ?? ''));

if (!preg_match('/^[a-zA-Z0-9._-]+$/', $mailbox)) {
    http_response_code(400); die('Invalid mailbox.');
}
if (!preg_match('/^[a-f0-9]{64}$/', $sha256)) {
    http_response_code(400); die('Invalid sha256.');
}

// ── Look up attachment in DB ──────────────────────────────────────────────────
$stmt = $pdo->prepare(
    "SELECT stored_path, original_filename, mime, size
     FROM archive_attachments
     WHERE mailbox = ? AND sha256 = ?
     LIMIT 1"
);
$stmt->execute([$mailbox, $sha256]);
$att = $stmt->fetch();

if (!$att) {
    http_response_code(404); die('Attachment not found.');
}

// ── Resolve file path safely ──────────────────────────────────────────────────
// data_dir may be /app/data (Docker) or $DEVENV_ROOT/data (devenv)
$dataDir = rtrim((string)($config['data_dir'] ?? (getenv('DEVENV_ROOT') ? getenv('DEVENV_ROOT') . '/data' : '/app/data')), '/');

$filename    = basename($att['stored_path']);
$filePath    = $dataDir . '/mailboxes/' . $mailbox . '/attachments/' . $filename;
$realPath    = realpath($filePath);
$allowedBase = realpath($dataDir . '/mailboxes/' . $mailbox . '/attachments');

// Path traversal guard
if ($realPath === false || $allowedBase === false || strpos($realPath, $allowedBase) !== 0) {
    http_response_code(403); die('Access denied.');
}
if (!is_file($realPath)) {
    http_response_code(404); die('File not found on disk.');
}

// ── Stream file ───────────────────────────────────────────────────────────────
$originalName = $att['original_filename'] ?: $filename;
$mime         = $att['mime'] ?: 'application/octet-stream';

// Only allow safe inline display for images and PDFs; force download otherwise
$inlineMimes = ['image/jpeg','image/png','image/gif','image/webp','image/svg+xml','application/pdf'];
$disposition = in_array($mime, $inlineMimes, true) ? 'inline' : 'attachment';

header('Content-Type: ' . $mime);
header('Content-Disposition: ' . $disposition . '; filename="' . addslashes($originalName) . '"');
header('Content-Length: ' . filesize($realPath));
header('Cache-Control: private, max-age=3600');
header('X-Content-Type-Options: nosniff');

readfile($realPath);
exit;
