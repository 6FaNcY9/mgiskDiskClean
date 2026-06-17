# Production UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Mrija Archive web app as a three-panel email-client UI with space-grey theming, inline attachment previews, VirusTotal scanning, and production Docker hardening — ready for handoff to a coworker on Windows + Docker.

**Architecture:** Single-file PHP approach: `index.php` is fully rewritten as a three-panel layout using CSS custom properties for theming. A new `VtService` class handles VirusTotal API calls and caches results in MariaDB. `download.php` gains a VT gate and an `?inline=1` mode for in-app previews.

**Tech Stack:** PHP 8.3 (no framework, no Composer), MariaDB via PDO, CSS custom properties, vanilla JS, Docker Compose, VirusTotal API v2.

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `web/migrations/002_vt_cache.sql` | Create | `vt_cache` table schema |
| `web/src/VirusTotal/VtService.php` | Create | VT API calls + cache read/write |
| `web/public/download.php` | Modify | Add `?inline=1` mode + VT gate |
| `docker-compose.yml` | Modify | Localhost port binding + VT_API_KEY env |
| `web/config/local.php.docker` | Modify | Wire `vt_api_key` from env |
| `.env.example` | Modify | Document `VT_API_KEY` |
| `web/public/index.php` | Rewrite | Three-panel UI, theme system, VT badges, inline preview |

---

## Task 1: DB Migration — vt_cache Table

**Files:**
- Create: `web/migrations/002_vt_cache.sql`

- [ ] **Step 1.1: Create the migration file**

```sql
-- web/migrations/002_vt_cache.sql
-- VirusTotal scan result cache, keyed by file SHA-256.
-- Applied by: docker compose run --rm app php web/src/cli/migrate.php

CREATE TABLE IF NOT EXISTS vt_cache (
    sha256      CHAR(64)     NOT NULL,
    status      ENUM('pending','clean','infected','error') NOT NULL DEFAULT 'pending',
    scan_id     VARCHAR(64)  NOT NULL DEFAULT '',
    positives   TINYINT      NOT NULL DEFAULT 0,
    total       SMALLINT     NOT NULL DEFAULT 0,
    scanned_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                             ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 1.2: Start the Docker stack and run the migration**

```bash
docker compose up -d --build
docker compose run --rm app php web/src/cli/migrate.php
```

Expected output:
```
  [skip]  001_archive_schema.sql
  [apply] 002_vt_cache.sql
==> Migrations complete. Applied: 1
```

- [ ] **Step 1.3: Verify the table exists**

```bash
docker compose run --rm app php -r "
\$pdo = new PDO('mysql:host=db;dbname=mailreview;charset=utf8mb4','mailreview',getenv('DB_PASS'));
\$cols = \$pdo->query('DESCRIBE vt_cache')->fetchAll(PDO::FETCH_COLUMN);
echo implode(', ', \$cols) . PHP_EOL;
"
```

Expected output: `sha256, status, scan_id, positives, total, scanned_at`

- [ ] **Step 1.4: Commit**

```bash
git add web/migrations/002_vt_cache.sql
git commit -m "feat(db): add vt_cache migration for VirusTotal scan results"
```

---

## Task 2: VtService Class

**Files:**
- Create: `web/src/VirusTotal/VtService.php`

- [ ] **Step 2.1: Create the directory and class**

Create `web/src/VirusTotal/VtService.php`:

```php
<?php
/**
 * VtService — VirusTotal v2 API wrapper with MariaDB result cache.
 *
 * Usage:
 *   $vt = new \MailReview\VirusTotal\VtService($pdo, $config['vt_api_key'], $dataDir);
 *   $r  = $vt->check($sha256, $filePath);
 *   // $r = ['status' => 'clean'|'infected'|'pending'|'error'|'disabled', 'positives' => int]
 */
declare(strict_types=1);

namespace MailReview\VirusTotal;

class VtService
{
    private \PDO    $pdo;
    private string  $apiKey;
    private string  $dataDir;

