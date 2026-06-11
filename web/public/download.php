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

// ── Config + Auth ─────────────────────────────────────────────────────────────
$cfgPath = __DIR__ . '/../config/local.php';
if (!is_file($cfgPath)) { http_response_code(500); die('Config missing.'); }
$config = require $cfgPath;

spl_autoload_register(function (string $c): void {
    $map = [
        'MailReview\\Auth\\SessionManager' => __DIR__ . '/../src/Auth/SessionManager.php',
        'MailReview\\Auth\\CsrfGuard'      => __DIR__ . '/../src/Auth/CsrfGuard.php',
    ];
    if (isset($map[$c])) require_once $map[$c];
});
$sm = new \MailReview\Auth\SessionManager($config['session'] ?? []);
$sm->start();

$authEnabled = $config['auth']['enabled'] ?? true;
if ($authEnabled) {
    $sm->requireAuth('/login.php');
}

$db     = $config['db'] ?? [];
$engine = $db['engine'] ?? 'mysql';
$socket = $db['socket'] ?? '';

if ($engine === 'sqlite') {
    $dsn = "sqlite:" . ($db['path'] ?? ':memory:');
} elseif ($socket && file_exists($socket)) {
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
$mailbox  = trim((string)($_GET['mailbox'] ?? ''));
$sha256   = trim((string)($_GET['sha256']  ?? ''));
$isInline = ($_GET['inline'] ?? '') === '1';
$bypassVt = ($_GET['bypass_vt'] ?? '') === '1';

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
$allowedBase = $allowedBase === false ? false : rtrim($allowedBase, DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR;

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

// RFC 6266 dual encoding for Content-Disposition filename (handles non-ASCII)
$asciiFallback = preg_replace('/[^\x20-\x7E]+/', '_', $originalName);
$utf8Encoded   = rawurlencode($originalName);

// ── VirusTotal gate (applies to ALL downloads, including inline) ──────────
$vtApiKey = $config['vt_api_key'] ?? '';
if ($vtApiKey !== '' && !$bypassVt) {
    spl_autoload_register(function (string $class): void {
        $map = ['MailReview\\VirusTotal\\VtService' => __DIR__ . '/../src/VirusTotal/VtService.php'];
        if (isset($map[$class])) require_once $map[$class];
    });
    $vt    = new \MailReview\VirusTotal\VtService($pdo, $vtApiKey, $dataDir);
    $vtRes = $vt->check($sha256, $realPath);

    if ($vtRes['status'] === 'infected') {
        http_response_code(403);
        if ($isInline) {
            // Return a minimal blocked placeholder for iframe/img contexts
            header('Content-Type: text/html; charset=utf-8');
            echo '<html><body style="background:#1a1a1a;color:#c0606a;font-family:sans-serif;'
               . 'display:flex;align-items:center;justify-content:center;height:100vh;margin:0">'
               . '<div style="text-align:center"><div style="font-size:2rem">&#9888;</div>'
               . '<p style="font-size:.8rem">Blocked by VirusTotal</p></div></body></html>';
        } else {
            echo '<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<title>Datei blockiert</title>
<style>body{background:#0d0d0d;color:#e8e8e8;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{text-align:center;max-width:400px}.icon{font-size:3rem;margin-bottom:1rem}
h1{color:#c0606a;font-size:1.1rem;margin-bottom:.5rem}
p{color:#888;font-size:.85rem;line-height:1.6}</style></head>
<body><div class="box"><div class="icon">&#9888;</div>
<h1>Datei blockiert</h1>
<p>VirusTotal hat in dieser Datei Schadsoftware erkannt (' . (int)$vtRes['positives'] . ' Treffer).<br>
Der Download wurde gesperrt.</p></div></body></html>';
        }
        exit;
    }

    if ($vtRes['status'] === 'pending') {
        http_response_code(202);
        if ($isInline) {
            header('Content-Type: text/html; charset=utf-8');
            echo '<html><body style="background:#1a1a1a;color:#888;font-family:sans-serif;'
               . 'display:flex;align-items:center;justify-content:center;height:100vh;margin:0">'
               . '<div style="text-align:center"><div style="font-size:2rem">&#8987;</div>'
               . '<p style="font-size:.8rem">Scanning…</p></div></body></html>';
        } else {
            echo '<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<meta http-equiv="refresh" content="4">
<title>Wird geprüft…</title>
<style>body{background:#0d0d0d;color:#e8e8e8;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{text-align:center;max-width:400px}.icon{font-size:2.5rem;margin-bottom:1rem}
h1{color:#888;font-size:1rem;margin-bottom:.5rem}
p{color:#555;font-size:.8rem}</style></head>
<body><div class="box"><div class="icon">&#8987;</div>
<h1>Wird von VirusTotal geprüft…</h1>
<p>Diese Seite lädt automatisch neu. Bitte warten.</p></div></body></html>';
        }
        exit;
    }
    // status: clean, error, or disabled → fall through and serve
}

// ── Serve file ────────────────────────────────────────────────────────────
header('Content-Type: ' . $mime);
if ($isInline) {
    header("Content-Disposition: inline; filename=\"$asciiFallback\"; filename*=UTF-8''$utf8Encoded");
} else {
    header("Content-Disposition: attachment; filename=\"$asciiFallback\"; filename*=UTF-8''$utf8Encoded");
}
header('Content-Length: ' . filesize($realPath));
header('Cache-Control: private, max-age=3600');
header('X-Content-Type-Options: nosniff');
readfile($realPath);
exit;
