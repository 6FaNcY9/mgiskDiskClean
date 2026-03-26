<?php
/**
 * web/public/index.php — Single entry point for the mailbox review app.
 *
 * Minimal, framework-free PHP 8.3 front controller.
 * Requires web/config/local.php to be present; fails safely if missing.
 *
 * Auth model (Task 5):
 *   - Unauthenticated requests to any route redirect to /login.php
 *   - Two roles: 'admin' and 'coworker'
 *   - Admin-only:  GET/POST /admin/* (import, export, all-users dashboards)
 *   - Coworker:    POST /review/update (mark decisions; records updated_by)
 *   - CSRF token required on all POST routes (validated via CsrfGuard::enforce())
 *
 * Route table:
 *   GET  /                → dashboard (auth required, any role)
 *   GET  /admin/reports   → list imported reports (admin only)
 *   POST /admin/import    → import a report from data_dir (admin only, CSRF)
 *   POST /review/update   → update a decision (coworker only, CSRF)
 *   POST /logout          → destroy session (any authenticated user, CSRF)
 */

declare(strict_types=1);

// ── Strict error handling ──────────────────────────────────────────────────
ini_set('display_errors', '0');
ini_set('log_errors', '1');
error_reporting(E_ALL);

set_exception_handler(function (Throwable $e): void {
    http_response_code(500);
    // Non-sensitive message only — no stack trace in HTTP response
    echo 'Internal server error. Please contact the administrator.';
    // Log the real error server-side
    error_log('Unhandled exception: ' . $e->getMessage() . ' in ' . $e->getFile() . ':' . $e->getLine());
    exit(1);
});

set_error_handler(function (int $errno, string $errstr, string $errfile, int $errline): bool {
    throw new ErrorException($errstr, 0, $errno, $errfile, $errline);
});

// ── Config bootstrap ───────────────────────────────────────────────────────
$configPath = dirname(__DIR__) . '/config/local.php';

if (!is_file($configPath)) {
    http_response_code(500);
    // Safe failure: no path disclosure, no stack trace
    echo 'Service unavailable: configuration missing. Please contact the administrator.';
    error_log('Missing config file: ' . $configPath);
    exit(1);
}

/** @var array<string,mixed> $config */
$config = require $configPath;

// ── Security headers ───────────────────────────────────────────────────────
header('X-Frame-Options: DENY');
header('X-Content-Type-Options: nosniff');
header('Referrer-Policy: same-origin');
header("Content-Security-Policy: default-src 'self'; style-src 'self' 'unsafe-inline'");

// ── Maintenance mode check ─────────────────────────────────────────────────
$maintenanceFlag = dirname(__DIR__) . '/data/.maintenance';
if (is_file($maintenanceFlag)) {
    http_response_code(503);
    header('Retry-After: 3600');
    echo 'Service temporarily unavailable for maintenance. Please try again later.';
    exit(0);
}

// ── Autoload (simple PSR-4-like; no Composer) ─────────────────────────────
spl_autoload_register(function (string $class): void {
    $prefix = 'MailReview\\';
    $baseDir = dirname(__DIR__) . '/src/';
    if (!str_starts_with($class, $prefix)) {
        return;
    }
    $relative = substr($class, strlen($prefix));
    $file = $baseDir . str_replace('\\', '/', $relative) . '.php';
    if (is_file($file)) {
        require $file;
    }
});

// ── Session bootstrap ──────────────────────────────────────────────────────
// Must be started before any output / route dispatch.
$session = new \MailReview\Auth\SessionManager($config['session'] ?? []);
$session->start();

// ── Request parsing ────────────────────────────────────────────────────────────
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
$path   = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?? '/';
// ── I18n ───────────────────────────────────────────────────────────────────────
$i18n = new \MailReview\Services\I18nService($_SESSION['lang'] ?? 'en');

// ── GET /lang/{lang} — switch language ─────────────────────────────────────────
if (preg_match('#^/lang/([a-z]{2})$#', $path, $m) && $method === 'GET') {
    $lang = $m[1];
    if (in_array($lang, ['en', 'de', 'uk'])) {
        $_SESSION['lang'] = $lang;
    }
    header('Location: ' . ($_SERVER['HTTP_REFERER'] ?? '/'));
    exit(0);
}

// ── CSRF guard (shared instance for all routes) ────────────────────────────
$csrf = new \MailReview\Auth\CsrfGuard();

// ── PDO factory (lazy; only built when a route needs DB) ──────────────────
$makePdo = static function () use ($config): \PDO {
    $dbCfg  = $config['db'] ?? [];
    $socket = $dbCfg['socket'] ?? '';
    if ($socket && file_exists($socket)) {
        $dsn = "mysql:unix_socket={$socket};dbname={$dbCfg['dbname']};charset={$dbCfg['charset']}";
    } else {
        $host = $dbCfg['host'] ?? '127.0.0.1';
        $port = $dbCfg['port'] ?? 3306;
        $dsn  = "mysql:host={$host};port={$port};dbname={$dbCfg['dbname']};charset={$dbCfg['charset']}";
    }
    return new \PDO($dsn, $dbCfg['user'] ?? '', $dbCfg['password'] ?? '', [
        \PDO::ATTR_ERRMODE            => \PDO::ERRMODE_EXCEPTION,
        \PDO::ATTR_DEFAULT_FETCH_MODE => \PDO::FETCH_ASSOC,
        \PDO::ATTR_EMULATE_PREPARES   => false,
    ]);
};

// ── Route dispatch ─────────────────────────────────────────────────────────

// ── POST /logout — destroy session (any authenticated user) ───────────────
if ($path === '/logout' && $method === 'POST') {
    // Require auth (redirect to login if not authenticated)
    $session->requireAuth('/login.php');
    // Validate CSRF to prevent CSRF-triggered logout
    $csrf->enforce();
    $session->logout();
    header('Location: /login.php', true, 302);
    exit(0);
}

