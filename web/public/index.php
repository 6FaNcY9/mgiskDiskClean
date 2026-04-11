<?php
/**
 * web/public/index.php — Mrija Archive search UI.
 * Served by: php -S 0.0.0.0:8080 -t /app/web/public
 */
declare(strict_types=1);

// ── DB connection ─────────────────────────────────────────────────────────────
$cfgPath = __DIR__ . '/../config/local.php';
if (!is_file($cfgPath)) {
    die('<p style="color:#f87171;font-family:sans-serif;padding:2rem">Config not found. Ensure local.php exists in web/config/.</p>');
}
/** @var array<string,mixed> $config */
$config = require $cfgPath;
$db = $config['db'] ?? [];
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
    echo '<!DOCTYPE html><html><body style="background:#111827;color:#f87171;font-family:sans-serif;padding:3rem;text-align:center">';
    echo '<h2>Database starting up…</h2><p>Please wait a moment and refresh the page.</p>';
    echo '<meta http-equiv="refresh" content="3">';
    echo '</body></html>';
    exit;
}

// ── Input ─────────────────────────────────────────────────────────────────────
$q          = trim((string)($_GET['q']      ?? ''));
$mailbox    = trim((string)($_GET['mailbox'] ?? ''));
$selectedId = trim((string)($_GET['id']     ?? ''));
$selMailbox = trim((string)($_GET['smb']    ?? ''));

// ── Data ──────────────────────────────────────────────────────────────────────
$mailboxes = $pdo->query("SELECT DISTINCT mailbox FROM archive_emails ORDER BY mailbox")
                 ->fetchAll(PDO::FETCH_COLUMN);
$total     = (int) $pdo->query("SELECT COUNT(*) FROM archive_emails")->fetchColumn();
$lastImport = $pdo->query(
    "SELECT MAX(imported_at) FROM archive_emails"
)->fetchColumn() ?: '—';

$results = [];
if ($q !== '') {
    $sql    = "SELECT mailbox, stable_id, date, from_addr, subject,
                      LEFT(body_text, 160) AS preview
               FROM archive_emails
               WHERE MATCH(subject, from_addr, to_addrs, cc_addrs, body_text)
                     AGAINST (? IN BOOLEAN MODE)";
    $params = [$q];
    if ($mailbox !== '') {
        $sql    .= " AND mailbox = ?";
        $params[] = $mailbox;
    }
    $sql .= " ORDER BY date DESC LIMIT 50";
    $stmt = $pdo->prepare($sql);
    $stmt->execute($params);
    $results = $stmt->fetchAll();
}

$email = null;
if ($selectedId !== '' && $selMailbox !== '') {
    $stmt = $pdo->prepare("SELECT * FROM archive_emails WHERE stable_id = ? AND mailbox = ?");
    $stmt->execute([$selectedId, $selMailbox]);
    $email = $stmt->fetch() ?: null;
    if ($email) {
        $stmt2 = $pdo->prepare("SELECT * FROM archive_attachments WHERE email_stable_id = ? AND mailbox = ?");
        $stmt2->execute([$selectedId, $selMailbox]);
        $email['attachments'] = $stmt2->fetchAll();
    }
}

function esc(string $s): string { return htmlspecialchars($s, ENT_QUOTES, 'UTF-8'); }
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mrija Archive</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#111827;color:#d1d5db;font-family:system-ui,-apple-system,sans-serif;height:100vh;display:flex;flex-direction:column;overflow:hidden}

