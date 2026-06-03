<?php
declare(strict_types=1);

$cfgPath = __DIR__ . '/../../config/local.php';
if (!is_file($cfgPath)) { http_response_code(500); exit; }
$config = require $cfgPath;

spl_autoload_register(function (string $c): void {
    $map = ['MailReview\\Auth\\SessionManager' => __DIR__ . '/../../src/Auth/SessionManager.php'];
    if (isset($map[$c])) require_once $map[$c];
});
$sm = new \MailReview\Auth\SessionManager($config['session'] ?? []);
$sm->start();
if (!$sm->isAuthenticated()) { http_response_code(401); exit; }
if ($_SERVER['REQUEST_METHOD'] !== 'POST') { http_response_code(405); exit; }

header('Content-Type: application/json; charset=utf-8');

$updateUrl = rtrim((string)($config['update_server_url'] ?? ''), '/');
if ($updateUrl === '') {
    http_response_code(503);
    echo json_encode(['ok' => false, 'error' => 'no_update_server']);
    exit;
}

$body     = (string)file_get_contents('php://input');
$payload  = json_decode($body, true);
$manifest = is_array($payload) ? ($payload['manifest'] ?? null) : null;

// Fetch manifest fresh if not supplied in body
if (!is_array($manifest) || empty($manifest['sha256'])) {
    $ctx = stream_context_create(['http' => ['timeout' => 8]]);
    $raw = @file_get_contents($updateUrl . '/updates/manifest.json', false, $ctx);
    $manifest = $raw ? json_decode($raw, true) : null;
}

if (!is_array($manifest) || empty($manifest['filename']) || empty($manifest['sha256'])) {
    http_response_code(422);
    echo json_encode(['ok' => false, 'error' => 'no_manifest']);
    exit;
}

$filename = basename($manifest['filename']);
if (!preg_match('/^mrija-[\dT]+Z\.sql\.gz$/', $filename)) {
    http_response_code(422);
    echo json_encode(['ok' => false, 'error' => 'invalid_filename']);
    exit;
}

$dumpUrl = $updateUrl . '/updates/' . $filename;
$tmpFile = sys_get_temp_dir() . '/' . $filename;

// Download dump
$ctx = stream_context_create(['http' => ['timeout' => 120]]);
$bytes = @file_get_contents($dumpUrl, false, $ctx);
if ($bytes === false) {
    http_response_code(502);
    echo json_encode(['ok' => false, 'error' => 'download_failed']);
    exit;
}
file_put_contents($tmpFile, $bytes);
unset($bytes);

// Verify SHA-256
$actual = hash_file('sha256', $tmpFile);
if (!hash_equals($manifest['sha256'], (string)$actual)) {
    @unlink($tmpFile);
    http_response_code(422);
    echo json_encode(['ok' => false, 'error' => 'sha256_mismatch']);
    exit;
}

$db   = $config['db'] ?? [];
$host = escapeshellarg($db['host'] ?? '127.0.0.1');
$port = (int)($db['port'] ?? 3306);
$user = escapeshellarg($db['user'] ?? 'mailreview');
$pass = escapeshellarg($db['password'] ?? '');
$name = escapeshellarg($db['dbname'] ?? 'mailreview');

set_time_limit(300);

$cmd = "zcat " . escapeshellarg($tmpFile)
     . " | mysql -h $host -P $port -u $user -p$pass $name 2>&1";

$output   = [];
$exitCode = 0;
exec($cmd, $output, $exitCode);
@unlink($tmpFile);

// Clear the manifest session cache so next check-update returns fresh data
unset($_SESSION['_update_manifest_cache'], $_SESSION['_update_manifest_ts']);

if ($exitCode !== 0) {
    http_response_code(500);
    echo json_encode(['ok' => false, 'error' => 'mysql_failed', 'detail' => implode("\n", $output)]);
    exit;
}

echo json_encode(['ok' => true, 'version' => $manifest['version']]);
