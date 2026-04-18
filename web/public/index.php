<?php
/**
 * web/public/index.php — Mrija Archive search UI.
 */
declare(strict_types=1);

// ── DB connection ─────────────────────────────────────────────────────────────
$cfgPath = __DIR__ . '/../config/local.php';
if (!is_file($cfgPath)) {
    die('<p style="color:#f87171;font-family:sans-serif;padding:2rem">Config not found.</p>');
}
$config = require $cfgPath;
$db     = $config['db'] ?? [];
$socket = $db['socket'] ?? '';
if ($socket && file_exists($socket)) {
    $dsn = "mysql:unix_socket=$socket;dbname={$db['dbname']};charset={$db['charset']}";
} else {
    $dsn = "mysql:host=" . ($db['host'] ?? '127.0.0.1') . ";port=" . ($db['port'] ?? 3306) .
           ";dbname={$db['dbname']};charset={$db['charset']}";
}
try {
    $pdo = new PDO($dsn, $db['user'] ?? '', $db['password'] ?? '', [
        PDO::ATTR_ERRMODE            => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
} catch (PDOException $e) {
    echo '<!DOCTYPE html><html><body style="background:#111827;color:#f87171;font-family:sans-serif;padding:3rem;text-align:center">';
    echo '<h2>Database starting up…</h2><p>Please refresh in a moment.</p>';
    echo '<meta http-equiv="refresh" content="3">';
    echo '</body></html>';
    exit;
}

// ── Input ─────────────────────────────────────────────────────────────────────
$q          = trim((string)($_GET['q']         ?? ''));
$mailbox    = trim((string)($_GET['mailbox']   ?? ''));
$selectedId = trim((string)($_GET['id']        ?? ''));
$selMailbox = trim((string)($_GET['smb']       ?? ''));
$dateFrom   = trim((string)($_GET['date_from'] ?? ''));
$dateTo     = trim((string)($_GET['date_to']   ?? ''));
$sort       = trim((string)($_GET['sort']      ?? 'date_desc'));
$hasAtt     = ($_GET['has_att'] ?? '') === '1';
$exportCsv  = ($_GET['export']  ?? '') === 'csv';
$page       = max(1, (int)($_GET['page'] ?? 1));
$limit      = 100;
$offset     = ($page - 1) * $limit;

if ($dateFrom && !preg_match('/^\d{4}-\d{2}-\d{2}$/', $dateFrom)) $dateFrom = '';
if ($dateTo   && !preg_match('/^\d{4}-\d{2}-\d{2}$/', $dateTo))   $dateTo   = '';

$sortOptions = [
    'date_desc'   => ['label' => 'Datum ↓ (neueste)',  'sql' => 'ae.date DESC'],
    'date_asc'    => ['label' => 'Datum ↑ (älteste)',  'sql' => 'ae.date ASC'],
    'from_asc'    => ['label' => 'Absender A→Z',       'sql' => 'ae.from_addr ASC'],
    'from_desc'   => ['label' => 'Absender Z→A',       'sql' => 'ae.from_addr DESC'],
    'subject_asc' => ['label' => 'Betreff A→Z',        'sql' => 'ae.subject ASC'],
    'size_desc'   => ['label' => 'Größe ↓',            'sql' => 'ae.total_size_bytes DESC'],
];
if (!array_key_exists($sort, $sortOptions)) $sort = 'date_desc';
$orderBy = $sortOptions[$sort]['sql'];

// ── Stats ─────────────────────────────────────────────────────────────────────
$total      = (int)$pdo->query("SELECT COUNT(*) FROM archive_emails")->fetchColumn();
$lastImport = $pdo->query("SELECT MAX(imported_at) FROM archive_emails")->fetchColumn() ?: '—';

// Mailbox list with email count + total size
$mailboxStats = $pdo->query(
    "SELECT mailbox,
            COUNT(*) AS email_count,
            SUM(total_size_bytes) AS total_bytes
     FROM archive_emails
     GROUP BY mailbox
     ORDER BY mailbox"
)->fetchAll();
$mailboxes = array_column($mailboxStats, null, 'mailbox');

// ── Build WHERE ───────────────────────────────────────────────────────────────
$whereClauses = [];
$params       = [];

if ($mailbox !== '') {
    $whereClauses[] = 'ae.mailbox = ?';
    $params[]       = $mailbox;
}
if ($dateFrom !== '') {
    $whereClauses[] = 'ae.date >= ?';
    $params[]       = $dateFrom;
}
if ($dateTo !== '') {
    $whereClauses[] = 'ae.date <= ?';
    $params[]       = $dateTo . ' 23:59:59';
}
if ($hasAtt) {
    $whereClauses[] = 'EXISTS (SELECT 1 FROM archive_attachments att WHERE att.mailbox = ae.mailbox AND att.email_stable_id = ae.stable_id)';
}

$attCountSubq = '(SELECT COUNT(*) FROM archive_attachments att WHERE att.mailbox = ae.mailbox AND att.email_stable_id = ae.stable_id)';
$selectCols   = "ae.mailbox, ae.stable_id, ae.date, ae.from_addr, ae.subject,
                 LEFT(ae.body_text, 200) AS preview,
                 $attCountSubq AS att_count";

// ── Query ─────────────────────────────────────────────────────────────────────
$results    = [];
$totalFound = 0;

if ($q !== '') {
    $ftParams = array_merge([$q], $params);
    $whereAnd = $whereClauses ? 'AND ' . implode(' AND ', $whereClauses) : '';

    $countStmt = $pdo->prepare(
        "SELECT COUNT(*) FROM archive_emails ae
         WHERE MATCH(ae.subject, ae.from_addr, ae.to_addrs, ae.cc_addrs, ae.body_text)
               AGAINST (? IN BOOLEAN MODE) $whereAnd"
    );
    $countStmt->execute($ftParams);
    $totalFound = (int)$countStmt->fetchColumn();

    if (!$exportCsv) {
        $stmt = $pdo->prepare(
            "SELECT $selectCols
             FROM archive_emails ae
             WHERE MATCH(ae.subject, ae.from_addr, ae.to_addrs, ae.cc_addrs, ae.body_text)
                   AGAINST (? IN BOOLEAN MODE) $whereAnd
             ORDER BY $orderBy LIMIT $limit OFFSET $offset"
        );
        $stmt->execute($ftParams);
        $results = $stmt->fetchAll();
    }
} else {
    $whereStr = $whereClauses ? 'WHERE ' . implode(' AND ', $whereClauses) : '';

    $countStmt = $pdo->prepare("SELECT COUNT(*) FROM archive_emails ae $whereStr");
    $countStmt->execute($params);
    $totalFound = (int)$countStmt->fetchColumn();

    if (!$exportCsv) {
        $stmt = $pdo->prepare(
            "SELECT $selectCols FROM archive_emails ae $whereStr
             ORDER BY $orderBy LIMIT $limit OFFSET $offset"
        );
        $stmt->execute($params);
        $results = $stmt->fetchAll();
    }
}

$totalPages = max(1, (int)ceil($totalFound / $limit));

// ── CSV export (streams before any HTML) ─────────────────────────────────────
if ($exportCsv) {
    $filename = 'mrija_export_' . date('Y-m-d');
    if ($mailbox) $filename .= '_' . preg_replace('/[^a-z0-9._-]/i', '_', $mailbox);
    if ($q)       $filename .= '_' . preg_replace('/[^a-z0-9._-]/i', '_', substr($q, 0, 30));

    header('Content-Type: text/csv; charset=utf-8');
    header('Content-Disposition: attachment; filename="' . $filename . '.csv"');
    header('Cache-Control: no-cache');

    $out = fopen('php://output', 'w');
    fprintf($out, chr(0xEF) . chr(0xBB) . chr(0xBF)); // UTF-8 BOM for Excel
    fputcsv($out, ['Datum', 'Absender', 'An', 'Betreff', 'Mailbox', 'Anhänge', 'Größe (KB)']);

    $exportLimit  = 5000;
    $exportOffset = 0;
    while (true) {
        if ($q !== '') {
            $ftParams = array_merge([$q], $params);
            $whereAnd = $whereClauses ? 'AND ' . implode(' AND ', $whereClauses) : '';
            $estmt = $pdo->prepare(
                "SELECT ae.date, ae.from_addr, ae.to_addrs, ae.subject, ae.mailbox,
                        $attCountSubq AS att_count, ae.total_size_bytes
                 FROM archive_emails ae
                 WHERE MATCH(ae.subject, ae.from_addr, ae.to_addrs, ae.cc_addrs, ae.body_text)
                       AGAINST (? IN BOOLEAN MODE) $whereAnd
                 ORDER BY $orderBy LIMIT $exportLimit OFFSET $exportOffset"
            );
            $estmt->execute($ftParams);
        } else {
            $whereStr = $whereClauses ? 'WHERE ' . implode(' AND ', $whereClauses) : '';
            $estmt = $pdo->prepare(
                "SELECT ae.date, ae.from_addr, ae.to_addrs, ae.subject, ae.mailbox,
                        $attCountSubq AS att_count, ae.total_size_bytes
                 FROM archive_emails ae $whereStr
                 ORDER BY $orderBy LIMIT $exportLimit OFFSET $exportOffset"
            );
            $estmt->execute($params);
        }
        $rows = $estmt->fetchAll();
        if (!$rows) break;
        foreach ($rows as $row) {
            fputcsv($out, [
                substr($row['date'], 0, 10),
                $row['from_addr'],
                $row['to_addrs'],
                $row['subject'],
                $row['mailbox'],
                $row['att_count'],
                number_format((int)$row['total_size_bytes'] / 1024, 1),
            ]);
        }
        $exportOffset += $exportLimit;
        if (count($rows) < $exportLimit) break;
    }
    fclose($out);
    exit;
}

// ── Selected email ────────────────────────────────────────────────────────────
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

// ── Helpers ───────────────────────────────────────────────────────────────────
function esc(string $s): string { return htmlspecialchars($s, ENT_QUOTES, 'UTF-8'); }

function fmtBytes(int $bytes): string {
    if ($bytes >= 1073741824) return number_format($bytes / 1073741824, 1) . ' GB';
    if ($bytes >= 1048576)    return number_format($bytes / 1048576, 1) . ' MB';
    return number_format($bytes / 1024, 0) . ' KB';
}

// Highlight search terms in already-escaped HTML
function highlight(string $escaped, string $q): string {
    if ($q === '') return $escaped;
    $words = preg_split('/[\s+\-*"()]+/', $q, -1, PREG_SPLIT_NO_EMPTY);
    foreach ($words as $word) {
        if (mb_strlen($word) < 2) continue;
        $escaped = preg_replace(
            '/(' . preg_quote(esc($word), '/') . ')/iu',
            '<mark>$1</mark>',
            $escaped
        );
    }
    return $escaped;
}

function buildUrl(array $overrides = []): string {
    global $q, $mailbox, $dateFrom, $dateTo, $sort, $hasAtt, $page, $selectedId, $selMailbox;
    $params = array_filter([
        'q'         => $q,
        'mailbox'   => $mailbox,
        'date_from' => $dateFrom,
        'date_to'   => $dateTo,
        'sort'      => $sort !== 'date_desc' ? $sort : '',
        'has_att'   => $hasAtt ? '1' : '',
        'page'      => $page > 1 ? (string)$page : '',
        'id'        => $selectedId,
        'smb'       => $selMailbox,
    ], fn($v) => $v !== '');
    return '?' . http_build_query(array_merge($params, $overrides));
}
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
#toolbar{background:#1e1b4b;padding:.45rem .8rem;display:flex;align-items:center;gap:.6rem;border-bottom:1px solid #312e81;flex-shrink:0;flex-wrap:wrap}
.logo-btn{background:none;border:none;cursor:pointer;display:flex;align-items:center;gap:.35rem;padding:.2rem .45rem;border-radius:6px;color:#e0e7ff;font-weight:600;font-size:.88rem;white-space:nowrap;position:relative}
.logo-btn:hover{background:#312e81}
.logo-btn .caret{font-size:.55rem;opacity:.5}

/* Mailbox dropdown */
.mb-dropdown{position:absolute;top:calc(100% + 4px);left:0;background:#1e1b4b;border:1px solid #4f46e5;border-radius:8px;min-width:220px;max-height:380px;overflow-y:auto;z-index:200;box-shadow:0 8px 28px rgba(0,0,0,.6)}
.mb-dropdown a{display:flex;justify-content:space-between;align-items:center;padding:.38rem .8rem;font-size:.78rem;color:#c7d2fe;text-decoration:none;gap:.5rem}
.mb-dropdown a:hover,.mb-dropdown a.cur{background:#312e81;color:#e0e7ff}
.mb-dropdown .mb-sep{border-top:1px solid #312e81;margin:.2rem 0}
.mb-dropdown .mb-meta{color:#4b5563;font-size:.65rem;white-space:nowrap}

/* Search form */
#search-form{flex:1;display:flex;gap:.35rem;align-items:center;min-width:0}
#q{flex:1;background:#111827;border:1px solid #4f46e5;border-radius:6px;padding:.32rem .7rem;color:#e0e7ff;font-size:.83rem;outline:none;min-width:0}
#q:focus{border-color:#818cf8}
.date-input{background:#111827;border:1px solid #374151;color:#9ca3af;border-radius:6px;padding:.28rem .45rem;font-size:.73rem;width:112px}
.date-input:focus{outline:none;border-color:#4f46e5;color:#e0e7ff}
.date-sep{color:#4b5563;font-size:.73rem}
select.toolbar-sel{background:#111827;border:1px solid #374151;color:#9ca3af;border-radius:6px;padding:.28rem .45rem;font-size:.73rem;cursor:pointer}
select.toolbar-sel:focus{outline:none;border-color:#4f46e5}
#search-btn{background:#4f46e5;color:#fff;border:none;border-radius:6px;padding:.32rem .9rem;font-size:.78rem;cursor:pointer;white-space:nowrap}
#search-btn:hover{background:#4338ca}

/* Toolbar right-side buttons */
.tb-btn{background:transparent;color:#9ca3af;border:1px solid #374151;border-radius:5px;padding:.25rem .55rem;font-size:.72rem;cursor:pointer;text-decoration:none;white-space:nowrap;display:inline-flex;align-items:center;gap:.25rem}
.tb-btn:hover{color:#e0e7ff;border-color:#6366f1}
.tb-btn.active{background:#312e81;color:#a5b4fc;border-color:#4f46e5}
.stop-btn{color:#9ca3af;border-color:#374151}
.stop-btn:hover{color:#f87171;border-color:#f87171}

/* Filter badges */
.badges{display:flex;gap:.35rem;align-items:center;flex-wrap:wrap}
.badge{background:#1f2937;color:#a5b4fc;border:1px solid #374151;border-radius:4px;padding:.12rem .4rem;font-size:.67rem;display:inline-flex;align-items:center;gap:.25rem}
.badge a{color:#6366f1;text-decoration:none;font-size:.75rem;line-height:1}
.badge a:hover{color:#f87171}

/* Main layout */
#main{display:flex;flex:1;overflow:hidden}

/* Results list */
#results{width:40%;border-right:1px solid #1f2937;overflow-y:auto;flex-shrink:0;display:flex;flex-direction:column}
.result{padding:.55rem .75rem;border-bottom:1px solid #1f2937;cursor:pointer;border-left:3px solid transparent;flex-shrink:0}
.result:hover{background:#1a2234}
.result.active{background:#1e1b4b;border-left-color:#6366f1}
.result .r-subj{color:#c7d2fe;font-size:.77rem;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.result.active .r-subj{color:#e0e7ff}
.result .r-meta{color:#6b7280;font-size:.66rem;margin-top:.12rem;display:flex;gap:.35rem;align-items:center;flex-wrap:wrap}
.mb-tag{background:#1f2937;color:#6366f1;border-radius:3px;padding:.04rem .28rem;font-size:.62rem}
.att-badge{color:#f59e0b;font-size:.65rem}
.result .r-preview{color:#4b5563;font-size:.64rem;margin-top:.18rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.result.active .r-preview{color:#6b7280}
mark{background:#3b2f00;color:#fcd34d;border-radius:2px;padding:0 1px}

.result-count{padding:.32rem .75rem;color:#4b5563;font-size:.66rem;border-bottom:1px solid #1f2937;flex-shrink:0;display:flex;justify-content:space-between;align-items:center}

/* Pagination */
.pagination{padding:.45rem;display:flex;align-items:center;justify-content:center;gap:.3rem;border-top:1px solid #1f2937;flex-shrink:0;margin-top:auto}
.pagination a,.pagination span{padding:.18rem .5rem;border-radius:4px;font-size:.7rem;text-decoration:none;color:#9ca3af;border:1px solid #374151}
.pagination a:hover{background:#1f2937;color:#e0e7ff}
.pagination .cur{background:#4f46e5;color:#fff;border-color:#4f46e5}
.pagination .dis{opacity:.3;pointer-events:none}

.empty-state{padding:3rem;text-align:center;color:#374151;flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center}
.empty-state .icon{font-size:2.2rem;margin-bottom:.6rem}

/* Email detail */
#detail{flex:1;padding:1.1rem 1.3rem;overflow-y:auto}
.d-subject{color:#e0e7ff;font-size:.98rem;font-weight:600;margin-bottom:.65rem;line-height:1.35}
.d-meta{display:grid;grid-template-columns:auto 1fr;gap:.18rem .7rem;font-size:.74rem;margin-bottom:.8rem;padding-bottom:.8rem;border-bottom:1px solid #1f2937}
.d-meta .lbl{color:#6366f1;white-space:nowrap}
.d-meta .val{color:#9ca3af;word-break:break-word}
.d-body{color:#d1d5db;font-size:.81rem;line-height:1.68;white-space:pre-wrap;word-break:break-word}
.att-section{margin-top:1.1rem;padding-top:.8rem;border-top:1px solid #1f2937}
.att-section-title{color:#6b7280;font-size:.7rem;margin-bottom:.5rem;text-transform:uppercase;letter-spacing:.05em}
.att-list{display:flex;flex-wrap:wrap;gap:.35rem}
.att{display:inline-flex;align-items:center;gap:.3rem;background:#1f2937;border:1px solid #374151;border-radius:5px;padding:.28rem .65rem;font-size:.71rem;color:#9ca3af;text-decoration:none;transition:all .15s}
.att:hover{background:#312e81;color:#c7d2fe;border-color:#4f46e5}
.att .att-size{color:#4b5563;font-size:.63rem}

.d-placeholder{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;color:#374151;font-size:.83rem;gap:.5rem}
.d-placeholder .keys{font-size:.68rem;color:#1f2937;margin-top:.5rem}
kbd{background:#1f2937;border:1px solid #374151;border-radius:3px;padding:.1rem .3rem;font-size:.65rem;color:#6b7280}

/* Status bar */
#statusbar{background:#0f172a;border-top:1px solid #1f2937;padding:.22rem 1rem;display:flex;justify-content:space-between;font-size:.63rem;color:#374151;flex-shrink:0}
</style>
</head>
<body>

<div id="toolbar">
  <!-- Logo / Mailbox-Picker -->
  <div style="position:relative;flex-shrink:0">
    <button class="logo-btn" id="logo-btn" onclick="toggleDrop(event)">
      📧 Mrija Archive <span class="caret">▼</span>
    </button>
    <div class="mb-dropdown" id="mb-dropdown" style="display:none">
      <a class="<?= $mailbox === '' ? 'cur' : '' ?>"
         href="<?= esc(buildUrl(['mailbox'=>'','page'=>'1','id'=>'','smb'=>''])) ?>">
        <span>Alle Postfächer</span>
        <span class="mb-meta"><?= number_format($total) ?></span>
      </a>
      <div class="mb-sep"></div>
      <?php foreach ($mailboxStats as $ms):
        $mb    = $ms['mailbox'];
        $cnt   = (int)$ms['email_count'];
        $bytes = (int)$ms['total_bytes'];
      ?>
        <a href="<?= esc(buildUrl(['mailbox'=>$mb,'page'=>'1','id'=>'','smb'=>''])) ?>"
           class="<?= $mailbox === $mb ? 'cur' : '' ?>">
          <span><?= esc($mb) ?></span>
          <span class="mb-meta"><?= number_format($cnt) ?> · <?= fmtBytes($bytes) ?></span>
        </a>
      <?php endforeach ?>
    </div>
  </div>

  <!-- Suchformular -->
  <form id="search-form" method="get" action="" style="flex:1;display:flex;gap:.35rem;align-items:center;min-width:0">
    <?php if ($mailbox !== ''): ?>
      <input type="hidden" name="mailbox" value="<?= esc($mailbox) ?>">
    <?php endif ?>
    <input id="q" name="q" type="text" value="<?= esc($q) ?>"
           placeholder="Suchen — Betreff, Absender, Text…" autocomplete="off">
    <input type="date" name="date_from" class="date-input" value="<?= esc($dateFrom) ?>" title="Von Datum">
    <span class="date-sep">–</span>
    <input type="date" name="date_to"   class="date-input" value="<?= esc($dateTo) ?>"   title="Bis Datum">
    <select name="sort" class="toolbar-sel" onchange="this.form.submit()">
      <?php foreach ($sortOptions as $key => $opt): ?>
        <option value="<?= esc($key) ?>" <?= $sort === $key ? 'selected' : '' ?>><?= esc($opt['label']) ?></option>
      <?php endforeach ?>
    </select>
    <button id="search-btn" type="submit">Suchen</button>
  </form>

  <!-- Filter-Buttons -->
  <a class="tb-btn <?= $hasAtt ? 'active' : '' ?>"
     href="<?= esc(buildUrl(['has_att' => $hasAtt ? '' : '1', 'page' => '1'])) ?>"
     title="Nur E-Mails mit Anhängen">
    📎 Anhänge
  </a>

  <?php if ($totalFound > 0): ?>
    <a class="tb-btn" href="<?= esc(buildUrl(['export' => 'csv', 'page' => ''])) ?>"
       title="Aktuelle Ergebnisse als CSV exportieren (<?= number_format($totalFound) ?> Zeilen)">
      ⬇ CSV
    </a>
  <?php endif ?>

  <!-- Aktive Filter-Badges -->
  <div class="badges">
    <?php if ($mailbox !== ''): ?>
      <span class="badge">📂 <?= esc($mailbox) ?> <a href="<?= esc(buildUrl(['mailbox'=>'','page'=>'1'])) ?>">✕</a></span>
    <?php endif ?>
    <?php if ($dateFrom !== '' || $dateTo !== ''): ?>
      <span class="badge">
        📅 <?= $dateFrom ?: '…' ?> – <?= $dateTo ?: '…' ?>
        <a href="<?= esc(buildUrl(['date_from'=>'','date_to'=>'','page'=>'1'])) ?>">✕</a>
      </span>
    <?php endif ?>
    <?php if ($hasAtt): ?>
      <span class="badge">📎 nur mit Anhang <a href="<?= esc(buildUrl(['has_att'=>'','page'=>'1'])) ?>">✕</a></span>
    <?php endif ?>
    <?php if ($q !== '' || $dateFrom !== '' || $dateTo !== '' || $hasAtt): ?>
      <a class="tb-btn" href="<?= esc(buildUrl(['q'=>'','date_from'=>'','date_to'=>'','has_att'=>'','page'=>'1','id'=>'','smb'=>''])) ?>">✕ Alle Filter</a>
    <?php endif ?>
  </div>

  <button class="tb-btn stop-btn" onclick="if(window.pywebview){window.pywebview.api.stop_archive()}else{alert('Launcher verwenden.')}">■ Stop</button>
</div>

<div id="main">
  <div id="results">
    <?php if (empty($results) && $totalFound === 0): ?>
      <div class="empty-state">
        <div class="icon">📭</div>
        <div><?= $q !== '' ? 'Keine Ergebnisse für <em>' . esc($q) . '</em>' : 'Keine E-Mails gefunden' ?></div>
      </div>
    <?php else: ?>
      <!-- Ergebnis-Kopf -->
      <div class="result-count">
        <span>
          <?php
            $from = $offset + 1;
            $to   = min($offset + count($results), $totalFound);
            echo number_format($from) . '–' . number_format($to) . ' von ' . number_format($totalFound);
            if ($q !== '') echo ' für <em>' . esc($q) . '</em>';
          ?>
        </span>
      </div>

      <!-- Ergebnisliste -->
      <?php foreach ($results as $idx => $r):
        $isActive = ($r['stable_id'] === $selectedId && $r['mailbox'] === $selMailbox);
        $link = buildUrl(['id' => $r['stable_id'], 'smb' => $r['mailbox'], 'page' => (string)$page]);
        $attCount = (int)$r['att_count'];
      ?>
      <div class="result <?= $isActive ? 'active' : '' ?>"
           data-href="<?= esc($link) ?>"
           data-idx="<?= $idx ?>"
           onclick="window.location=this.dataset.href">
        <div class="r-subj"><?= highlight(esc($r['subject'] ?: '(kein Betreff)'), $q) ?></div>
        <div class="r-meta">
          <span><?= highlight(esc($r['from_addr']), $q) ?></span>
          <span>·</span>
          <span><?= esc(substr($r['date'], 0, 10)) ?></span>
          <?php if ($mailbox === ''): ?>
            <span class="mb-tag"><?= esc($r['mailbox']) ?></span>
          <?php endif ?>
          <?php if ($attCount > 0): ?>
            <span class="att-badge">📎<?= $attCount > 1 ? " $attCount" : '' ?></span>
          <?php endif ?>
        </div>
        <div class="r-preview"><?= highlight(esc($r['preview']), $q) ?></div>
      </div>
      <?php endforeach ?>

      <!-- Pagination -->
      <?php if ($totalPages > 1): ?>
        <div class="pagination">
          <?php if ($page > 1): ?>
            <a href="<?= esc(buildUrl(['page'=>'1'])) ?>">«</a>
            <a href="<?= esc(buildUrl(['page'=>(string)($page-1)])) ?>">‹</a>
          <?php else: ?>
            <span class="dis">«</span><span class="dis">‹</span>
          <?php endif ?>
          <?php
            $s = max(1, $page-2); $e = min($totalPages, $page+2);
            if ($s > 1) echo '<span>…</span>';
            for ($i = $s; $i <= $e; $i++):
          ?>
            <?php if ($i === $page): ?>
              <span class="cur"><?= $i ?></span>
            <?php else: ?>
              <a href="<?= esc(buildUrl(['page'=>(string)$i])) ?>"><?= $i ?></a>
            <?php endif ?>
          <?php endfor; if ($e < $totalPages) echo '<span>…</span>'; ?>
          <?php if ($page < $totalPages): ?>
            <a href="<?= esc(buildUrl(['page'=>(string)($page+1)])) ?>">›</a>
            <a href="<?= esc(buildUrl(['page'=>(string)$totalPages])) ?>">»</a>
          <?php else: ?>
            <span class="dis">›</span><span class="dis">»</span>
          <?php endif ?>
        </div>
      <?php endif ?>
    <?php endif ?>
  </div>

  <!-- E-Mail Detail -->
  <div id="detail">
    <?php if ($email): ?>
      <div class="d-subject"><?= highlight(esc($email['subject'] ?: '(kein Betreff)'), $q) ?></div>
      <div class="d-meta">
        <span class="lbl">Von</span>     <span class="val"><?= highlight(esc($email['from_addr']), $q) ?></span>
        <span class="lbl">An</span>      <span class="val"><?= esc($email['to_addrs']) ?></span>
        <?php if ($email['cc_addrs']): ?>
        <span class="lbl">Cc</span>      <span class="val"><?= esc($email['cc_addrs']) ?></span>
        <?php endif ?>
        <span class="lbl">Datum</span>   <span class="val"><?= esc($email['date']) ?></span>
        <span class="lbl">Postfach</span><span class="val"><?= esc($email['mailbox']) ?></span>
        <span class="lbl">Größe</span>   <span class="val"><?= fmtBytes((int)$email['total_size_bytes']) ?></span>
      </div>
      <div class="d-body"><?= highlight(esc($email['body_text'] ?: '(kein Text)'), $q) ?></div>
      <?php if (!empty($email['attachments'])): ?>
        <div class="att-section">
          <div class="att-section-title">Anhänge (<?= count($email['attachments']) ?>)</div>
          <div class="att-list">
            <?php foreach ($email['attachments'] as $a):
              $dlUrl = '/download.php?' . http_build_query(['mailbox'=>$a['mailbox'],'sha256'=>$a['sha256']]);
            ?>
              <a class="att" href="<?= esc($dlUrl) ?>" download="<?= esc($a['original_filename']) ?>">
                📎 <?= esc($a['original_filename']) ?>
                <span class="att-size"><?= fmtBytes((int)$a['size']) ?></span>
              </a>
            <?php endforeach ?>
          </div>
        </div>
      <?php endif ?>
    <?php else: ?>
      <div class="d-placeholder">
        <div>E-Mail auswählen zum Lesen</div>
        <div class="keys">Tastatur: <kbd>j</kbd><kbd>k</kbd> navigieren · <kbd>Enter</kbd> öffnen · <kbd>/</kbd> suchen</div>
      </div>
    <?php endif ?>
  </div>
</div>

<div id="statusbar">
  <span><?= number_format($total) ?> E-Mails gesamt · letzter Import: <?= esc((string)$lastImport) ?></span>
  <span>MariaDB ● PHP</span>
</div>

<script>
// ── Mailbox-Dropdown ──────────────────────────────────────────────────────────
function toggleDrop(e) {
  e.stopPropagation();
  const d = document.getElementById('mb-dropdown');
  d.style.display = d.style.display === 'none' ? 'block' : 'none';
}
document.addEventListener('click', () => {
  document.getElementById('mb-dropdown').style.display = 'none';
});

// ── Tastaturnavigation ────────────────────────────────────────────────────────
const results = Array.from(document.querySelectorAll('.result'));
let focusIdx   = results.findIndex(r => r.classList.contains('active'));

function moveFocus(delta) {
  const next = focusIdx + delta;
  if (next < 0 || next >= results.length) return;
  focusIdx = next;
  results[focusIdx].scrollIntoView({block: 'nearest'});
  results.forEach((r, i) => r.style.outline = i === focusIdx ? '1px solid #4f46e5' : '');
}

document.addEventListener('keydown', e => {
  const tag = document.activeElement?.tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') {
    // '/' focuses search
    if (e.key === 'Escape') document.activeElement.blur();
    return;
  }
  switch (e.key) {
    case 'j': case 'ArrowDown':  e.preventDefault(); moveFocus(+1); break;
    case 'k': case 'ArrowUp':    e.preventDefault(); moveFocus(-1); break;
    case 'Enter':
      if (focusIdx >= 0) window.location = results[focusIdx].dataset.href;
      break;
    case '/':
      e.preventDefault();
      document.getElementById('q').focus();
      break;
  }
});
</script>

</body>
</html>
