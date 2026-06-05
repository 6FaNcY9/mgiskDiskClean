<?php
/**
 * web/public/index.php — Mrija Archive search UI.
 */
declare(strict_types=1);

// ── Config + Auth ─────────────────────────────────────────────────────────────
$cfgPath = __DIR__ . '/../config/local.php';
if (!is_file($cfgPath)) {
    die('<p style="color:#f87171;font-family:sans-serif;padding:2rem">Config not found.</p>');
}
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
$csrf = new \MailReview\Auth\CsrfGuard();
$csrfToken = $csrf->getToken();
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

// Load VT cache status for this email's attachments (keyed by sha256)
$vtStatuses = [];
if ($email && !empty($email['attachments'])) {
    $hashes = array_column($email['attachments'], 'sha256');
    $placeholders = implode(',', array_fill(0, count($hashes), '?'));
    $vtStmt = $pdo->prepare(
        "SELECT sha256, status, positives FROM vt_cache WHERE sha256 IN ($placeholders)"
    );
    $vtStmt->execute($hashes);
    foreach ($vtStmt->fetchAll() as $vr) {
        $vtStatuses[$vr['sha256']] = $vr;
    }
}

// Load the current review decision if the migration has been applied.
$reviewDecision = null;
if ($email) {
    try {
        $reviewStmt = $pdo->prepare(
            "SELECT decision, notes, reviewer_role, reviewer_name, updated_at
             FROM review_decisions
             WHERE mailbox = ? AND email_stable_id = ?
             LIMIT 1"
        );
        $reviewStmt->execute([$email['mailbox'], $email['stable_id']]);
        $reviewDecision = $reviewStmt->fetch() ?: null;
    } catch (PDOException $e) {
        $reviewDecision = null;
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
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="csrf-token" content="<?= esc($csrfToken) ?>">
<title>Mrija Archive</title>
<script>
/* Anti-flicker: apply stored theme before first paint */
(function(){
  var a=localStorage.getItem('mrija-accent')||'';
  var m=localStorage.getItem('mrija-mode')||'dark';
  var h=document.documentElement;
  if(a)h.setAttribute('data-accent',a);
  if(m==='light')h.setAttribute('data-mode','light');
})();
</script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;overflow:hidden}
body{font-family:system-ui,-apple-system,sans-serif;background:var(--bg-1);color:var(--text-1);height:100vh;display:flex;flex-direction:column}

/* ── Theme variables ─────────────────────────────────────────────────────── */
:root{
  --theme-bg:#111111;--theme-sidebar-bg:#0d0d0d;--theme-surface:#1a1a1a;--theme-surface-raised:#222222;
  --theme-border:#2a2a2a;
  --theme-text:#e8e8e8;--theme-text-dim:#888888;--theme-text-muted:#444444;
  --theme-accent:#c0c0c0;--theme-accent-bg:rgba(192,192,192,.08);--theme-accent-border:rgba(192,192,192,.22);
  --bg-0:var(--theme-sidebar-bg);--bg-1:var(--theme-bg);--bg-2:var(--theme-surface);--bg-3:var(--theme-surface-raised);
  --border:var(--theme-border);
  --text-1:var(--theme-text);--text-2:var(--theme-text-dim);--text-3:var(--theme-text-muted);
  --accent:var(--theme-accent);--accent-bg:var(--theme-accent-bg);--accent-border:var(--theme-accent-border);
  --danger:#c0606a;--warn:#d4900a;--ok:#6a9f6a;
}
[data-mode="light"]{
  --theme-bg:#f5f5f5;--theme-sidebar-bg:#ebebeb;--theme-surface:#ffffff;--theme-surface-raised:#e0e0e0;
  --theme-border:#d4d4d4;
  --theme-text:#1a1a1a;--theme-text-dim:#666666;--theme-text-muted:#aaaaaa;
  --theme-accent:#555555;--theme-accent-bg:rgba(85,85,85,.07);--theme-accent-border:rgba(85,85,85,.22);
}
[data-accent="blue"]  {--theme-accent:#4a90d9;--theme-accent-bg:rgba(74,144,217,.1);--theme-accent-border:rgba(74,144,217,.3)}
[data-accent="teal"]  {--theme-accent:#2a9d8f;--theme-accent-bg:rgba(42,157,143,.1);--theme-accent-border:rgba(42,157,143,.3)}
[data-accent="amber"] {--theme-accent:#d4900a;--theme-accent-bg:rgba(212,144,10,.1);--theme-accent-border:rgba(212,144,10,.3)}
[data-accent="sage"]  {--theme-accent:#6a9f6a;--theme-accent-bg:rgba(106,159,106,.1);--theme-accent-border:rgba(106,159,106,.3)}
[data-accent="rose"]  {--theme-accent:#c0606a;--theme-accent-bg:rgba(192,96,106,.1);--theme-accent-border:rgba(192,96,106,.3)}

/* ── Layout ──────────────────────────────────────────────────────────────── */
#app{display:flex;flex:1;overflow:hidden}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
#sidebar{width:185px;flex-shrink:0;background:var(--bg-0);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.sb-title{padding:.75rem .9rem .55rem;font-size:.82rem;font-weight:700;color:var(--text-1);letter-spacing:.02em;border-bottom:1px solid var(--border);flex-shrink:0}
.mb-list{flex:1;overflow-y:auto;padding:.35rem 0}
.mb-item{display:block;padding:.36rem .9rem;text-decoration:none;color:var(--text-2);font-size:.77rem;border-left:2px solid transparent;transition:all .1s}
.mb-item:hover{background:var(--bg-2);color:var(--text-1)}
.mb-item.cur{background:var(--accent-bg);border-left-color:var(--accent);color:var(--accent)}
.mb-name{display:block;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mb-meta{display:block;font-size:.63rem;color:var(--text-3);margin-top:.06rem}
.mb-item.cur .mb-meta{color:var(--accent);opacity:.65}
.mb-sep{border-top:1px solid var(--border);margin:.3rem 0}
.sb-foot{border-top:1px solid var(--border);padding:.45rem .9rem;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
.sb-stats{font-size:.62rem;color:var(--text-3)}
.theme-btn{background:none;border:1px solid var(--border);border-radius:4px;padding:.18rem .3rem;font-size:.78rem;cursor:pointer;color:var(--text-3);transition:all .1s;line-height:1}
.theme-btn:hover{border-color:var(--accent);color:var(--accent)}

/* ── Middle panel ────────────────────────────────────────────────────────── */
#panel-mid{width:305px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;background:var(--bg-1)}
.search-wrap{padding:.45rem .55rem;border-bottom:1px solid var(--border);flex-shrink:0}
.search-row{display:flex;gap:.3rem;align-items:center}
#q{flex:1;background:var(--bg-2);border:1px solid var(--border);border-radius:5px;padding:.3rem .6rem;color:var(--text-1);font-size:.8rem;outline:none;min-width:0}
#q:focus{border-color:var(--accent)}
#q::placeholder{color:var(--text-3)}
.search-btn{background:var(--accent);color:var(--bg-0);border:none;border-radius:5px;padding:.3rem .6rem;font-size:.75rem;cursor:pointer;font-weight:700;white-space:nowrap}
.search-btn:hover{opacity:.85}
.filter-row{display:flex;gap:.28rem;align-items:center;padding:.3rem .55rem;border-bottom:1px solid var(--border);flex-shrink:0;flex-wrap:wrap}
.fi{background:var(--bg-2);border:1px solid var(--border);color:var(--text-2);border-radius:4px;padding:.18rem .35rem;font-size:.69rem;cursor:pointer}
.fi:focus{outline:none;border-color:var(--accent);color:var(--text-1)}
.date-sep{color:var(--text-3);font-size:.69rem}
.att-toggle{background:none;border:1px solid var(--border);border-radius:4px;padding:.18rem .35rem;font-size:.69rem;color:var(--text-2);cursor:pointer;text-decoration:none;white-space:nowrap}
.att-toggle.on{background:var(--accent-bg);border-color:var(--accent-border);color:var(--accent)}
.list-head{padding:.24rem .7rem;font-size:.63rem;color:var(--text-3);border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
.list-head a{color:var(--text-3);text-decoration:none}
.list-head a:hover{color:var(--accent)}
.email-list{flex:1;overflow-y:auto}
.erow{padding:.46rem .72rem;border-bottom:1px solid var(--border);border-left:2px solid transparent;cursor:pointer;transition:background .08s}
.erow:hover{background:var(--bg-2)}
.erow.cur{background:var(--accent-bg);border-left-color:var(--accent)}
.e-subj{font-size:.77rem;font-weight:600;color:var(--text-1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.erow.cur .e-subj{color:var(--accent)}
.e-meta{font-size:.65rem;color:var(--text-2);margin-top:.1rem;display:flex;gap:.3rem;align-items:center;flex-wrap:wrap}
.e-prev{font-size:.63rem;color:var(--text-3);margin-top:.08rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.att-badge{color:var(--warn);font-size:.62rem}
.mb-tag{background:var(--bg-3);color:var(--text-3);border-radius:3px;padding:.02rem .25rem;font-size:.6rem}
mark{background:rgba(212,144,10,.18);color:var(--text-1);border-radius:2px;padding:0 1px}
.empty-state{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--text-3);font-size:.8rem;gap:.45rem;padding:2rem;text-align:center}
.empty-state .icon{font-size:2rem}
.pagination{padding:.3rem;display:flex;align-items:center;justify-content:center;gap:.22rem;border-top:1px solid var(--border);flex-shrink:0}
.pagination a,.pagination span{padding:.14rem .42rem;border-radius:3px;font-size:.68rem;text-decoration:none;color:var(--text-2);border:1px solid var(--border)}
.pagination a:hover{background:var(--bg-2);color:var(--text-1)}
.pagination .cur{background:var(--accent);color:var(--bg-0);border-color:var(--accent);font-weight:700}
.pagination .dis{opacity:.3;pointer-events:none}

/* ── Detail panel ────────────────────────────────────────────────────────── */
#panel-detail{flex:1;overflow-y:auto;background:var(--bg-1);display:flex;flex-direction:column}
.d-empty{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--text-3);gap:.45rem;font-size:.8rem}
.d-empty .icon{font-size:2rem}
kbd{background:var(--bg-2);border:1px solid var(--border);border-radius:3px;padding:.08rem .28rem;font-size:.62rem;color:var(--text-2)}
.d-content{padding:1rem 1.25rem}
.d-subject{font-size:1rem;font-weight:700;color:var(--text-1);margin-bottom:.7rem;line-height:1.35}
.d-meta{display:grid;grid-template-columns:auto 1fr;gap:.15rem .6rem;font-size:.73rem;margin-bottom:.8rem;padding-bottom:.8rem;border-bottom:1px solid var(--border)}
.d-lbl{color:var(--accent);white-space:nowrap;font-size:.67rem;text-transform:uppercase;letter-spacing:.04em}
.d-val{color:var(--text-2);word-break:break-word}
.d-body{color:var(--text-1);font-size:.8rem;line-height:1.7;white-space:pre-wrap;word-break:break-word}
.review-box{margin:.8rem 0 1rem;padding:.7rem;border:1px solid var(--border);border-radius:6px;background:var(--bg-2)}
.review-row{display:grid;grid-template-columns:minmax(130px,190px) 1fr auto;gap:.5rem;align-items:start}
.review-select,.review-notes{background:var(--bg-1);border:1px solid var(--border);border-radius:5px;color:var(--text-1);font:inherit;font-size:.76rem;padding:.38rem .5rem;min-width:0}
.review-select:focus,.review-notes:focus{outline:none;border-color:var(--accent)}
.review-notes{min-height:2.15rem;resize:vertical;line-height:1.4}
.review-save{background:var(--accent);border:none;border-radius:5px;color:var(--bg-0);cursor:pointer;font-size:.75rem;font-weight:700;padding:.4rem .65rem;white-space:nowrap}
.review-save:disabled{cursor:default;opacity:.45}
.review-status{grid-column:1 / -1;color:var(--text-3);font-size:.65rem;min-height:.9rem}
.review-status.ok{color:var(--ok)}
.review-status.err{color:var(--danger)}

/* ── Attachments ─────────────────────────────────────────────────────────── */
.att-section{margin-top:1rem;padding-top:.8rem;border-top:1px solid var(--border)}
.att-sec-title{font-size:.67rem;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.55rem}
.att-block{background:var(--bg-2);border:1px solid var(--border);border-radius:6px;margin-bottom:.45rem;overflow:hidden}
.att-hdr{display:flex;align-items:center;gap:.45rem;padding:.4rem .65rem;cursor:pointer;transition:background .08s;font-size:.77rem}
.att-hdr:hover{background:var(--bg-3)}
.att-fname{flex:1;font-weight:500;color:var(--text-1);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.att-fsize{font-size:.64rem;color:var(--text-3);white-space:nowrap}
.vt-badge{font-size:.62rem;padding:.08rem .3rem;border-radius:3px;white-space:nowrap;font-weight:600}
.vt-clean{background:rgba(106,159,106,.13);color:#6a9f6a;border:1px solid rgba(106,159,106,.28)}
.vt-infected{background:rgba(192,96,106,.13);color:#c0606a;border:1px solid rgba(192,96,106,.28)}
.vt-pending{background:var(--bg-3);color:var(--text-3);border:1px solid var(--border)}
.vt-none{color:var(--text-3);font-size:.62rem}
.att-dl{background:var(--accent-bg);border:1px solid var(--accent-border);color:var(--accent);border-radius:4px;padding:.16rem .48rem;font-size:.7rem;text-decoration:none;white-space:nowrap;transition:opacity .1s}
.att-dl:hover{opacity:.8}
.att-blocked{background:rgba(192,96,106,.1);border-color:rgba(192,96,106,.28);color:#c0606a;border-radius:4px;padding:.16rem .48rem;font-size:.7rem;cursor:not-allowed}
.att-preview{max-height:0;overflow:hidden;transition:max-height .25s ease}
.att-preview.open{max-height:520px}
.att-preview img{width:100%;max-height:300px;object-fit:contain;display:block;padding:.5rem;background:var(--bg-0)}
.att-preview iframe{width:100%;height:420px;border:none;display:block;background:var(--bg-0)}

/* ── Theme popover ───────────────────────────────────────────────────────── */
#theme-pop{position:fixed;background:var(--bg-0);border:1px solid var(--border);border-radius:8px;padding:.65rem .75rem;box-shadow:0 8px 28px rgba(0,0,0,.6);z-index:1000;display:none;min-width:155px}
#theme-pop.open{display:block}
.tp-label{font-size:.63rem;color:var(--text-3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.4rem}
.tp-swatches{display:flex;gap:.32rem;margin-bottom:.55rem}
.tp-sw{width:20px;height:20px;border-radius:50%;cursor:pointer;border:2px solid transparent;transition:transform .1s,border-color .1s;flex-shrink:0}
.tp-sw:hover{transform:scale(1.18)}
.tp-sw.on{border-color:var(--text-1)}
.tp-mode{display:flex;align-items:center;gap:.45rem;font-size:.72rem;color:var(--text-2);cursor:pointer}
.mode-toggle{width:32px;height:18px;background:var(--bg-3);border-radius:9px;position:relative;border:1px solid var(--border);flex-shrink:0;transition:background .15s}
.mode-toggle::after{content:'';position:absolute;top:2px;left:2px;width:12px;height:12px;background:var(--text-2);border-radius:50%;transition:transform .15s,background .15s}
[data-mode="light"] .mode-toggle{background:var(--accent-bg)}
[data-mode="light"] .mode-toggle::after{transform:translateX(14px);background:var(--accent)}

/* ── Status bar ──────────────────────────────────────────────────────────── */
#statusbar{background:var(--bg-0);border-top:1px solid var(--border);padding:.18rem .9rem;font-size:.62rem;color:var(--text-3);display:flex;justify-content:space-between;flex-shrink:0}

@media (max-width: 900px){
  html,body{overflow:auto;height:auto}
  body{min-height:100vh}
  #app{flex-direction:column;overflow:visible}
  #sidebar,#panel-mid,#panel-detail{width:100%;border-right:0}
  #sidebar{max-height:38vh;border-bottom:1px solid var(--border)}
  #panel-mid{min-height:42vh;border-bottom:1px solid var(--border)}
  #panel-detail{min-height:60vh;overflow:visible}
  .review-row{grid-template-columns:1fr}
  .review-save{width:100%}
  #statusbar{position:sticky;bottom:0}
}
</style>
</head>
<body>
<div id="app">

  <!-- ── LEFT SIDEBAR ──────────────────────────────────────────────────── -->
  <div id="sidebar">
    <div class="sb-title">📧 Mrija Archive</div>

    <div class="mb-list">
      <?php
        // "All mailboxes" entry
        $allUrl = buildUrl(['mailbox'=>'','page'=>'1','id'=>'','smb'=>'']);
      ?>
      <a class="mb-item <?= $mailbox === '' ? 'cur' : '' ?>"
         href="<?= esc($allUrl) ?>">
        <span class="mb-name">Alle Postfächer</span>
        <span class="mb-meta"><?= number_format($total) ?> E-Mails</span>
      </a>
      <div class="mb-sep"></div>
      <?php foreach ($mailboxStats as $ms):
        $mb    = $ms['mailbox'];
        $cnt   = (int)$ms['email_count'];
        $bytes = (int)$ms['total_bytes'];
        $mbUrl = buildUrl(['mailbox'=>$mb,'page'=>'1','id'=>'','smb'=>'']);
      ?>
        <a class="mb-item <?= $mailbox === $mb ? 'cur' : '' ?>"
           href="<?= esc($mbUrl) ?>">
          <span class="mb-name"><?= esc($mb) ?></span>
          <span class="mb-meta"><?= number_format($cnt) ?> · <?= fmtBytes($bytes) ?></span>
        </a>
      <?php endforeach ?>
    </div>

    <div class="sb-foot">
      <span class="sb-stats">
        <?= number_format($total) ?> gesamt<br>
        <span title="Letzter Import"><?= esc(substr((string)$lastImport,0,10)) ?></span>
      </span>
      <button class="theme-btn" id="update-btn" onclick="checkUpdate(event)" title="Auf Updates prüfen">⟳</button>
      <button class="theme-btn" id="theme-btn" onclick="toggleThemePop(event)" title="Farbschema">🎨</button>
      <a href="/logout.php" class="theme-btn" title="Abmelden" style="text-decoration:none">⏻</a>
    </div>
  </div>

  <!-- ── MIDDLE PANEL ──────────────────────────────────────────────────── -->
  <div id="panel-mid">
    <div class="search-wrap">
      <form id="sf" method="get" action="" class="search-row">
        <?php if ($mailbox !== ''): ?>
          <input type="hidden" name="mailbox" value="<?= esc($mailbox) ?>">
        <?php endif ?>
        <input id="q" name="q" type="text" value="<?= esc($q) ?>"
               placeholder="Suchen — Betreff, Absender, Text…" autocomplete="off">
        <button class="search-btn" type="submit">↵</button>
      </form>
    </div>

    <div class="filter-row">
      <input type="date" form="sf" name="date_from" class="fi"
             value="<?= esc($dateFrom) ?>" title="Von Datum">
      <span class="date-sep">–</span>
      <input type="date" form="sf" name="date_to" class="fi"
             value="<?= esc($dateTo) ?>" title="Bis Datum">
      <a class="att-toggle <?= $hasAtt ? 'on' : '' ?>"
         href="<?= esc(buildUrl(['has_att'=>$hasAtt?'':'1','page'=>'1'])) ?>"
         title="Nur E-Mails mit Anhängen">📎</a>
      <select form="sf" name="sort" class="fi" onchange="document.getElementById('sf').submit()">
        <?php foreach ($sortOptions as $key => $opt): ?>
          <option value="<?= esc($key) ?>" <?= $sort===$key?'selected':'' ?>><?= esc($opt['label']) ?></option>
        <?php endforeach ?>
      </select>
    </div>

    <?php if ($totalFound > 0): ?>
    <div class="list-head">
      <span>
        <?php
          $from = $offset + 1; $to = min($offset + count($results), $totalFound);
          echo number_format($from).'–'.number_format($to).' / '.number_format($totalFound);
          if ($q !== '') echo ' für <em>'.esc($q).'</em>';
        ?>
      </span>
      <a href="<?= esc(buildUrl(['export'=>'csv','page'=>''])) ?>" title="Als CSV exportieren">⬇ CSV</a>
    </div>
    <?php else: ?>
    <div class="list-head">
      <span><?= $q !== '' ? 'Keine Ergebnisse für <em>'.esc($q).'</em>' : 'Keine E-Mails' ?></span>
    </div>
    <?php endif ?>

    <div class="email-list">
      <?php if (empty($results) && $totalFound === 0): ?>
        <div class="empty-state">
          <div class="icon">📭</div>
          <div><?= $q !== '' ? 'Keine Treffer' : 'Leer' ?></div>
        </div>
      <?php else: ?>
        <?php foreach ($results as $idx => $r):
          $isActive = ($r['stable_id'] === $selectedId && $r['mailbox'] === $selMailbox);
          $link = buildUrl(['id'=>$r['stable_id'],'smb'=>$r['mailbox'],'page'=>(string)$page]);
          $attCount = (int)$r['att_count'];
        ?>
        <div class="erow <?= $isActive ? 'cur' : '' ?>"
             data-href="<?= esc($link) ?>"
             data-idx="<?= $idx ?>"
             onclick="window.location=this.dataset.href">
          <div class="e-subj"><?= highlight(esc($r['subject']?:'(kein Betreff)'),$q) ?></div>
          <div class="e-meta">
            <span><?= highlight(esc($r['from_addr']),$q) ?></span>
            <span>·</span>
            <span><?= esc(substr($r['date'],0,10)) ?></span>
            <?php if ($mailbox === ''): ?>
              <span class="mb-tag"><?= esc($r['mailbox']) ?></span>
            <?php endif ?>
            <?php if ($attCount > 0): ?>
              <span class="att-badge">📎<?= $attCount > 1 ? " $attCount" : '' ?></span>
            <?php endif ?>
          </div>
          <div class="e-prev"><?= highlight(esc($r['preview']),$q) ?></div>
        </div>
        <?php endforeach ?>
      <?php endif ?>
    </div>

    <?php if ($totalPages > 1): ?>
    <div class="pagination">
      <?php if ($page > 1): ?>
        <a href="<?= esc(buildUrl(['page'=>'1'])) ?>">«</a>
        <a href="<?= esc(buildUrl(['page'=>(string)($page-1)])) ?>">‹</a>
      <?php else: ?><span class="dis">«</span><span class="dis">‹</span><?php endif ?>
      <?php
        $s = max(1,$page-2); $e = min($totalPages,$page+2);
        if ($s > 1) echo '<span>…</span>';
        for ($i=$s; $i<=$e; $i++):
      ?>
        <?php if ($i===$page): ?><span class="cur"><?= $i ?></span>
        <?php else: ?><a href="<?= esc(buildUrl(['page'=>(string)$i])) ?>"><?= $i ?></a><?php endif ?>
      <?php endfor; if ($e < $totalPages) echo '<span>…</span>'; ?>
      <?php if ($page < $totalPages): ?>
        <a href="<?= esc(buildUrl(['page'=>(string)($page+1)])) ?>">›</a>
        <a href="<?= esc(buildUrl(['page'=>(string)$totalPages])) ?>">»</a>
      <?php else: ?><span class="dis">›</span><span class="dis">»</span><?php endif ?>
    </div>
    <?php endif ?>
  </div>

  <!-- ── DETAIL PANEL ───────────────────────────────────────────────────── -->
  <div id="panel-detail">
    <?php if ($email): ?>
      <div class="d-content">
        <div class="d-subject"><?= highlight(esc($email['subject']?:'(kein Betreff)'),$q) ?></div>
        <div class="d-meta">
          <span class="d-lbl">Von</span>      <span class="d-val"><?= highlight(esc($email['from_addr']),$q) ?></span>
          <span class="d-lbl">An</span>       <span class="d-val"><?= esc($email['to_addrs']) ?></span>
          <?php if ($email['cc_addrs']): ?>
          <span class="d-lbl">Cc</span>       <span class="d-val"><?= esc($email['cc_addrs']) ?></span>
          <?php endif ?>
          <span class="d-lbl">Datum</span>    <span class="d-val"><?= esc($email['date']) ?></span>
          <span class="d-lbl">Postfach</span> <span class="d-val"><?= esc($email['mailbox']) ?></span>
          <span class="d-lbl">Größe</span>    <span class="d-val"><?= fmtBytes((int)$email['total_size_bytes']) ?></span>
        </div>

        <div class="review-box"
             data-mailbox="<?= esc($email['mailbox']) ?>"
             data-email-id="<?= esc($email['stable_id']) ?>">
          <div class="review-row">
            <select class="review-select" id="review-decision" aria-label="Review decision">
              <option value="unsure" <?= (($reviewDecision['decision'] ?? '') === 'unsure' || !$reviewDecision) ? 'selected' : '' ?>>Unsure</option>
              <option value="keep" <?= (($reviewDecision['decision'] ?? '') === 'keep') ? 'selected' : '' ?>>Keep</option>
              <option value="delete" <?= (($reviewDecision['decision'] ?? '') === 'delete') ? 'selected' : '' ?>>Delete</option>
            </select>
            <textarea class="review-notes" id="review-notes" maxlength="5000" placeholder="Review notes"><?= esc((string)($reviewDecision['notes'] ?? '')) ?></textarea>
            <button class="review-save" id="review-save" type="button">Save</button>
            <div class="review-status" id="review-status">
              <?php if ($reviewDecision): ?>
                Saved <?= esc(substr((string)$reviewDecision['updated_at'], 0, 16)) ?>
              <?php endif ?>
            </div>
          </div>
        </div>

        <div class="d-body"><?= highlight(esc($email['body_text']?:'(kein Text)'),$q) ?></div>

        <?php if (!empty($email['attachments'])): ?>
        <div class="att-section">
          <div class="att-sec-title">Anhänge (<?= count($email['attachments']) ?>)</div>
          <?php
            $multiAtt = count($email['attachments']) > 1;
            foreach ($email['attachments'] as $ai => $a):
              $dlBase = ['mailbox'=>$a['mailbox'],'sha256'=>$a['sha256']];
              $dlUrl  = '/download.php?'.http_build_query($dlBase);
              $inUrl  = '/download.php?'.http_build_query($dlBase + ['inline'=>'1']);
              $vtRow  = $vtStatuses[$a['sha256']] ?? null;
              $vtSt   = $vtRow['status'] ?? 'none';
              $isImg  = in_array($a['mime'],['image/jpeg','image/png','image/gif','image/webp','image/svg+xml'],true);
              $isPdf  = $a['mime'] === 'application/pdf';
              $hasPreview = $isImg || $isPdf;
              $previewOpen = $hasPreview && !$multiAtt;
              $blockId = 'att-'.$ai;
          ?>
          <div class="att-block">
            <div class="att-hdr" onclick="toggleAtt('<?= $blockId ?>')" id="<?= $blockId ?>-hdr">
              <span>📎</span>
              <span class="att-fname"><?= esc($a['original_filename'] ?: basename($a['stored_path'])) ?></span>
              <span class="att-fsize"><?= fmtBytes((int)$a['size']) ?></span>

              <?php if ($vtSt === 'clean'): ?>
                <span class="vt-badge vt-clean">✓ Sauber</span>
              <?php elseif ($vtSt === 'infected'): ?>
                <span class="vt-badge vt-infected">⚠ Infiziert</span>
              <?php elseif ($vtSt === 'pending'): ?>
                <span class="vt-badge vt-pending">⏳</span>
              <?php else: ?>
                <span class="vt-none">○</span>
              <?php endif ?>

              <?php if ($vtSt === 'infected'): ?>
                <span class="att-blocked">Gesperrt</span>
              <?php else: ?>
                <a class="att-dl" href="<?= esc($dlUrl) ?>" onclick="event.stopPropagation()">↓ Download</a>
              <?php endif ?>
            </div>

            <?php if ($hasPreview): ?>
            <div class="att-preview <?= $previewOpen ? 'open' : '' ?>" id="<?= $blockId ?>-prev">
              <?php if ($isImg): ?>
                <img src="<?= esc($inUrl) ?>" alt="<?= esc($a['original_filename']) ?>"
                     onclick="window.open('<?= esc($dlUrl) ?>','_blank')">
              <?php else: ?>
                <iframe src="<?= esc($inUrl) ?>" loading="lazy"></iframe>
              <?php endif ?>
            </div>
            <?php endif ?>
          </div>
          <?php endforeach ?>
        </div>
        <?php endif ?>
      </div>

    <?php else: ?>
      <div class="d-empty">
        <div class="icon">✉️</div>
        <div>E-Mail auswählen</div>
        <div style="font-size:.68rem;color:var(--text-3);margin-top:.3rem">
          <kbd>j</kbd><kbd>k</kbd> navigieren &nbsp;·&nbsp; <kbd>Enter</kbd> öffnen &nbsp;·&nbsp; <kbd>/</kbd> suchen
        </div>
      </div>
    <?php endif ?>
  </div>

</div><!-- #app -->

<!-- ── Status bar ──────────────────────────────────────────────────────── -->
<div id="statusbar">
  <span><?= number_format($total) ?> E-Mails · Import: <?= esc(substr((string)$lastImport,0,10)) ?></span>
  <span style="color:var(--text-3)">MariaDB · PHP</span>
</div>

<!-- ── Update popover ──────────────────────────────────────────────────── -->
<div id="update-pop" style="position:fixed;background:var(--bg-0);border:1px solid var(--border);
  border-radius:8px;padding:.8rem 1rem;box-shadow:0 8px 28px rgba(0,0,0,.6);z-index:1001;
  display:none;min-width:220px;font-size:.82rem;color:var(--text-2)">
  <div id="update-pop-body">Prüfe…</div>
</div>

<!-- ── Theme picker popover ────────────────────────────────────────────── -->
<div id="update-pop-ref"></div>
<div id="theme-pop">
  <div class="tp-label">Akzentfarbe</div>
  <div class="tp-swatches" id="tp-swatches"></div>
  <div class="tp-label" style="margin-top:.1rem">Modus</div>
  <div class="tp-mode" onclick="toggleMode()">
    <div class="mode-toggle"></div>
    <span id="mode-label">Hell</span>
  </div>
</div>

<script>
// ── Theme picker ──────────────────────────────────────────────────────────
const ACCENTS=[
  {key:'',     color:'#c0c0c0',label:'Grau (Standard)'},
  {key:'blue', color:'#4a90d9',label:'Blau'},
  {key:'teal', color:'#2a9d8f',label:'Türkis'},
  {key:'amber',color:'#d4900a',label:'Bernstein'},
  {key:'sage', color:'#6a9f6a',label:'Salbei'},
  {key:'rose', color:'#c0606a',label:'Rose'},
];

function buildSwatches(){
  const cur=localStorage.getItem('mrija-accent')||'';
  const c=document.getElementById('tp-swatches');
  c.innerHTML='';
  ACCENTS.forEach(a=>{
    const s=document.createElement('div');
    s.className='tp-sw'+(a.key===cur?' on':'');
    s.style.background=a.color;
    s.title=a.label;
    s.addEventListener('click',e=>{
      e.stopPropagation();
      localStorage.setItem('mrija-accent',a.key);
      const h=document.documentElement;
      a.key?h.setAttribute('data-accent',a.key):h.removeAttribute('data-accent');
      c.querySelectorAll('.tp-sw').forEach(x=>x.classList.remove('on'));
      s.classList.add('on');
    });
    c.appendChild(s);
  });
}

function toggleThemePop(e){
  e.stopPropagation();
  const pop=document.getElementById('theme-pop');
  if(pop.classList.contains('open')){pop.classList.remove('open');return;}
  buildSwatches();
  const isLight=document.documentElement.getAttribute('data-mode')==='light';
  document.getElementById('mode-label').textContent=isLight?'Hell':'Dunkel';
  const btn=document.getElementById('theme-btn');
  const r=btn.getBoundingClientRect();
  pop.style.bottom=(window.innerHeight-r.top+4)+'px';
  pop.style.left=r.left+'px';
  pop.classList.add('open');
}

function toggleMode(){
  const h=document.documentElement;
  const isLight=h.getAttribute('data-mode')==='light';
  if(isLight){h.removeAttribute('data-mode');localStorage.setItem('mrija-mode','dark');}
  else{h.setAttribute('data-mode','light');localStorage.setItem('mrija-mode','light');}
  document.getElementById('mode-label').textContent=isLight?'Dunkel':'Hell';
}

document.addEventListener('click',e=>{
  document.getElementById('theme-pop').classList.remove('open');
  const up=document.getElementById('update-pop');
  if(up&&!up.contains(e.target)&&e.target.id!=='update-btn')up.style.display='none';
});

// ── Activity logging ──────────────────────────────────────────────────────
function logEvent(d){
  try{navigator.sendBeacon('/api/log-event.php',JSON.stringify(d));}catch(e){}
}

document.addEventListener('DOMContentLoaded',()=>logEvent({type:'app_start'}));
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='hidden')logEvent({type:'app_stop'});
});
document.querySelectorAll('.erow').forEach(r=>{
  r.addEventListener('click',()=>logEvent({type:'email_opened',id:r.dataset.id||''}));
});
document.getElementById('sf')?.addEventListener('submit',()=>{
  logEvent({type:'search',q:document.getElementById('q')?.value||''});
});
document.querySelectorAll('a[href*="download.php"]').forEach(a=>{
  a.addEventListener('click',()=>{
    const u=new URL(a.href,location.href);
    logEvent({type:'download',sha256:u.searchParams.get('sha256')||''});
  });
});

// ── Review decision save ─────────────────────────────────────────────────
document.getElementById('review-save')?.addEventListener('click',async()=>{
  const box=document.querySelector('.review-box');
  const decision=document.getElementById('review-decision');
  const notes=document.getElementById('review-notes');
  const btn=document.getElementById('review-save');
  const status=document.getElementById('review-status');
  const csrf=document.querySelector('meta[name="csrf-token"]')?.content||'';
  if(!box||!decision||!notes||!btn||!status)return;
  btn.disabled=true;
  status.className='review-status';
  status.textContent='Saving...';
  try{
    const res=await fetch('/api/review-decision.php',{
      method:'POST',
      headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
      body:JSON.stringify({
        mailbox:box.dataset.mailbox,
        email_stable_id:box.dataset.emailId,
        decision:decision.value,
        notes:notes.value,
      }),
    });
    const j=await res.json().catch(()=>({ok:false,error:'invalid_response'}));
    if(!res.ok||!j.ok)throw new Error(j.error||'save_failed');
    status.className='review-status ok';
    status.textContent='Saved';
    logEvent({type:'review_decision',id:box.dataset.emailId,decision:decision.value});
  }catch(err){
    status.className='review-status err';
    status.textContent='Save failed';
  }finally{
    btn.disabled=false;
  }
});

// ── Update check ──────────────────────────────────────────────────────────
async function checkUpdate(e){
  e.stopPropagation();
  const btn=document.getElementById('update-btn');
  const pop=document.getElementById('update-pop');
  const body=document.getElementById('update-pop-body');
  const r=btn.getBoundingClientRect();
  pop.style.bottom=(window.innerHeight-r.top+4)+'px';
  pop.style.left=r.left+'px';
  pop.style.display='block';
  body.innerHTML='<span style="color:var(--text-3)">Prüfe auf Updates…</span>';
  try{
    const res=await fetch('/api/check-update.php');
    const j=await res.json();
    if(!j.available){
      body.innerHTML='<span style="color:var(--text-3)">Kein Update-Server konfiguriert.</span>';
      return;
    }
    const m=j.manifest;
    body.innerHTML=`<div style="margin-bottom:.5rem;font-weight:600;color:var(--text-1)">Update verfügbar</div>
<div style="color:var(--text-3);margin-bottom:.75rem;font-size:.77rem">Version: ${m.version}</div>
<button id="apply-update-btn" style="background:var(--accent);border:none;border-radius:5px;
  color:#fff;cursor:pointer;font-size:.82rem;padding:.4rem .8rem;width:100%">
  Jetzt installieren
</button>`;
    document.getElementById('apply-update-btn').onclick=()=>applyUpdate(m);
  }catch(err){
    body.innerHTML='<span style="color:#c0606a">Fehler beim Prüfen.</span>';
  }
}

async function applyUpdate(manifest){
  const body=document.getElementById('update-pop-body');
  body.innerHTML='<span style="color:var(--text-3)">Installiere… bitte warten.</span>';
  try{
    const res=await fetch('/api/apply-update.php',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({manifest}),
    });
    const j=await res.json();
    if(j.ok){
      body.innerHTML='<span style="color:#6a9a6a">&#10003; Update installiert! Seite wird neu geladen…</span>';
      logEvent({type:'update_applied',version:j.version});
      setTimeout(()=>location.reload(),1500);
    }else{
      body.innerHTML=`<span style="color:#c0606a">Fehler: ${j.error||'unbekannt'}</span>`;
    }
  }catch(err){
    body.innerHTML='<span style="color:#c0606a">Netzwerkfehler.</span>';
  }
}

// ── Attachment preview toggle ─────────────────────────────────────────────
function toggleAtt(id){
  const p=document.getElementById(id+'-prev');
  if(p)p.classList.toggle('open');
}

// ── Keyboard navigation ───────────────────────────────────────────────────
const rows=Array.from(document.querySelectorAll('.erow'));
let fi=rows.findIndex(r=>r.classList.contains('cur'));

function moveFocus(d){
  const n=fi+d;
  if(n<0||n>=rows.length)return;
  fi=n;
  rows[fi].scrollIntoView({block:'nearest'});
  rows.forEach((r,i)=>r.style.outline=i===fi?'1px solid var(--accent)':'');
}

document.addEventListener('keydown',e=>{
  const tag=document.activeElement?.tagName;
  if(tag==='INPUT'||tag==='SELECT'||tag==='TEXTAREA'){
    if(e.key==='Escape')document.activeElement.blur();
    return;
  }
  switch(e.key){
    case 'j':case 'ArrowDown':e.preventDefault();moveFocus(+1);break;
    case 'k':case 'ArrowUp':e.preventDefault();moveFocus(-1);break;
    case 'Enter':if(fi>=0)window.location=rows[fi].dataset.href;break;
    case '/':e.preventDefault();document.getElementById('q').focus();break;
  }
});
</script>
</body>
</html>