    public function __construct(\PDO $pdo, string $apiKey, string $dataDir)
    {
        $this->pdo     = $pdo;
        $this->apiKey  = $apiKey;
        $this->dataDir = rtrim($dataDir, '/');
    }

    /**
     * Check a file against VirusTotal.
     * Returns ['status' => string, 'positives' => int].
     */
    public function check(string $sha256, string $filePath): array
    {
        if ($this->apiKey === '') {
            return ['status' => 'disabled', 'positives' => 0];
        }

        $cached = $this->getCached($sha256);

        // Non-pending cache hit → return immediately
        if ($cached !== null && $cached['status'] !== 'pending') {
            return ['status' => $cached['status'], 'positives' => (int)$cached['positives']];
        }

        // Pending with a scan_id → poll VT for the result
        if ($cached !== null && $cached['scan_id'] !== '') {
            return $this->pollScan($sha256, $cached['scan_id']);
        }

        // Cache miss → check VT by hash first (avoids uploading known files)
        $report = $this->fileReport($sha256);
        if ($report === null) {
            $this->upsert($sha256, 'error', '', 0);
            return ['status' => 'error', 'positives' => 0];
        }
        if ((int)($report['response_code'] ?? 0) === 1) {
            return $this->storeReport($sha256, $report);
        }

        // Hash unknown to VT → upload the file
        if (!is_file($filePath)) {
            $this->upsert($sha256, 'error', '', 0);
            return ['status' => 'error', 'positives' => 0];
        }
        $scan = $this->fileScan($filePath);
        if ($scan === null || empty($scan['scan_id'])) {
            $this->upsert($sha256, 'error', '', 0);
            return ['status' => 'error', 'positives' => 0];
        }
        $this->upsert($sha256, 'pending', (string)$scan['scan_id'], 0);
        return ['status' => 'pending', 'positives' => 0];
    }

    // ── Private helpers ────────────────────────────────────────────────────

    private function getCached(string $sha256): ?array
    {
        $st = $this->pdo->prepare(
            'SELECT status, scan_id, positives FROM vt_cache WHERE sha256 = ?'
        );
        $st->execute([$sha256]);
        return $st->fetch(\PDO::FETCH_ASSOC) ?: null;
    }

    private function pollScan(string $sha256, string $scanId): array
    {
        $report = $this->fileReport($sha256);
        if ($report === null || (int)($report['response_code'] ?? 0) !== 1) {
            return ['status' => 'pending', 'positives' => 0];
        }
        return $this->storeReport($sha256, $report);
    }

    private function storeReport(string $sha256, array $report): array
    {
        $positives = (int)($report['positives'] ?? 0);
        $status    = $positives === 0 ? 'clean' : 'infected';
        $scanId    = (string)($report['scan_id'] ?? '');
        $this->upsert($sha256, $status, $scanId, $positives);
        return ['status' => $status, 'positives' => $positives];
    }

    private function upsert(string $sha256, string $status, string $scanId, int $positives): void
    {
        $this->pdo->prepare(
            'INSERT INTO vt_cache (sha256, status, scan_id, positives, scanned_at)
             VALUES (?, ?, ?, ?, NOW())
             ON DUPLICATE KEY UPDATE
               status     = VALUES(status),
               scan_id    = VALUES(scan_id),
               positives  = VALUES(positives),
               scanned_at = NOW()'
        )->execute([$sha256, $status, $scanId, $positives]);
    }

    private function fileReport(string $sha256): ?array
    {
        $url = 'https://www.virustotal.com/vtapi/v2/file/report?'
             . http_build_query(['apikey' => $this->apiKey, 'resource' => $sha256]);
        $ctx = stream_context_create(['http' => ['timeout' => 15]]);
        $raw = @file_get_contents($url, false, $ctx);
        if ($raw === false) return null;
        $data = json_decode($raw, true);
        return is_array($data) ? $data : null;
    }

