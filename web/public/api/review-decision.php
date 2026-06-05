<?php
declare(strict_types=1);

$cfgPath = __DIR__ . '/../../config/local.php';
if (!is_file($cfgPath)) {
    http_response_code(500);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => 'config_missing']);
    exit;
}
$config = require $cfgPath;

spl_autoload_register(function (string $c): void {
    $map = [
        'MailReview\\Auth\\SessionManager' => __DIR__ . '/../../src/Auth/SessionManager.php',
        'MailReview\\Auth\\CsrfGuard'      => __DIR__ . '/../../src/Auth/CsrfGuard.php',
    ];
    if (isset($map[$c])) require_once $map[$c];
});

$sm = new \MailReview\Auth\SessionManager($config['session'] ?? []);
$sm->start();
$authEnabled = $config['auth']['enabled'] ?? true;
if ($authEnabled && !$sm->isAuthenticated()) {
    http_response_code(401);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => 'unauthorized']);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    header('Allow: POST');
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => 'method_not_allowed']);
    exit;
}

$csrf = new \MailReview\Auth\CsrfGuard();
if ($authEnabled) {
    $csrf->enforce();
}

$body = (string)file_get_contents('php://input');
$data = json_decode($body, true);
if (!is_array($data)) {
    http_response_code(400);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => 'invalid_json']);
    exit;
}

$mailbox = trim((string)($data['mailbox'] ?? ''));
$emailId = trim((string)($data['email_stable_id'] ?? ''));
$decision = trim((string)($data['decision'] ?? ''));
$notes = trim((string)($data['notes'] ?? ''));

if (!preg_match('/^[a-zA-Z0-9._-]+$/', $mailbox)) {
    http_response_code(400);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => 'invalid_mailbox']);
    exit;
}
if (!preg_match('/^[a-f0-9]{64}$/', $emailId)) {
    http_response_code(400);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => 'invalid_email_stable_id']);
    exit;
}
if (!in_array($decision, ['keep', 'delete', 'unsure'], true)) {
    http_response_code(400);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => 'invalid_decision']);
    exit;
}
if (strlen($notes) > 5000) {
    http_response_code(400);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => 'notes_too_long']);
    exit;
}

$db = $config['db'] ?? [];
$engine = $db['engine'] ?? 'mysql';
$socket = $db['socket'] ?? '';
if ($engine === 'sqlite') {
    $dsn = 'sqlite:' . ($db['path'] ?? ':memory:');
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

    $exists = $pdo->prepare(
        'SELECT 1 FROM archive_emails WHERE mailbox = ? AND stable_id = ? LIMIT 1'
    );
    $exists->execute([$mailbox, $emailId]);
    if (!$exists->fetchColumn()) {
        http_response_code(404);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode(['ok' => false, 'error' => 'email_not_found']);
        exit;
    }

    $role = $sm->getRole();
    $name = $sm->getDisplayName();
    if ($engine === 'sqlite') {
        $stmt = $pdo->prepare(
            "INSERT INTO review_decisions
             (mailbox, email_stable_id, decision, notes, reviewer_role, reviewer_name, decided_at, updated_at)
             VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
             ON CONFLICT(mailbox, email_stable_id) DO UPDATE SET
               decision = excluded.decision,
               notes = excluded.notes,
               reviewer_role = excluded.reviewer_role,
               reviewer_name = excluded.reviewer_name,
               updated_at = CURRENT_TIMESTAMP"
        );
    } else {
        $stmt = $pdo->prepare(
            "INSERT INTO review_decisions
             (mailbox, email_stable_id, decision, notes, reviewer_role, reviewer_name)
             VALUES (?, ?, ?, ?, ?, ?)
             ON DUPLICATE KEY UPDATE
               decision = VALUES(decision),
               notes = VALUES(notes),
               reviewer_role = VALUES(reviewer_role),
               reviewer_name = VALUES(reviewer_name),
               updated_at = CURRENT_TIMESTAMP"
        );
    }
    $stmt->execute([$mailbox, $emailId, $decision, $notes, $role, $name]);
} catch (PDOException $e) {
    http_response_code(500);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['ok' => false, 'error' => 'database_error']);
    exit;
}

header('Content-Type: application/json; charset=utf-8');
echo json_encode([
    'ok' => true,
    'decision' => $decision,
    'notes' => $notes,
]);