if ($path === '/' && $method === 'GET') {
    $session->requireAuth('/login.php');

    $role        = $session->getRole();
    $displayName = htmlspecialchars($session->getDisplayName(), ENT_QUOTES, 'UTF-8');
    $csrfToken   = htmlspecialchars($csrf->getToken(), ENT_QUOTES, 'UTF-8');
    $lang        = $i18n->getLang();

    http_response_code(200);
    header('Content-Type: text/html; charset=utf-8');
    ?><!DOCTYPE html>
<html lang="<?= $lang ?>">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title><?= $i18n->t('dashboard') ?> — Mailbox Review</title>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, -apple-system, sans-serif; background: #f5f5f5; color: #222; }
        header {
            background: #1a1a2e;
            color: #fff;
            padding: 0.75rem 2rem;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        header h1 { font-size: 1.1rem; font-weight: 600; }
        header .controls { display: flex; align-items: center; gap: 1rem; }
        header .user-info { font-size: 0.875rem; opacity: 0.85; }
        header .lang-switch a { color: #fff; text-decoration: none; margin-left: 0.5rem; opacity: 0.6; font-size: 0.8rem; }
        header .lang-switch a.active { opacity: 1; font-weight: bold; text-decoration: underline; }
        header .logout-form { display: inline; margin-left: 1rem; }
        header .logout-btn {
            background: transparent;
            border: 1px solid rgba(255,255,255,0.5);
            color: #fff;
            padding: 0.25rem 0.75rem;
            border-radius: 4px;
            cursor: pointer;
            font-size: 0.8rem;
        }
        header .logout-btn:hover { background: rgba(255,255,255,0.1); }
        main { padding: 2rem; max-width: 900px; margin: 0 auto; }
        .card {
            background: #fff;
            border: 1px solid #ddd;
            border-radius: 6px;
            padding: 1.5rem;
            margin-bottom: 1.5rem;
        }
        .card h2 { font-size: 1rem; font-weight: 600; margin-bottom: 0.75rem; color: #333; }
        .role-badge {
            display: inline-block;
            padding: 0.2rem 0.6rem;
            border-radius: 3px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .role-admin    { background: #ffe0b2; color: #e65100; }
        .role-coworker { background: #e3f2fd; color: #1565c0; }
        .btn {
            display: inline-block;
            padding: 0.5rem 1rem;
            background: #0066cc;
            color: #fff;
            text-decoration: none;
            border-radius: 4px;
            font-size: 0.9rem;
            margin-right: 0.5rem;
            margin-bottom: 0.5rem;
        }
        .btn:hover { background: #0055aa; }
        p { line-height: 1.5; color: #555; margin-bottom: 0.5rem; }
    </style>
</head>
<body>
<header>
    <h1>Mailbox Review</h1>
    <div class="controls">
        <div class="lang-switch">
            <a href="/lang/en" class="<?= $lang==='en'?'active':'' ?>">EN</a>
            <a href="/lang/de" class="<?= $lang==='de'?'active':'' ?>">DE</a>
            <a href="/lang/uk" class="<?= $lang==='uk'?'active':'' ?>">UK</a>
        </div>
        <span class="user-info">
            <?php if ($displayName !== ''): ?>
                <?= $displayName ?> &mdash;
            <?php endif; ?>
            <span class="role-badge role-<?= htmlspecialchars($role, ENT_QUOTES, 'UTF-8') ?>">
                <?= $i18n->t('role_' . $role) ?>
            </span>
        </span>
        <form class="logout-form" method="post" action="/logout">
            <input type="hidden" name="csrf_token" value="<?= $csrfToken ?>">
            <button type="submit" class="logout-btn"><?= $i18n->t('logout') ?></button>
        </form>
    </div>
</header>
<main>
    <div class="card">
        <h2><?= $i18n->t('welcome') ?></h2>
        <p><?= $i18n->t('welcome') ?>, <strong><?= $displayName !== '' ? $displayName : $role ?></strong>.</p>
        
        <div style="margin-top: 1.5rem">
            <a href="/reports" class="btn"><?= $i18n->t('view_reports') ?></a>
            
            <?php if ($role === 'admin'): ?>
            <a href="/admin/overview" class="btn"><?= $i18n->t('admin_overview') ?></a>
            <?php endif; ?>
        </div>
    </div>
</main>
</body>
</html>
    <?php
    exit(0);
}

// ── GET /reports — List reports (authenticated users, any role) ────────────────
if ($path === '/reports' && $method === 'GET') {
    $session->requireAuth('/login.php');
    
    $role        = $session->getRole();
    $displayName = htmlspecialchars($session->getDisplayName(), ENT_QUOTES, 'UTF-8');
    $lang        = $i18n->getLang();
    
    try {
        $pdo = $makePdo();
        $reviewService = new \MailReview\Services\ReviewService($pdo);
        $reports = $reviewService->getReports();
    } catch (\PDOException $e) {
        http_response_code(500);
        echo 'Database error.';
        error_log('Reports DB error: ' . $e->getMessage());
        exit(1);
    }
    
    http_response_code(200);
    header('Content-Type: text/html; charset=utf-8');
    ?><!DOCTYPE html>
<html lang="<?= $lang ?>">
<head>
    <meta charset="UTF-8">
    <title><?= $i18n->t('reports_list') ?> — Mailbox Review</title>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, -apple-system, sans-serif; background: #f5f5f5; color: #222; }
        header { background: #1a1a2e; color: #fff; padding: 0.75rem 2rem; display: flex; align-items: center; justify-content: space-between; }
        header h1 { font-size: 1.1rem; font-weight: 600; }
        header a { color: #fff; text-decoration: none; }
        main { padding: 2rem; max-width: 900px; margin: 0 auto; }
        .card { background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 1.5rem; margin-bottom: 1.5rem; }
        table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
        th, td { text-align: left; padding: 0.75rem; border-bottom: 1px solid #eee; }
        th { background: #f9f9f9; font-weight: 600; font-size: 0.9rem; }
        .btn { display: inline-block; padding: 0.4rem 0.8rem; background: #0066cc; color: #fff; text-decoration: none; border-radius: 4px; font-size: 0.85rem; }
        .btn:hover { background: #0055aa; }
        .progress-bar { background: #eee; border-radius: 3px; height: 6px; width: 100px; display: inline-block; overflow: hidden; }
        .progress-fill { background: #4caf50; height: 100%; }
    </style>
</head>
<body>
<header>
    <h1><a href="/"><?= $i18n->t('dashboard') ?></a> / <?= $i18n->t('reports_list') ?></h1>
    <div>
         <span style="font-size:0.875rem; opacity:0.8"><?= $displayName ?></span>
    </div>
</header>
<main>
    <div class="card">
        <h2><?= $i18n->t('reports_list') ?></h2>
        <table>
            <thead>
                <tr>
                    <th><?= $i18n->t('mailbox') ?></th>
                    <th><?= $i18n->t('imported_at') ?></th>
                    <th><?= $i18n->t('total_emails') ?></th>
                    <th><?= $i18n->t('progress') ?></th>
                    <th><?= $i18n->t('actions') ?></th>
                </tr>
            </thead>
            <tbody>
                <?php foreach ($reports as $r): 
                    $total = (int)$r['total_emails'];
                    $reviewed = (int)$r['reviewed_count'];
                    $pct = $total > 0 ? round(($reviewed / $total) * 100) : 0;
                ?>
                <tr>
                    <td><?= htmlspecialchars($r['mailbox']) ?></td>
                    <td><?= htmlspecialchars($r['imported_at']) ?></td>
                    <td><?= $total ?></td>
                    <td>
                        <div class="progress-bar" title="<?= $reviewed ?>/<?= $total ?>">
                            <div class="progress-fill" style="width: <?= $pct ?>%"></div>
                        </div>
                        <span style="font-size:0.8rem; margin-left:0.5rem"><?= $pct ?>%</span>
                    </td>
                    <td>
                        <a href="/review?report_id=<?= urlencode($r['report_id']) ?>" class="btn"><?= $i18n->t('review') ?></a>
                    </td>
                </tr>
                <?php endforeach; ?>
                <?php if (empty($reports)): ?>
                <tr><td colspan="5" style="text-align:center;color:#777">No reports found.</td></tr>
                <?php endif; ?>
            </tbody>
        </table>
    </div>
    <p><a href="/" style="color:#0066cc; text-decoration:none">&larr; <?= $i18n->t('back_dashboard') ?></a></p>
</main>
</body>
</html>
    <?php
    exit(0);
}

// ── POST /admin/import — import a report from data_dir into MySQL ──────────

// ── GET /review — Review UI (authenticated users) ──────────────────────────────
if ($path === '/review' && $method === 'GET') {
    $session->requireAuth('/login.php');
    
    $reportId = $_GET['report_id'] ?? '';
    if ($reportId === '') {
        header('Location: /reports');
        exit(0);
    }

    $role        = $session->getRole();
    $displayName = htmlspecialchars($session->getDisplayName(), ENT_QUOTES, 'UTF-8');
    $lang        = $i18n->getLang();
    $csrfToken   = htmlspecialchars($csrf->getToken(), ENT_QUOTES, 'UTF-8');

    $filters = [
        'decision'        => $_GET['decision'] ?? '',
        'only_duplicates' => !empty($_GET['only_duplicates']),
        'search'          => trim($_GET['search'] ?? ''),
    ];
    $page = max(1, (int)($_GET['page'] ?? 1));
    $perPage = 50;
    $offset = ($page - 1) * $perPage;

    try {
        $pdo = $makePdo();
        $dataDir       = rtrim((string)($config['data_dir'] ?? ''), '/');
        $reviewService = new \MailReview\Services\ReviewService($pdo);
        $totalEmails   = $reviewService->countReportEmails($reportId, $filters);
        $emails        = $reviewService->getReportEmails($reportId, $perPage, $offset, $filters);
        $totalPages    = max(1, (int)ceil($totalEmails / $perPage));

        // Build attachment map keyed by stable_id for the current page.
        // Gracefully empty when index.sqlite unavailable (Task 2b not yet run).
        $attachmentMap = [];  // [stable_id => list<array{sha256,size,original_filename}>]
        foreach ($emails as $e) {
            $sid = (string)($e['stable_id'] ?? '');
            if ($sid !== '') {
                $attachmentMap[$sid] = $reviewService->getEmailAttachments($reportId, $sid, $dataDir);
            }
        }
    } catch (\PDOException $e) {
        http_response_code(500);
        echo 'Database error.';
        error_log('Review list DB error: ' . $e->getMessage());
        exit(1);
    }

    http_response_code(200);
    header('Content-Type: text/html; charset=utf-8');
    ?><!DOCTYPE html>
<html lang="<?= $lang ?>">
<head>
    <meta charset="UTF-8">
    <title><?= $i18n->t('review') ?> — Mailbox Review</title>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, -apple-system, sans-serif; background: #f5f5f5; color: #222; }
        header { background: #1a1a2e; color: #fff; padding: 0.75rem 2rem; display: flex; align-items: center; justify-content: space-between; }
        header h1 { font-size: 1.1rem; font-weight: 600; }
        header a { color: #fff; text-decoration: none; }
        main { padding: 2rem; max-width: 1200px; margin: 0 auto; }
        .card { background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 1.5rem; margin-bottom: 1.5rem; }
        .filters { display: flex; gap: 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; }
        .filters input, .filters select { padding: 0.4rem; border: 1px solid #ccc; border-radius: 4px; }
        .btn-sm { padding: 0.3rem 0.6rem; font-size: 0.8rem; border: 1px solid #ccc; background: #fff; border-radius: 3px; cursor: not-allowed; color: #888; }
        table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
        th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid #eee; vertical-align: top; }
        th { background: #f9f9f9; font-weight: 600; }
        .decision-keep { color: green; font-weight: bold; }
        .decision-delete { color: red; font-weight: bold; }
        .decision-unsure { color: orange; font-weight: bold; }
        .note-input { width: 100%; padding: 0.3rem; border: 1px solid #ddd; border-radius: 3px; font-size: 0.85rem; }
        .pagination { display: flex; gap: 0.5rem; justify-content: center; margin-top: 1rem; }
        .pagination a { padding: 0.5rem 1rem; background: #fff; border: 1px solid #ddd; border-radius: 4px; color: #333; text-decoration: none; }
        .pagination a.active { background: #0066cc; color: #fff; border-color: #0066cc; }
        .status-msg { position: fixed; bottom: 1rem; right: 1rem; padding: 1rem; background: #333; color: #fff; border-radius: 4px; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
    </style>
</head>
<body>
<header>
    <h1><a href="/"><?= $i18n->t('dashboard') ?></a> / <a href="/reports"><?= $i18n->t('reports_list') ?></a> / <?= $i18n->t('review') ?></h1>
    <div><span style="font-size:0.875rem; opacity:0.8"><?= $displayName ?></span></div>
</header>
<main>
    <div class="card">
        <form method="get" class="filters">
            <input type="hidden" name="report_id" value="<?= htmlspecialchars($reportId) ?>">
            <select name="decision" onchange="this.form.submit()">
                <option value=""><?= $i18n->t('filter_all') ?></option>
                <option value="keep" <?= $filters['decision'] === 'keep' ? 'selected' : '' ?>><?= $i18n->t('decision_keep') ?></option>
                <option value="delete" <?= $filters['decision'] === 'delete' ? 'selected' : '' ?>><?= $i18n->t('decision_delete') ?></option>
                <option value="unsure" <?= $filters['decision'] === 'unsure' ? 'selected' : '' ?>><?= $i18n->t('decision_unsure') ?></option>
                <option value="none" <?= $filters['decision'] === 'none' ? 'selected' : '' ?>>Pending</option>
            </select>
            <label>
                <input type="checkbox" name="only_duplicates" value="1" <?= $filters['only_duplicates'] ? 'checked' : '' ?> onchange="this.form.submit()">
                <?= $i18n->t('filter_duplicates') ?>
            </label>
            <input type="text" name="search" placeholder="<?= $i18n->t('search_placeholder') ?>" value="<?= htmlspecialchars($filters['search']) ?>">
            <button type="submit" class="btn"><?= $i18n->t('apply_filters') ?></button>
            <div class="actions-bar" style="margin-top: 1rem; padding: 0.75rem; background: #f9f9f9; border: 1px solid #eee; border-radius: 4px; display: flex; gap: 0.5rem; align-items: center;">
                <span style="font-weight:600; font-size:0.9rem; color:#555;">Bulk Actions (Simulated):</span>
                <button type="button" class="btn-sm" disabled>Apply to filtered</button>
                <button type="button" class="btn-sm" disabled>Auto-decision duplicates</button>
                <button type="button" class="btn-sm" disabled>Find top savings</button>
            </div>
        </form>

        <table>
            <thead>
                <tr>
                    <th><?= $i18n->t('date') ?></th>
                    <th><?= $i18n->t('from') ?></th>
                    <th><?= $i18n->t('subject') ?></th>
                    <th><?= $i18n->t('size') ?></th>
                    <th>Attachments</th>
                    <th><?= $i18n->t('duplicates') ?></th>
                    <th style="width: 150px"><?= $i18n->t('decision') ?></th>
                    <th style="width: 200px"><?= $i18n->t('note') ?></th>
                </tr>
            </thead>
            <tbody>
                <?php foreach ($emails as $e): ?>
                <tr data-stable-id="<?= htmlspecialchars($e['stable_id']) ?>">
                    <td><?= htmlspecialchars($e['date']) ?></td>
                    <td><div style="max-width:150px; overflow:hidden; text-overflow:ellipsis" title="<?= htmlspecialchars($e['sender'] ?? '') ?>"><?= htmlspecialchars($e['sender'] ?? '') ?></div></td>
                    <td>
                        <div style="max-width:300px; overflow:hidden; text-overflow:ellipsis" title="<?= htmlspecialchars($e['subject'] ?? '') ?>"><?= htmlspecialchars($e['subject'] ?? '') ?></div>
                        <div style="font-size:0.75rem; color:#777">Folder: <?= htmlspecialchars($e['folder'] ?? '') ?></div>
                        <a href="/report/pdf/<?= htmlspecialchars($reportId) ?>/<?= htmlspecialchars($e['stable_id']) ?>" target="_blank" style="font-size:0.75rem">View PDF</a>
                    </td>
                    <td><?= number_format(((int)($e['total_size_bytes'] ?? 0)) / 1024, 1) ?> KB</td>
                    <td>
                        <?php
                        $atts = $attachmentMap[$e['stable_id']] ?? [];
                        $attCount = count($atts);
                        if ($attCount === 0): ?>
                        <span style="font-size:0.85rem; color:#aaa;">none</span>
                        <?php else: ?>
                        <div style="font-size:0.85rem; color:#444;">
                            <span style="font-weight:600;"><?= $attCount ?></span> file<?= $attCount !== 1 ? 's' : '' ?><br>
                            <?php foreach ($atts as $att): ?>
                            <a href="/download/attachment/<?= urlencode($reportId) ?>/<?= urlencode($e['stable_id']) ?>/<?= urlencode($att['sha256']) ?>"
                               style="font-size:0.75rem; word-break:break-all;"
                               title="<?= htmlspecialchars($att['mime']) ?> — <?= number_format($att['size'] / 1024, 1) ?> KB">
                                <?= htmlspecialchars($att['original_filename'] !== '' ? $att['original_filename'] : $att['sha256']) ?>
                            </a><br>
                            <?php endforeach; ?>
                        </div>
                        <?php endif; ?>
                    </td>
                    <td><?= $e['is_duplicate'] ? 'Yes (' . $e['dup_rank'] . ')' : 'No' ?></td>
                    <td>
                        <select class="decision-select" onchange="updateDecision('<?= $e['stable_id'] ?>', this.value, document.getElementById('note-<?= $e['stable_id'] ?>').value)">
                            <option value=""><?= $i18n->t('decision_none') ?></option>
                            <option value="keep" <?= $e['decision'] === 'keep' ? 'selected' : '' ?>><?= $i18n->t('decision_keep') ?></option>
                            <option value="delete" <?= $e['decision'] === 'delete' ? 'selected' : '' ?>><?= $i18n->t('decision_delete') ?></option>
                            <option value="unsure" <?= $e['decision'] === 'unsure' ? 'selected' : '' ?>><?= $i18n->t('decision_unsure') ?></option>
                        </select>
                        <?php if ($e['updated_by']): ?>
                            <div style="font-size:0.7rem; color:#999; margin-top:0.2rem"><?= htmlspecialchars($e['updated_by'] ?? '') ?></div>
                        <?php endif; ?>
                    </td>
                    <td>
                        <textarea id="note-<?= $e['stable_id'] ?>" class="note-input" rows="2" onblur="updateDecision('<?= $e['stable_id'] ?>', this.parentElement.previousElementSibling.querySelector('select').value, this.value)"><?= htmlspecialchars($e['note'] ?? '') ?></textarea>
                    </td>
                </tr>
                <?php endforeach; ?>
                <?php if (empty($emails)): ?>
                <tr><td colspan="8" style="text-align:center; padding: 2rem; color: #777">No emails found matching filters.</td></tr>
                <?php endif; ?>
            </tbody>
        </table>

        <?php if ($totalPages > 1): ?>
        <div class="pagination">
            <?php for ($p = 1; $p <= $totalPages; $p++): ?>
                <a href="?report_id=<?= urlencode($reportId) ?>&page=<?= $p ?>&decision=<?= urlencode($filters['decision']) ?>&only_duplicates=<?= $filters['only_duplicates'] ?>&search=<?= urlencode($filters['search']) ?>" class="<?= $p === $page ? 'active' : '' ?>"><?= $p ?></a>
            <?php endfor; ?>
        </div>
        <?php endif; ?>
    </div>
</main>
<div id="status-msg" class="status-msg">Saved</div>
<script>
    function updateDecision(stableId, decision, note) {
        if (!decision) return;

        fetch('/review/update', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': '<?= $csrfToken ?>'
            },
            body: JSON.stringify({
                report_id: '<?= $reportId ?>',
                stable_id: stableId,
                decision: decision,
                note: note
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'updated') {
                showStatus('Saved');
            } else {
                showStatus('Error: ' + (data.error || 'Unknown error'));
            }
        })
        .catch(error => {
            console.error('Error:', error);
            showStatus('Connection error');
        });
    }

    function showStatus(msg) {
        const el = document.getElementById('status-msg');
        el.textContent = msg;
        el.style.opacity = '1';
        setTimeout(() => { el.style.opacity = '0'; }, 2000);
    }
</script>
</body>
</html>
    <?php
    exit(0);
}

// ── GET /admin/overview — Admin overview ───────────────────────────────────────
if ($path === '/admin/overview' && $method === 'GET') {
    $session->requireAuth('/login.php');
    $session->requireRole('admin', '/login.php');

    $role        = $session->getRole();
    $displayName = htmlspecialchars($session->getDisplayName(), ENT_QUOTES, 'UTF-8');
    $lang        = $i18n->getLang();

    $filters = [
        'updated_by' => $_GET['updated_by'] ?? '',
        'decision'   => $_GET['decision'] ?? '',
    ];

    try {
        $pdo = $makePdo();
        $reviewService = new \MailReview\Services\ReviewService($pdo);
        $overview  = $reviewService->getAdminOverview($filters);
        $reviewers = $reviewService->getReviewers();
    } catch (\PDOException $e) {
        http_response_code(500);
        echo 'Database error.';
        error_log('Admin overview DB error: ' . $e->getMessage());
        exit(1);
    }
    
    http_response_code(200);
    header('Content-Type: text/html; charset=utf-8');
    ?><!DOCTYPE html>
<html lang="<?= $lang ?>">
<head>
    <meta charset="UTF-8">
    <title><?= $i18n->t('admin_overview') ?> — Mailbox Review</title>
    <style>
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: system-ui, -apple-system, sans-serif; background: #f5f5f5; color: #222; }
        header { background: #1a1a2e; color: #fff; padding: 0.75rem 2rem; display: flex; align-items: center; justify-content: space-between; }
        header h1 { font-size: 1.1rem; font-weight: 600; }
        header a { color: #fff; text-decoration: none; }
        main { padding: 2rem; max-width: 900px; margin: 0 auto; }
        .card { background: #fff; border: 1px solid #ddd; border-radius: 6px; padding: 1.5rem; margin-bottom: 1.5rem; }
        table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
        th, td { text-align: left; padding: 0.75rem; border-bottom: 1px solid #eee; }
        th { background: #f9f9f9; font-weight: 600; font-size: 0.9rem; }
        .filters { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
    </style>
</head>
<body>
<header>
    <h1><a href="/"><?= $i18n->t('dashboard') ?></a> / <?= $i18n->t('admin_overview') ?></h1>
    <div><span style="font-size:0.875rem; opacity:0.8"><?= $displayName ?></span></div>
</header>
<main>
    <div class="card">
        <h2><?= $i18n->t('admin_overview') ?></h2>
        
        <form method="get" class="filters">
            <select name="updated_by" onchange="this.form.submit()">
                <option value="">All Reviewers</option>
                <?php foreach ($reviewers as $r): ?>
                <option value="<?= htmlspecialchars($r) ?>" <?= $filters['updated_by'] === $r ? 'selected' : '' ?>><?= htmlspecialchars($r) ?></option>
                <?php endforeach; ?>
            </select>
            <select name="decision" onchange="this.form.submit()">
                <option value="">All Decisions</option>
                <option value="keep" <?= $filters['decision'] === 'keep' ? 'selected' : '' ?>>Keep</option>
                <option value="delete" <?= $filters['decision'] === 'delete' ? 'selected' : '' ?>>Delete</option>
                <option value="unsure" <?= $filters['decision'] === 'unsure' ? 'selected' : '' ?>>Unsure</option>
            </select>
        </form>

        <table>
            <thead>
                <tr>
                    <th><?= $i18n->t('decision') ?></th>
                    <th><?= $i18n->t('updated_by') ?></th>
                    <th>Count</th>
                    <th><?= $i18n->t('updated_at') ?></th>
                </tr>
            </thead>
            <tbody>
                <?php foreach ($overview as $row): ?>
                <tr>
                    <td><?= htmlspecialchars($row['decision']) ?></td>
                    <td><?= htmlspecialchars($row['updated_by']) ?></td>
                    <td><?= htmlspecialchars((string)$row['count']) ?></td>
                    <td><?= htmlspecialchars($row['last_updated']) ?></td>
                </tr>
                <?php endforeach; ?>
                <?php if (empty($overview)): ?>
                <tr><td colspan="4" style="text-align:center; color:#777">No decisions found.</td></tr>
                <?php endif; ?>
            </tbody>
        </table>
    </div>
    <p><a href="/" style="color:#0066cc; text-decoration:none">&larr; <?= $i18n->t('back_dashboard') ?></a></p>
</main>
</body>
</html>
    <?php
    exit(0);
}
// Admin-only; requires CSRF.
if ($path === '/admin/import' && $method === 'POST') {
    $session->requireAuth('/login.php');
    $session->requireRole('admin', '/login.php');
    $csrf->enforce();

    header('Content-Type: application/json; charset=utf-8');

    $body    = (string)file_get_contents('php://input');
    $payload = json_decode($body, true, 10);

    if (!is_array($payload) || !isset($payload['mailbox']) || !is_string($payload['mailbox'])) {
        http_response_code(400);
        echo json_encode(['error' => 'Request body must be JSON with a "mailbox" string field.']);
        exit(0);
    }

    $mailbox    = trim($payload['mailbox']);
    // report_name is the Maildir basename used in output filenames (default: 'maildir').
    // store-mailbox always uses 'maildir' as the rsync target directory.
    $reportName = isset($payload['report_name']) && is_string($payload['report_name'])
        ? trim($payload['report_name'])
        : 'maildir';
    $dataDir    = rtrim((string)($config['data_dir'] ?? ''), '/');

    try {
        $pdo      = $makePdo();
        $importer = new \MailReview\Import\Importer($pdo, $dataDir);
        $result   = $importer->import($mailbox, $reportName);

        http_response_code(200);
        echo json_encode([
            'status'               => 'imported',
            'report_id'            => $result['report_id'],
            'email_count'          => $result['email_count'],
            'decisions_seed_path'  => $result['decisions_seed_path'],
        ]);
    } catch (\MailReview\Import\ImportException $e) {
        http_response_code($e->getHttpStatus());
        echo json_encode(['error' => $e->getMessage()]);
        error_log('Import error: ' . $e->getMessage());
    } catch (\PDOException $e) {
        http_response_code(500);
        echo json_encode(['error' => 'Database connection failed.']);
        error_log('Import DB error: ' . $e->getMessage());
    }
    exit(0);
}

// ── GET /admin/reports — list imported reports ─────────────────────────────
// Admin-only; no CSRF needed on GET.
if ($path === '/admin/reports' && $method === 'GET') {
    $session->requireAuth('/login.php');
    $session->requireRole('admin', '/login.php');

    header('Content-Type: application/json; charset=utf-8');
    try {
        $pdo   = $makePdo();
        $rows  = $pdo->query(
            'SELECT report_id, mailbox, generated_at, imported_at, decisions_seed_path
             FROM reports ORDER BY imported_at DESC LIMIT 100'
        )->fetchAll();
        http_response_code(200);
        echo json_encode(['reports' => $rows]);
    } catch (\PDOException $e) {
        http_response_code(500);
        echo json_encode(['error' => 'Database connection failed.']);
        error_log('Reports list DB error: ' . $e->getMessage());
    }
    exit(0);
}

// ── POST /review/update — update a decision (coworker only) ───────────────
// Coworker role; CSRF required; records updated_by from session display_name.
if ($path === '/review/update' && $method === 'POST') {
    $session->requireAuth('/login.php');
    $session->requireRole('coworker', '/login.php');
    $csrf->enforce();

    header('Content-Type: application/json; charset=utf-8');

    $body    = (string)file_get_contents('php://input');
    $payload = json_decode($body, true, 10);

    if (!is_array($payload)) {
        http_response_code(400);
        echo json_encode(['error' => 'Request body must be a JSON object.']);
        exit(0);
    }

    $reportId  = trim((string)($payload['report_id']  ?? ''));
    $stableId  = trim((string)($payload['stable_id']  ?? ''));
    $decision  = trim((string)($payload['decision']   ?? ''));
    $note      = trim((string)($payload['note']       ?? ''));

    $allowedDecisions = ['keep', 'delete', 'unsure', ''];
    if ($reportId === '' || $stableId === '' || !in_array($decision, $allowedDecisions, true)) {
        http_response_code(400);
        echo json_encode(['error' => 'Missing or invalid fields: report_id, stable_id, decision required.']);
        exit(0);
    }

    // Display name is mandatory for coworker (enforced at login).
    $updatedBy = $session->getDisplayName();
    if ($updatedBy === '') {
        http_response_code(400);
        echo json_encode(['error' => 'Session is missing display name. Please log in again.']);
        exit(0);
    }

    try {
        $pdo  = $makePdo();

        // Verify the (report_id, stable_id) exists before updating
        $check = $pdo->prepare(
            'SELECT 1 FROM emails WHERE report_id = :report_id AND stable_id = :stable_id LIMIT 1'
        );
        $check->execute([':report_id' => $reportId, ':stable_id' => $stableId]);
        if ($check->fetchColumn() === false) {
            http_response_code(404);
            echo json_encode(['error' => 'Email not found for this report.']);
            exit(0);
        }

        // Upsert decision
        $stmt = $pdo->prepare(<<<SQL
            INSERT INTO decisions
                (report_id, stable_id, decision, note, updated_at, updated_by)
            VALUES
                (:report_id, :stable_id, :decision, :note, NOW(), :updated_by)
            ON DUPLICATE KEY UPDATE
                decision   = VALUES(decision),
                note       = VALUES(note),
                updated_at = NOW(),
                updated_by = VALUES(updated_by)
        SQL);
        $stmt->execute([
            ':report_id'  => $reportId,
            ':stable_id'  => $stableId,
            ':decision'   => $decision,
            ':note'       => $note,
            ':updated_by' => $updatedBy,
        ]);

        http_response_code(200);
        echo json_encode([
            'status'     => 'updated',
            'report_id'  => $reportId,
            'stable_id'  => $stableId,
            'decision'   => $decision,
            'updated_by' => $updatedBy,
        ]);
    } catch (\PDOException $e) {
        http_response_code(500);
        echo json_encode(['error' => 'Database error during update.']);
        error_log('Review update DB error: ' . $e->getMessage());
    }
    exit(0);
}

// ── GET /download/report/{report_id}/{file} — Serve a report artifact ──────
//
// Authenticated (any role). Serves PDF, manifest JSON, or seed CSV for a report.
// The file param is validated (alphanumeric + dot/hyphen/underscore + .pdf/.json/.csv);
// the actual path is resolved from the MySQL reports table — no raw path in URL.
if (preg_match('#^/download/report/([^/]+)/([^/]+)$#', $path, $dm) && $method === 'GET') {
    $session->requireAuth('/login.php');

    $dlReportId = urldecode($dm[1]);
    $dlFile     = urldecode($dm[2]);
    $dataDir    = rtrim((string)($config['data_dir'] ?? ''), '/');

    try {
        $pdo = $makePdo();
        $dlService = new \MailReview\Download\DownloadService($pdo, $dataDir);
        $fileInfo  = $dlService->resolveReportArtifact($dlReportId, $dlFile);
    } catch (\MailReview\Download\DownloadException $e) {
        http_response_code($e->getHttpStatus());
        header('Content-Type: text/plain; charset=utf-8');
        echo $e->getMessage();
        error_log('Download report error: ' . $e->getMessage());
        exit(0);
    } catch (\PDOException $e) {
        http_response_code(500);
        header('Content-Type: text/plain; charset=utf-8');
        echo 'Database error.';
        error_log('Download report DB error: ' . $e->getMessage());
        exit(0);
    }

    // Stream the file safely.
    $safeFilename = rawurlencode($fileInfo['filename']);
    header('Content-Type: ' . $fileInfo['mime']);
    header('Content-Disposition: attachment; filename="' . addslashes($fileInfo['filename']) . '"; filename*=UTF-8\'\''. $safeFilename);
    header('Content-Length: ' . $fileInfo['size']);
    header('X-Content-Type-Options: nosniff');
    header('Cache-Control: private, no-cache');
    readfile($fileInfo['path']);
    exit(0);
}

// ── GET /download/attachment/{report_id}/{stable_id}/{sha256} — Serve attachment
//
// Authenticated (any role).
// Lookup is keyed by (report_id, stable_id, sha256); mismatches return 404.
// Path is resolved from the per-mailbox SQLite index — never from user input.
if (preg_match('#^/download/attachment/([^/]+)/([^/]+)/([0-9a-fA-F]{64})$#', $path, $am) && $method === 'GET') {
    $session->requireAuth('/login.php');

    $dlReportId = urldecode($am[1]);
    $dlStableId = urldecode($am[2]);
    $dlSha256   = strtolower($am[3]);   // normalise to lowercase for the index lookup
    $dataDir    = rtrim((string)($config['data_dir'] ?? ''), '/');

    try {
        $pdo = $makePdo();
        $dlService = new \MailReview\Download\DownloadService($pdo, $dataDir);
        $fileInfo  = $dlService->resolveAttachment($dlReportId, $dlStableId, $dlSha256);
    } catch (\MailReview\Download\DownloadException $e) {
        http_response_code($e->getHttpStatus());
        header('Content-Type: text/plain; charset=utf-8');
        echo $e->getMessage();
        error_log('Download attachment error: ' . $e->getMessage());
        exit(0);
    } catch (\PDOException $e) {
        http_response_code(500);
        header('Content-Type: text/plain; charset=utf-8');
        echo 'Database error.';
        error_log('Download attachment DB error: ' . $e->getMessage());
        exit(0);
    }

    // Stream the file safely.
    $safeFilename = rawurlencode($fileInfo['filename']);
    header('Content-Type: ' . $fileInfo['mime']);
    header('Content-Disposition: attachment; filename="' . addslashes($fileInfo['filename']) . '"; filename*=UTF-8\'\''. $safeFilename);
    header('Content-Length: ' . $fileInfo['size']);
    header('X-Content-Type-Options: nosniff');
    header('Cache-Control: private, no-cache');
    readfile($fileInfo['path']);
    exit(0);
}

// ── POST /review/bulk-apply — apply a decision to all rows matching a filter ─
//
// Coworker role; CSRF required.
// Required POST params: report_id, decision, confirm ('1' to execute).
// Optional filters:  filter_dup_only (1|0), filter_decision (keep|delete|unsure)
// Without confirm=1: returns {"dry_run":true,"count":N} HTTP 200.
// With confirm=1:    updates in chunks of 1000, returns {"updated":N}.
if ($path === '/review/bulk-apply' && $method === 'POST') {
    $session->requireAuth('/login.php');
    $session->requireRole('coworker', '/login.php');
    $csrf->enforce();

    header('Content-Type: application/json; charset=utf-8');

    $reportId      = trim((string)($_POST['report_id']       ?? ''));
    $decision      = trim((string)($_POST['decision']        ?? ''));
    $confirm       = trim((string)($_POST['confirm']         ?? ''));
    $filterDupOnly = trim((string)($_POST['filter_dup_only'] ?? ''));
    $filterDecision = trim((string)($_POST['filter_decision'] ?? ''));

    $allowedDecisions = ['keep', 'delete', 'unsure'];
    if ($reportId === '' || !in_array($decision, $allowedDecisions, true)) {
        http_response_code(400);
        echo json_encode(['error' => 'Missing or invalid fields: report_id and decision (keep|delete|unsure) required.']);
        exit(0);
    }

    $updatedBy = $session->getDisplayName();
    if ($updatedBy === '') {
        http_response_code(400);
        echo json_encode(['error' => 'Session is missing display name. Please log in again.']);
        exit(0);
    }

    try {
        $pdo = $makePdo();

        // Build the WHERE clause for the current filter.
        // We JOIN emails to access dup_group_id / dup_rank for the dup-only filter.
        $conditions = ['d.report_id = :report_id'];
        $params     = [':report_id' => $reportId];

        if ($filterDupOnly === '1') {
            // Only rows whose email has a non-empty dup_group_id
            $conditions[] = 'e.dup_group_id <> \'\'';
        }

        if (in_array($filterDecision, ['keep', 'delete', 'unsure', ''], true) && $filterDecision !== '') {
            $conditions[]         = 'd.decision = :filter_decision';
            $params[':filter_decision'] = $filterDecision;
        }

        $whereClause = implode(' AND ', $conditions);

        // Count matching rows (used for dry-run and chunked update).
        $countSql = <<<SQL
            SELECT d.stable_id
            FROM decisions d
            JOIN emails e ON e.report_id = d.report_id AND e.stable_id = d.stable_id
            WHERE $whereClause
        SQL;
        $countStmt = $pdo->prepare($countSql);
        $countStmt->execute($params);
        $stableIds = $countStmt->fetchAll(\PDO::FETCH_COLUMN);
        $total = count($stableIds);

        if ($confirm !== '1') {
            // Dry-run: tell the caller how many rows would be affected.
            http_response_code(200);
            echo json_encode(['dry_run' => true, 'count' => $total]);
            exit(0);
        }

        // Execute: update in chunks of 1000 to avoid one huge transaction.
        $chunkSize = 1000;
        $updated   = 0;
        foreach (array_chunk($stableIds, $chunkSize) as $chunk) {
            $placeholders = implode(',', array_fill(0, count($chunk), '?'));
            $chunkParams  = array_merge([$decision, $updatedBy, $reportId], $chunk);
            $updateSql    = <<<SQL
                UPDATE decisions
                SET decision = ?, updated_at = NOW(), updated_by = ?
                WHERE report_id = ? AND stable_id IN ($placeholders)
            SQL;
            $upStmt = $pdo->prepare($updateSql);
            $upStmt->execute($chunkParams);
            $updated += $upStmt->rowCount();
        }

        http_response_code(200);
        echo json_encode(['updated' => $updated]);
    } catch (\PDOException $e) {
        http_response_code(500);
        echo json_encode(['error' => 'Database error during bulk apply.']);
        error_log('Bulk apply DB error: ' . $e->getMessage());
    }
    exit(0);
}

// ── POST /review/dup-group-action — keep rank-0, delete rest in a dup group ──
//
// Coworker role; CSRF required.
// Required POST params: report_id, dup_group_id.
// Returns {"kept":1,"deleted":N}.
if ($path === '/review/dup-group-action' && $method === 'POST') {
    $session->requireAuth('/login.php');
    $session->requireRole('coworker', '/login.php');
    $csrf->enforce();

    header('Content-Type: application/json; charset=utf-8');

    $reportId   = trim((string)($_POST['report_id']   ?? ''));
    $dupGroupId = trim((string)($_POST['dup_group_id'] ?? ''));

    if ($reportId === '' || $dupGroupId === '') {
        http_response_code(400);
        echo json_encode(['error' => 'Missing required fields: report_id, dup_group_id.']);
        exit(0);
    }

    $updatedBy = $session->getDisplayName();
    if ($updatedBy === '') {
        http_response_code(400);
        echo json_encode(['error' => 'Session is missing display name. Please log in again.']);
        exit(0);
    }

    try {
        $pdo = $makePdo();

        // Set decision='keep' for the canonical row (dup_rank = 0).
        $keepStmt = $pdo->prepare(<<<SQL
            UPDATE decisions d
            JOIN emails e ON e.report_id = d.report_id AND e.stable_id = d.stable_id
            SET d.decision = 'keep', d.updated_at = NOW(), d.updated_by = :updated_by
            WHERE d.report_id = :report_id
              AND e.dup_group_id = :dup_group_id
              AND e.dup_rank = 0
        SQL);
        $keepStmt->execute([
            ':updated_by'  => $updatedBy,
            ':report_id'   => $reportId,
            ':dup_group_id' => $dupGroupId,
        ]);
        $kept = $keepStmt->rowCount();

        // Set decision='delete' for non-canonical rows (dup_rank > 0).
        $delStmt = $pdo->prepare(<<<SQL
            UPDATE decisions d
            JOIN emails e ON e.report_id = d.report_id AND e.stable_id = d.stable_id
            SET d.decision = 'delete', d.updated_at = NOW(), d.updated_by = :updated_by
            WHERE d.report_id = :report_id
              AND e.dup_group_id = :dup_group_id
              AND e.dup_rank > 0
        SQL);
        $delStmt->execute([
            ':updated_by'  => $updatedBy,
            ':report_id'   => $reportId,
            ':dup_group_id' => $dupGroupId,
        ]);
        $deleted = $delStmt->rowCount();

        http_response_code(200);
        echo json_encode(['kept' => $kept, 'deleted' => $deleted]);
    } catch (\PDOException $e) {
        http_response_code(500);
        echo json_encode(['error' => 'Database error during dup group action.']);
        error_log('Dup group action DB error: ' . $e->getMessage());
    }
    exit(0);
}

// ── GET /admin/export/decisions — stream reviewed decisions CSV (admin only) ─
//
// Columns (stable_id order): stable_id, date, sender, subject, size_bytes,
//   has_attachments, attachment_count, attachment_total_bytes, attachment_extensions,
//   dup_group_id, dup_rank, total_size_bytes, decision, note
// NOTE: emails table stores total_size_bytes but not the per-attachment breakdown.
// has_attachments/attachment_count/attachment_total_bytes/attachment_extensions are
// stored in the seed CSV only; they are not in the DB schema. We export '' for those
// columns so the schema is preserved (the columns are present, values are blank).
if ($path === '/admin/export/decisions' && $method === 'GET') {
    $session->requireAuth('/login.php');
    $session->requireRole('admin', '/login.php');

    $reportId = trim($_GET['report_id'] ?? '');
    if ($reportId === '') {
        http_response_code(400);
        header('Content-Type: text/plain; charset=utf-8');
        echo 'Missing required parameter: report_id';
        exit(0);
    }

    try {
        $pdo = $makePdo();

        // Validate report exists
        $chk = $pdo->prepare('SELECT 1 FROM reports WHERE report_id = :rid LIMIT 1');
        $chk->execute([':rid' => $reportId]);
        if ($chk->fetchColumn() === false) {
            http_response_code(404);
            header('Content-Type: text/plain; charset=utf-8');
            echo 'Report not found.';
            exit(0);
        }

        header('Content-Type: text/csv; charset=utf-8');
        header('Content-Disposition: attachment; filename="decisions.reviewed.csv"');
        header('Cache-Control: private, no-cache');

        // Disable output buffering so rows stream immediately
        if (ob_get_level() > 0) {
            ob_end_clean();
        }

        $out = fopen('php://output', 'w');
        // BOM-free CSV; Excel-safe via proper fputcsv quoting
        fputcsv($out, [
            'stable_id', 'date', 'sender', 'subject', 'size_bytes',
            'has_attachments', 'attachment_count', 'attachment_total_bytes',
            'attachment_extensions', 'dup_group_id', 'dup_rank', 'total_size_bytes',
            'decision', 'note',
        ]);

        $stmt = $pdo->prepare(<<<SQL
            SELECT
                e.stable_id,
                e.date,
                e.sender,
                e.subject,
                e.total_size_bytes   AS size_bytes,
                e.dup_group_id,
                e.dup_rank,
                e.total_size_bytes,
                COALESCE(d.decision, '') AS decision,
                COALESCE(d.note,     '') AS note
            FROM emails e
            LEFT JOIN decisions d
                ON  d.report_id = e.report_id
                AND d.stable_id = e.stable_id
            WHERE e.report_id = :rid
            ORDER BY e.date ASC, e.stable_id ASC
        SQL);
        $stmt->execute([':rid' => $reportId]);

        while ($row = $stmt->fetch()) {
            fputcsv($out, [
                (string)($row['stable_id']      ?? ''),
                (string)($row['date']           ?? ''),
                (string)($row['sender']         ?? ''),
                (string)($row['subject']        ?? ''),
                (string)($row['size_bytes']     ?? ''),  // size_bytes
                '',                                      // has_attachments (not in DB)
                '',                                      // attachment_count (not in DB)
                '',                                      // attachment_total_bytes (not in DB)
                '',                                      // attachment_extensions (not in DB)
                (string)($row['dup_group_id']   ?? ''),
                (string)($row['dup_rank']       ?? ''),
                (string)($row['total_size_bytes'] ?? ''),
                (string)($row['decision']       ?? ''),
                (string)($row['note']           ?? ''),
            ]);
        }
        fclose($out);
    } catch (\PDOException $e) {
        // Headers may already be sent; log and exit
        error_log('Export decisions DB error: ' . $e->getMessage());
    }
    exit(0);
}

// ── GET /admin/export/audit — stream audit CSV with review metadata (admin only)
//
// Same as /admin/export/decisions but appends updated_by, updated_at, note columns.
if ($path === '/admin/export/audit' && $method === 'GET') {
    $session->requireAuth('/login.php');
    $session->requireRole('admin', '/login.php');

    $reportId = trim($_GET['report_id'] ?? '');
    if ($reportId === '') {
        http_response_code(400);
        header('Content-Type: text/plain; charset=utf-8');
        echo 'Missing required parameter: report_id';
        exit(0);
    }

    try {
        $pdo = $makePdo();

        // Validate report exists
        $chk = $pdo->prepare('SELECT 1 FROM reports WHERE report_id = :rid LIMIT 1');
        $chk->execute([':rid' => $reportId]);
        if ($chk->fetchColumn() === false) {
            http_response_code(404);
            header('Content-Type: text/plain; charset=utf-8');
            echo 'Report not found.';
            exit(0);
        }

        header('Content-Type: text/csv; charset=utf-8');
        header('Content-Disposition: attachment; filename="decisions.audit.csv"');
        header('Cache-Control: private, no-cache');

        if (ob_get_level() > 0) {
            ob_end_clean();
        }

        $out = fopen('php://output', 'w');
        fputcsv($out, [
            'stable_id', 'date', 'sender', 'subject', 'size_bytes',
            'has_attachments', 'attachment_count', 'attachment_total_bytes',
            'attachment_extensions', 'dup_group_id', 'dup_rank', 'total_size_bytes',
            'decision', 'note', 'updated_by', 'updated_at',
        ]);

        $stmt = $pdo->prepare(<<<SQL
            SELECT
                e.stable_id,
                e.date,
                e.sender,
                e.subject,
                e.total_size_bytes   AS size_bytes,
                e.dup_group_id,
                e.dup_rank,
                e.total_size_bytes,
                COALESCE(d.decision,   '') AS decision,
                COALESCE(d.note,       '') AS note,
                COALESCE(d.updated_by, '') AS updated_by,
                COALESCE(d.updated_at, '') AS updated_at
            FROM emails e
            LEFT JOIN decisions d
                ON  d.report_id = e.report_id
                AND d.stable_id = e.stable_id
            WHERE e.report_id = :rid
            ORDER BY e.date ASC, e.stable_id ASC
        SQL);
        $stmt->execute([':rid' => $reportId]);

        while ($row = $stmt->fetch()) {
            fputcsv($out, [
                (string)($row['stable_id']      ?? ''),
                (string)($row['date']           ?? ''),
                (string)($row['sender']         ?? ''),
                (string)($row['subject']        ?? ''),
                (string)($row['size_bytes']     ?? ''),
                '',                                       // has_attachments (not in DB)
                '',                                       // attachment_count (not in DB)
                '',                                       // attachment_total_bytes (not in DB)
                '',                                       // attachment_extensions (not in DB)
                (string)($row['dup_group_id']   ?? ''),
                (string)($row['dup_rank']       ?? ''),
                (string)($row['total_size_bytes'] ?? ''),
                (string)($row['decision']       ?? ''),
                (string)($row['note']           ?? ''),
                (string)($row['updated_by']     ?? ''),
                (string)($row['updated_at']     ?? ''),
            ]);
        }
        fclose($out);
    } catch (\PDOException $e) {
        error_log('Export audit DB error: ' . $e->getMessage());
    }
    exit(0);
}

// 404 fallback
http_response_code(404);
header('Content-Type: text/plain; charset=utf-8');
echo 'Not found.';
exit(0);