    private function fileScan(string $filePath): ?array
    {
        $boundary = '----VTBound' . bin2hex(random_bytes(8));
        $content  = file_get_contents($filePath);
        if ($content === false) return null;

        $body = "--{$boundary}\r\n"
              . "Content-Disposition: form-data; name=\"apikey\"\r\n\r\n"
              . $this->apiKey . "\r\n"
              . "--{$boundary}\r\n"
              . 'Content-Disposition: form-data; name="file"; filename="' . basename($filePath) . '"' . "\r\n"
              . "Content-Type: application/octet-stream\r\n\r\n"
              . $content . "\r\n"
              . "--{$boundary}--";

        $ctx = stream_context_create([
            'http' => [
                'method'  => 'POST',
                'header'  => "Content-Type: multipart/form-data; boundary={$boundary}\r\n",
                'content' => $body,
                'timeout' => 30,
            ],
        ]);
        $raw = @file_get_contents('https://www.virustotal.com/vtapi/v2/file/scan', false, $ctx);
        if ($raw === false) return null;
        $data = json_decode($raw, true);
        return is_array($data) ? $data : null;
    }
}
```

- [ ] **Step 2.2: Smoke-test VtService inside the container**

```bash
docker compose run --rm app php -r "
require_once 'web/src/VirusTotal/VtService.php';
\$pdo = new PDO('mysql:host=db;dbname=mailreview;charset=utf8mb4','mailreview',getenv('DB_PASS'));
// EICAR test hash — always returns clean/infected depending on VT.
// With empty key, should return disabled.
\$vt = new \MailReview\VirusTotal\VtService(\$pdo, '', '/app/data');
\$r  = \$vt->check('abc123', '/nonexistent');
echo \$r['status'] . PHP_EOL; // expects: disabled
"
```

Expected output: `disabled`

- [ ] **Step 2.3: Commit**

```bash
git add web/src/VirusTotal/VtService.php
git commit -m "feat(vt): add VtService for VirusTotal API + MariaDB cache"
```

---

## Task 3: Docker Hardening

**Files:**
- Modify: `docker-compose.yml`
- Modify: `web/config/local.php.docker`
- Modify: `.env.example`

- [ ] **Step 3.1: Lock web port to localhost in `docker-compose.yml`**

Find this block in the `web` service:
```yaml
    ports:
      - "${MRIJA_WEB_PORT:-8080}:8080"
```
Replace with:
```yaml
    ports:
      - "127.0.0.1:${MRIJA_WEB_PORT:-8080}:8080"
```

Also add `VT_API_KEY` to the `web` service `environment` block (add after `ADMIN_PASSWORD_HASH`):
```yaml
      VT_API_KEY: ${VT_API_KEY:-}
```

- [ ] **Step 3.2: Wire VT API key in `web/config/local.php.docker`**

Add one line after `'admin_password_hash'`:
```php
    'vt_api_key'          => $_ENV['VT_API_KEY'] ?? getenv('VT_API_KEY') ?: '',
```

- [ ] **Step 3.3: Document `VT_API_KEY` in `.env.example`**

Add after `ADMIN_PASSWORD_HASH=`:
```
VT_API_KEY=              # Free key from virustotal.com — leave empty to disable scanning
```

- [ ] **Step 3.4: Rebuild and verify localhost binding**

```bash
docker compose down && docker compose up -d --build
```

From a second shell, verify the port is NOT reachable externally (replace `<your-local-ip>` with actual IP like 192.168.x.x):
```bash
curl -s --connect-timeout 2 http://<your-local-ip>:8080/ || echo "BLOCKED (correct)"
curl -s --connect-timeout 2 http://127.0.0.1:8080/ | grep -c "Mrija" && echo "LOCALHOST OK"
```

Expected: `BLOCKED (correct)` then `1` + `LOCALHOST OK`

- [ ] **Step 3.5: Commit**

```bash
git add docker-compose.yml web/config/local.php.docker .env.example
git commit -m "feat(docker): bind web port to localhost only, wire VT_API_KEY"
```

---

## Task 4: download.php — Inline Mode + VT Gate

**Files:**
- Modify: `web/public/download.php`

- [ ] **Step 4.1: Replace the stream section with inline + VT-gated flow**

The current file ends with this block (after the path traversal guard, around line 78):

```php
$originalName = $att['original_filename'] ?: $filename;
$mime         = $att['mime'] ?: 'application/octet-stream';

