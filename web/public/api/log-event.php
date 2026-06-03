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

$doLogUrl   = rtrim((string)($config['do_log_url']   ?? ''), '/');
$doLogToken = (string)($config['do_log_token'] ?? '');

// No-op if not configured (admin / local-dev)
if ($doLogUrl === '' || $doLogToken === '') {
    http_response_code(204);
    exit;
}

$body = (string)file_get_contents('php://input');
$data = json_decode($body, true);
if (!is_array($data)) { http_response_code(400); exit; }

// Enrich with server-side context
$data['user']       = $sm->getDisplayName();
$data['role']       = $sm->getRole();
$data['session_ts'] = gmdate('c');

$enriched = json_encode($data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);

if (function_exists('curl_init')) {
    $ch = curl_init($doLogUrl . '/log');
    curl_setopt_array($ch, [
        CURLOPT_POST           => true,
        CURLOPT_POSTFIELDS     => $enriched,
        CURLOPT_HTTPHEADER     => [
            'Content-Type: application/json',
            'Authorization: Bearer ' . $doLogToken,
        ],
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => 2,
    ]);
    @curl_exec($ch);
    curl_close($ch);
} else {
    $ctx = stream_context_create([
        'http' => [
            'method'  => 'POST',
            'header'  => "Content-Type: application/json\r\nAuthorization: Bearer $doLogToken\r\n",
            'content' => $enriched,
            'timeout' => 2,
            'ignore_errors' => true,
        ],
    ]);
    @file_get_contents($doLogUrl . '/log', false, $ctx);
}

http_response_code(204);
