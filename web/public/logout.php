<?php
declare(strict_types=1);

$cfgPath = __DIR__ . '/../config/local.php';
if (!is_file($cfgPath)) { http_response_code(500); die('Config missing.'); }
$config = require $cfgPath;

spl_autoload_register(function (string $c): void {
    $map = ['MailReview\\Auth\\SessionManager' => __DIR__ . '/../src/Auth/SessionManager.php'];
    if (isset($map[$c])) require_once $map[$c];
});
$sm = new \MailReview\Auth\SessionManager($config['session'] ?? []);
$sm->start();
$sm->logout();

header('Location: /login.php', true, 302);
exit;
