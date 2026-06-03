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

header('Content-Type: application/json; charset=utf-8');

$updateUrl = rtrim((string)($config['update_server_url'] ?? ''), '/');
if ($updateUrl === '') {
    echo json_encode(['available' => false, 'reason' => 'no_update_server']);
    exit;
}

// Cache in session for 5 minutes
$cacheKey = '_update_manifest_cache';
$cacheTs  = '_update_manifest_ts';
$now      = time();
if (
    isset($_SESSION[$cacheKey], $_SESSION[$cacheTs])
    && ($now - (int)$_SESSION[$cacheTs]) < 300
) {
    echo json_encode(['available' => true, 'cached' => true, 'manifest' => $_SESSION[$cacheKey]]);
    exit;
}

$ctx = stream_context_create(['http' => ['timeout' => 8, 'ignore_errors' => true]]);
$raw = @file_get_contents($updateUrl . '/updates/manifest.json', false, $ctx);
if ($raw === false || !is_string($raw)) {
    echo json_encode(['available' => false, 'reason' => 'fetch_failed']);
    exit;
}
$manifest = json_decode($raw, true);
if (!is_array($manifest) || empty($manifest['version'])) {
    echo json_encode(['available' => false, 'reason' => 'invalid_manifest']);
    exit;
}

$_SESSION[$cacheKey] = $manifest;
$_SESSION[$cacheTs]  = $now;

echo json_encode(['available' => true, 'cached' => false, 'manifest' => $manifest]);