/* Toolbar */
#toolbar{background:#1e1b4b;padding:.5rem 1rem;display:flex;align-items:center;gap:.75rem;border-bottom:1px solid #312e81;flex-shrink:0}
#toolbar .logo{font-size:1.1rem}
#toolbar .title{color:#e0e7ff;font-weight:600;font-size:.9rem}
#search-form{flex:1;display:flex;gap:.5rem;margin:0 1rem}
#q{flex:1;background:#111827;border:1px solid #4f46e5;border-radius:6px;padding:.35rem .75rem;color:#e0e7ff;font-size:.85rem;outline:none}
#q:focus{border-color:#818cf8}
#mb-filter{background:#111827;border:1px solid #374151;color:#9ca3af;border-radius:6px;padding:.35rem .6rem;font-size:.8rem}
#search-btn{background:#4f46e5;color:#fff;border:none;border-radius:6px;padding:.35rem 1rem;font-size:.8rem;cursor:pointer}
#search-btn:hover{background:#4338ca}
.stop-btn{background:transparent;color:#9ca3af;border:1px solid #374151;border-radius:4px;padding:.2rem .6rem;font-size:.7rem;cursor:pointer;margin-left:auto}
.stop-btn:hover{color:#f87171;border-color:#f87171}

/* Main layout */
#main{display:flex;flex:1;overflow:hidden}

/* Results list */
#results{width:42%;border-right:1px solid #1f2937;overflow-y:auto;flex-shrink:0}
.result{padding:.65rem .8rem;border-bottom:1px solid #1f2937;cursor:pointer;border-left:3px solid transparent}
.result:hover{background:#1f2937}
.result.active{background:#1e1b4b;border-left-color:#6366f1}
.result .subj{color:#c7d2fe;font-size:.78rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.result.active .subj{color:#e0e7ff}
.result .meta{color:#6b7280;font-size:.68rem;margin-top:.15rem}
.result .preview{color:#4b5563;font-size:.65rem;margin-top:.2rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.result.active .preview{color:#6b7280}
.result-count{padding:.4rem .8rem;color:#4b5563;font-size:.68rem;text-align:center}
.empty-state{padding:3rem;text-align:center;color:#374151}
.empty-state .icon{font-size:2.5rem;margin-bottom:.75rem}

/* Email detail */
#detail{flex:1;padding:1.2rem;overflow-y:auto}
.detail-header{margin-bottom:1rem;padding-bottom:.75rem;border-bottom:1px solid #1f2937}
.detail-subject{color:#e0e7ff;font-size:1rem;font-weight:600;margin-bottom:.6rem}
.detail-meta{display:grid;grid-template-columns:auto 1fr;gap:.2rem .75rem;font-size:.75rem}
.detail-meta .label{color:#6366f1}
.detail-meta .val{color:#9ca3af}
.detail-body{color:#d1d5db;font-size:.82rem;line-height:1.65;white-space:pre-wrap;word-break:break-word}
.att-list{margin-top:1rem;display:flex;flex-wrap:wrap;gap:.4rem}
.att{display:inline-flex;align-items:center;gap:.35rem;background:#1f2937;border:1px solid #374151;border-radius:4px;padding:.3rem .6rem;font-size:.72rem;color:#9ca3af}

/* Status bar */
#statusbar{background:#111827;border-top:1px solid #1f2937;padding:.25rem 1rem;display:flex;justify-content:space-between;font-size:.65rem;color:#374151;flex-shrink:0}
</style>
</head>
<body>

<div id="toolbar">
  <span class="logo">📧</span>
  <span class="title">Mrija Archive</span>
  <form id="search-form" method="get" action="">
    <input id="q" name="q" type="text" value="<?= esc($q) ?>"
           placeholder="Search emails — subject, from, body…" autofocus>
    <select id="mb-filter" name="mailbox">
      <option value="">All mailboxes</option>
      <?php foreach ($mailboxes as $mb): ?>
        <option value="<?= esc($mb) ?>" <?= $mailbox === $mb ? 'selected' : '' ?>><?= esc($mb) ?></option>
      <?php endforeach ?>
    </select>
    <button id="search-btn" type="submit">Search</button>
  </form>
  <button class="stop-btn" onclick="if(window.pywebview){window.pywebview.api.stop_archive()}else{alert('Use the launcher to stop.')}">■ Stop</button>
</div>

<div id="main">
  <div id="results">
    <?php if ($q === ''): ?>
      <div class="empty-state">
        <div class="icon">🔍</div>
        <div style="color:#6b7280;font-size:.85rem">Search the archive above</div>
        <div style="color:#374151;font-size:.75rem;margin-top:.4rem"><?= number_format($total) ?> emails indexed</div>
      </div>
    <?php elseif (empty($results)): ?>
      <div class="empty-state">
        <div class="icon">📭</div>
        <div style="color:#6b7280;font-size:.85rem">No results for <em><?= esc($q) ?></em></div>
      </div>
    <?php else: ?>
      <?php foreach ($results as $r):
        $isActive = ($r['stable_id'] === $selectedId && $r['mailbox'] === $selMailbox);
        $link = '?' . http_build_query(['q' => $q, 'mailbox' => $mailbox, 'id' => $r['stable_id'], 'smb' => $r['mailbox']]);
      ?>
      <div class="result <?= $isActive ? 'active' : '' ?>" onclick="window.location='<?= esc($link) ?>'">
        <div class="subj"><?= esc($r['subject'] ?: '(no subject)') ?></div>
        <div class="meta"><?= esc($r['from_addr']) ?> · <?= esc(substr($r['date'], 0, 10)) ?> · <em><?= esc($r['mailbox']) ?></em></div>
        <div class="preview"><?= esc($r['preview']) ?></div>
      </div>
      <?php endforeach ?>
      <div class="result-count"><?= count($results) ?> result(s)</div>
    <?php endif ?>
  </div>

  <div id="detail">
    <?php if ($email): ?>
      <div class="detail-header">
        <div class="detail-subject"><?= esc($email['subject'] ?: '(no subject)') ?></div>
        <div class="detail-meta">
          <span class="label">From</span><span class="val"><?= esc($email['from_addr']) ?></span>
          <span class="label">To</span><span class="val"><?= esc($email['to_addrs']) ?></span>
          <?php if ($email['cc_addrs']): ?>
          <span class="label">Cc</span><span class="val"><?= esc($email['cc_addrs']) ?></span>
          <?php endif ?>
          <span class="label">Date</span><span class="val"><?= esc($email['date']) ?></span>
          <span class="label">Mailbox</span><span class="val"><?= esc($email['mailbox']) ?></span>
        </div>
      </div>
      <div class="detail-body"><?= esc($email['body_text']) ?></div>
      <?php if (!empty($email['attachments'])): ?>
        <div class="att-list">
          <?php foreach ($email['attachments'] as $a): ?>
            <span class="att">📎 <?= esc($a['original_filename']) ?></span>
          <?php endforeach ?>
        </div>
      <?php endif ?>
    <?php else: ?>
      <div class="empty-state" style="margin-top:4rem">
        <div style="color:#374151;font-size:.85rem">Select an email to read it</div>
      </div>
    <?php endif ?>
  </div>
</div>

<div id="statusbar">
  <span><?= number_format($total) ?> emails · last import: <?= esc((string)$lastImport) ?></span>
  <span>MariaDB ● PHP ●</span>
</div>

</body>
</html>