// Only allow safe inline display for images and PDFs; force download otherwise
$inlineMimes = ['image/jpeg','image/png','image/gif','image/webp','image/svg+xml','application/pdf'];
$disposition = in_array($mime, $inlineMimes, true) ? 'inline' : 'attachment';

header('Content-Type: ' . $mime);
header('Content-Disposition: ' . $disposition . '; filename="' . addslashes($originalName) . '"');
header('Content-Length: ' . filesize($realPath));
header('Cache-Control: private, max-age=3600');
header('X-Content-Type-Options: nosniff');

readfile($realPath);
exit;
```

Replace that entire block with:

```php
$originalName = $att['original_filename'] ?: $filename;
$mime         = $att['mime'] ?: 'application/octet-stream';

// ── Inline preview mode (in-app iframe/img) — skip VT, serve inline ──────
$isInline = ($_GET['inline'] ?? '') === '1';
if ($isInline) {
    header('Content-Type: ' . $mime);
    header('Content-Disposition: inline; filename="' . addslashes($originalName) . '"');
    header('Content-Length: ' . filesize($realPath));
    header('Cache-Control: private, max-age=3600');
    header('X-Content-Type-Options: nosniff');
    readfile($realPath);
    exit;
}

// ── VirusTotal gate for explicit downloads ────────────────────────────────
$vtApiKey = $config['vt_api_key'] ?? '';
if ($vtApiKey !== '') {
    spl_autoload_register(function (string $class): void {
        $map = ['MailReview\\VirusTotal\\VtService' => __DIR__ . '/../src/VirusTotal/VtService.php'];
        if (isset($map[$class])) require_once $map[$class];
    });
    $vt     = new \MailReview\VirusTotal\VtService($pdo, $vtApiKey, $dataDir);
    $vtRes  = $vt->check($sha256, $realPath);

    if ($vtRes['status'] === 'infected') {
        http_response_code(403);
        echo '<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<title>Datei blockiert</title>
<style>body{background:#0d0d0d;color:#e8e8e8;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{text-align:center;max-width:400px}.icon{font-size:3rem;margin-bottom:1rem}
h1{color:#c0606a;font-size:1.1rem;margin-bottom:.5rem}
p{color:#888;font-size:.85rem;line-height:1.6}</style></head>
<body><div class="box"><div class="icon">⚠️</div>
<h1>Datei blockiert</h1>
<p>VirusTotal hat in dieser Datei Schadsoftware erkannt (' . (int)$vtRes['positives'] . ' Treffer).<br>
Der Download wurde gesperrt.</p></div></body></html>';
        exit;
    }

    if ($vtRes['status'] === 'pending') {
        echo '<!DOCTYPE html><html lang="de"><head><meta charset="UTF-8">
<meta http-equiv="refresh" content="4">
<title>Wird geprüft…</title>
<style>body{background:#0d0d0d;color:#e8e8e8;font-family:system-ui,sans-serif;
display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
.box{text-align:center;max-width:400px}.icon{font-size:2.5rem;margin-bottom:1rem}
h1{color:#888;font-size:1rem;margin-bottom:.5rem}
p{color:#555;font-size:.8rem}</style></head>
<body><div class="box"><div class="icon">⏳</div>
<h1>Wird von VirusTotal geprüft…</h1>
<p>Diese Seite lädt automatisch neu. Bitte warten.</p></div></body></html>';
        exit;
    }
    // status: clean, error, or disabled → fall through and serve
}

// ── Serve file ────────────────────────────────────────────────────────────
header('Content-Type: ' . $mime);
header('Content-Disposition: attachment; filename="' . addslashes($originalName) . '"');
header('Content-Length: ' . filesize($realPath));
header('Cache-Control: private, max-age=3600');
header('X-Content-Type-Options: nosniff');
readfile($realPath);
exit;
```

- [ ] **Step 4.2: Test inline mode**

Upload any image into the archive (or use an existing fixture attachment). Open its URL with `?inline=1`:
```bash
docker compose run --rm app php -r "
// Quick sanity: does inline=1 reach the inline branch?
\$_GET = ['mailbox'=>'test','sha256'=>str_repeat('a',64),'inline'=>'1'];
echo 'params ok' . PHP_EOL;
"
```

Then open `http://localhost:8080/download.php?mailbox=<mb>&sha256=<hash>&inline=1` in a browser — the file should render (not download) for images/PDFs.

- [ ] **Step 4.3: Test VT disabled path**

With `VT_API_KEY=` empty in `.env`, files should download immediately without any VT check. Restart the stack (`docker compose up -d`) and confirm a known attachment downloads directly.

- [ ] **Step 4.4: Commit**

```bash
git add web/public/download.php
git commit -m "feat(download): add inline=1 mode and VirusTotal download gate"
```

---

## Task 5: index.php — CSS Foundation & Theme System

**Files:**
- Modify: `web/public/index.php` (replace `<style>` block and add anti-flicker script)

This task replaces only the `<head>` section of `index.php`. The PHP logic and HTML body are unchanged in this step.

- [ ] **Step 5.1: Replace the `<head>` block**

Find the `<!DOCTYPE html>` through the closing `</style>` tag (currently around lines 260–357). Replace with:

```html
<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
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
  --bg-0:#0d0d0d;--bg-1:#111111;--bg-2:#1a1a1a;--bg-3:#222222;
  --border:#2a2a2a;
  --text-1:#e8e8e8;--text-2:#888888;--text-3:#444444;
  --accent:#c0c0c0;--accent-bg:rgba(192,192,192,.08);--accent-border:rgba(192,192,192,.22);
  --danger:#c0606a;--warn:#d4900a;--ok:#6a9f6a;
}
[data-mode="light"]{
  --bg-0:#ebebeb;--bg-1:#f5f5f5;--bg-2:#ffffff;--bg-3:#e0e0e0;
  --border:#d4d4d4;
  --text-1:#1a1a1a;--text-2:#666666;--text-3:#aaaaaa;
  --accent:#555555;--accent-bg:rgba(85,85,85,.07);--accent-border:rgba(85,85,85,.22);
}
[data-accent="blue"]  {--accent:#4a90d9;--accent-bg:rgba(74,144,217,.1);--accent-border:rgba(74,144,217,.3)}
[data-accent="teal"]  {--accent:#2a9d8f;--accent-bg:rgba(42,157,143,.1);--accent-border:rgba(42,157,143,.3)}
[data-accent="amber"] {--accent:#d4900a;--accent-bg:rgba(212,144,10,.1);--accent-border:rgba(212,144,10,.3)}
[data-accent="sage"]  {--accent:#6a9f6a;--accent-bg:rgba(106,159,106,.1);--accent-border:rgba(106,159,106,.3)}
[data-accent="rose"]  {--accent:#c0606a;--accent-bg:rgba(192,96,106,.1);--accent-border:rgba(192,96,106,.3)}

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
</style>
</head>
```

- [ ] **Step 5.2: Verify the app still loads**

```bash
docker compose up -d
```

Open `http://localhost:8080` — app should still display (old layout, new CSS vars applied). If DB has no data, the empty state should show.

- [ ] **Step 5.3: Commit**

```bash
git add web/public/index.php
git commit -m "feat(ui): replace CSS with space-grey theme system + anti-flicker"
```

---

## Task 6: index.php — Three-Panel HTML + Sidebar

**Files:**
- Modify: `web/public/index.php` (replace `<body>` contents)

- [ ] **Step 6.1: Add VT cache query for the selected email's attachments**

Directly after the `$email` detail query block (after `$email['attachments'] = $stmt2->fetchAll()`), add:

```php
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
```

- [ ] **Step 6.2: Replace the entire `<body>` … `</body>` with the three-panel layout**

Replace from `<body>` through the closing `</body>` with:

```html
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
      <button class="theme-btn" id="theme-btn" onclick="toggleThemePop(event)" title="Farbschema">🎨</button>
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

<!-- ── Theme picker popover ────────────────────────────────────────────── -->
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

document.addEventListener('click',()=>document.getElementById('theme-pop').classList.remove('open'));

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
```

- [ ] **Step 6.3: Verify the app renders correctly**

```bash
docker compose up -d
```

Open `http://localhost:8080`:
- Three panels visible: sidebar (mailboxes), middle (search + list), detail (placeholder or email)
- Clicking a mailbox in sidebar filters the list
- Clicking an email row opens it in the detail panel
- 🎨 button in sidebar footer opens the theme popover
- Colour swatches change the accent colour immediately, dark/light toggle works
- Keyboard: `j`/`k` navigates rows, `Enter` opens email, `/` focuses search

- [ ] **Step 6.4: Commit**

```bash
git add web/public/index.php
git commit -m "feat(ui): three-panel layout, sidebar, email list, detail, theme picker, inline previews, VT badges"
```

---

## Task 7: Final Integration Test

**Files:** none modified

- [ ] **Step 7.1: Run the Docker QA script**

```bash
docker compose run --rm app bash docker/qa-archive-docker.sh
```

Expected: `ALL STEPS PASSED` (or equivalent success message from the script)

- [ ] **Step 7.2: Manual smoke-test checklist**

With the app running at `http://localhost:8080`:

1. **Sidebar** — all mailboxes listed; clicking one filters results; "Alle Postfächer" shows combined view
2. **Search** — type a keyword and press Enter; results narrow; highlighted matches appear in rows and detail
3. **Filters** — set a date range; toggle 📎; verify results change
4. **Sort** — change sort dropdown; order updates
5. **Pagination** — if >100 results, pagination appears at bottom of middle panel
6. **Email detail** — click any email; subject/meta/body appear in right panel; keyboard j/k/Enter work
7. **Attachment chip** — if email has attachments, chips appear; `○` badge (not scanned) visible
8. **Attachment preview** — image attachments render inline; PDF renders in iframe; click image opens full-size in new tab
9. **Theme** — click 🎨; try each swatch; toggle dark/light; reload page to confirm persistence
10. **CSV export** — click ⬇ CSV link in list header; file downloads with correct columns
11. **Localhost binding** — confirm `http://localhost:8080` works; try reaching from another device on same network — should time out

- [ ] **Step 7.3: Verify VT disabled gracefully**

With `VT_API_KEY=` empty in `.env`:
- No VT badges shown on attachment chips (only `○` placeholders)
- Clicking ↓ Download on an attachment downloads immediately — no VT redirect page

- [ ] **Step 7.4: Final commit**

```bash
git add .
git status  # verify only expected files changed; nothing sensitive
git commit -m "chore: production-ready — three-panel UI, VT integration, Docker hardening"
```

---

## Quick Reference

```bash
# Start stack
docker compose up -d --build

# Run migrations (needed once after first pull)
docker compose run --rm app php web/src/cli/migrate.php

# Import archive data (admin machine only)
docker compose run --rm app php web/src/cli/import_archive.php \
  --sqlite /app/data/index/mail_index.sqlite

# Run QA
docker compose run --rm app bash docker/qa-archive-docker.sh

# Coworker .env minimum
MRIJA_DB_ROOT_PASSWORD=<strong-password>
MRIJA_DB_PASSWORD=<strong-password>
VT_API_KEY=<coworker-vt-key>
```
