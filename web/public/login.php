<?php
declare(strict_types=1);

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

// Already authenticated → go straight to archive
if ($sm->isAuthenticated()) {
    header('Location: /index.php', true, 302);
    exit;
}

$csrf  = new \MailReview\Auth\CsrfGuard();
$error = '';

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!$csrf->validateToken((string)($_POST['csrf_token'] ?? ''))) {
        $error = 'Ungültige Sitzung. Bitte die Seite neu laden.';
    } else {
        $password = (string)($_POST['password'] ?? '');
        $name     = trim((string)($_POST['name'] ?? ''));

        $coworkerHash = (string)($config['coworker_password_hash'] ?? '');
        $adminHash    = (string)($config['admin_password_hash']    ?? '');

        if ($adminHash !== '' && password_verify($password, $adminHash)) {
            $sm->login('admin', $password, $adminHash, $name ?: 'Admin');
            header('Location: /index.php', true, 302);
            exit;
        } elseif ($coworkerHash !== '' && password_verify($password, $coworkerHash)) {
            if ($name === '') {
                $error = 'Bitte einen Namen eingeben.';
            } else {
                $sm->login('coworker', $password, $coworkerHash, $name);
                header('Location: /index.php', true, 302);
                exit;
            }
        } else {
            // Constant-time delay to slow brute-force
            usleep(300000);
            $error = 'Falsches Passwort.';
        }
    }
}

$csrfToken = htmlspecialchars($csrf->getToken(), ENT_QUOTES, 'UTF-8');
?><!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mrija Archiv — Anmeldung</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#e8e8e8;font-family:system-ui,-apple-system,sans-serif;
  display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#1a1a1a;border:1px solid #2a2a2a;border-radius:10px;padding:2.5rem;
  width:100%;max-width:360px}
h1{font-size:1.15rem;font-weight:600;color:#c8c8c8;margin-bottom:1.75rem;text-align:center}
label{display:block;font-size:.78rem;color:#888;margin-bottom:.35rem}
input[type=text],input[type=password]{
  width:100%;background:#111;border:1px solid #2e2e2e;border-radius:6px;
  color:#e8e8e8;font-size:.9rem;padding:.55rem .75rem;outline:none;
  transition:border-color .15s}
input:focus{border-color:#5a7fa8}
.field{margin-bottom:1.1rem}
button{width:100%;background:#3a5a7a;border:none;border-radius:6px;color:#e8e8e8;
  cursor:pointer;font-size:.9rem;padding:.65rem;margin-top:.5rem;
  transition:background .15s}
button:hover{background:#4a6a8a}
.error{background:#2a1a1a;border:1px solid #5a3a3a;border-radius:6px;
  color:#c0606a;font-size:.8rem;padding:.65rem .8rem;margin-bottom:1rem;text-align:center}
.note{color:#555;font-size:.72rem;text-align:center;margin-top:1.25rem;line-height:1.5}
</style>
</head>
<body>
<div class="card">
  <h1>Mrija Archiv</h1>
  <?php if ($error !== ''): ?>
  <div class="error"><?= htmlspecialchars($error, ENT_QUOTES, 'UTF-8') ?></div>
  <?php endif; ?>
  <form method="post" action="/login.php" autocomplete="off">
    <input type="hidden" name="csrf_token" value="<?= $csrfToken ?>">
    <div class="field">
      <label for="name">Ihr Name</label>
      <input type="text" id="name" name="name" placeholder="z.B. Andrij"
             value="<?= htmlspecialchars((string)($_POST['name'] ?? ''), ENT_QUOTES, 'UTF-8') ?>"
             maxlength="80" autocomplete="name">
    </div>
    <div class="field">
      <label for="password">Passwort</label>
      <input type="password" id="password" name="password" placeholder="••••••••"
             autocomplete="current-password">
    </div>
    <button type="submit">Anmelden</button>
  </form>
  <p class="note">Zugang nur für autorisierte Mitarbeiter.</p>
</div>
</body>
</html>
