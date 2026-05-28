# Design: Production UI Redesign + Hardening

**Date:** 2026-05-28
**Status:** Approved

## Overview

Redesign the Mrija Archive web app for production handoff to a coworker on Windows + Docker. The coworker browses and reads archived mrija.org emails — they never sync or import data (that stays on the admin machine). The app runs locally on their machine, so no login is needed, but it must be locked to localhost.

Key deliverables:
1. Three-panel UI (email client layout) — space grey, dark/light mode, user-selectable accent colour
2. Inline attachment preview (images + PDFs render in-app)
3. VirusTotal scan badges + download gating
4. Production Docker hardening (localhost binding, VT key wiring, remove pywebview button)

---

## Architecture

**Approach: Rewrite `index.php` in place, add two service classes, one migration.**

No new routing layer, no JS framework. One well-structured PHP file for the UI, two focused service classes, CSS custom properties for theming.

### New / changed files

| File | Change |
|------|--------|
| `web/public/index.php` | Full rewrite — three-panel layout, theme system, VT badges |
| `web/public/download.php` | Add VT check flow, add `?inline=1` mode |
| `web/src/VirusTotal/VtService.php` | New — VT API + cache logic |
| `web/migrations/002_vt_cache.sql` | New — `vt_cache` table |
| `docker-compose.yml` | Localhost port binding, VT_API_KEY env var |
| `web/config/local.php.docker` | Add `vt_api_key` from env |
| `.env.example` | Add `VT_API_KEY=` |

---

## Section 1: UI Layout

Three fixed panels, full-viewport height, no page scroll.

### Left sidebar (~160px)

- App title "Mrija Archive" at top
- Mailbox list: each entry shows name, email count, total size — clicking filters the middle panel
- Active mailbox highlighted with accent colour
- Footer: total email count, last import date, 🎨 theme picker button

### Middle panel (~300px)

- Search input at top (full-text FULLTEXT search — subject, sender, body)
- Filter bar below search: date-from / date-to, "nur mit Anhang" toggle, sort dropdown
- Email list: each row — subject (bold), sender, date, 📎 badge if has attachments, mailbox tag if "all mailboxes" view
- Active row: left border in accent colour, slightly lighter background
- Result count + CSV export link above list
- Pagination at bottom

### Right panel (flex, fills remaining width)

- **Empty state:** centred placeholder, keyboard shortcut hints (j/k/Enter//)
- **Email open:**
  - Subject as `h1`-level heading
  - Metadata grid: Von / An / Cc (if present) / Datum / Postfach / Größe
  - Body text (pre-wrap, scrollable)
  - Attachments section (see Section 4)

### Removed

- `■ Stop` toolbar button (pywebview-only, irrelevant to Docker)
- Top toolbar as a separate bar — mailbox switching moves to sidebar, search moves to middle panel top

---

## Section 2: Theme System

All colours are CSS custom properties on `:root`. JS swaps them on user interaction with no page reload.

### Dark mode base (default)

```
--bg-0: #0d0d0d   (deepest — sidebar)
--bg-1: #111111   (main backgrounds)
--bg-2: #1a1a1a   (cards, rows)
--bg-3: #222222   (hover states)
--border: #2a2a2a
--text-1: #e8e8e8  (primary)
--text-2: #888888  (secondary)
--text-3: #444444  (muted)
```

### Light mode base

```
--bg-0: #ebebeb
--bg-1: #f5f5f5
--bg-2: #ffffff
--bg-3: #e0e0e0
--border: #d4d4d4
--text-1: #1a1a1a
--text-2: #666666
--text-3: #aaaaaa
```

### Accent options (6 total)

| Name | Hex | Default |
|------|-----|---------|
| Monochrome | `#c0c0c0` (dark) / `#555555` (light) | ✓ |
| Steel Blue | `#4a90d9` | |
| Teal | `#2a9d8f` | |
| Amber | `#d4900a` | |
| Sage | `#6a9f6a` | |
| Rose | `#c0606a` | |

### Persistence

`localStorage` keys: `mrija-accent`, `mrija-mode`.
An inline `<script>` in `<head>` (before any CSS render) reads these and sets `data-accent` and `data-mode` attributes on `<html>`. This prevents flash of wrong theme on load.

### Theme picker popover

Triggered by 🎨 in sidebar footer. Small floating panel:
- 6 colour swatches (20px circles, clickable)
- Dark / Light toggle switch
- Closes on outside click

---

## Section 3: VirusTotal Integration

### Database

`web/migrations/002_vt_cache.sql`:

```sql
CREATE TABLE IF NOT EXISTS vt_cache (
    sha256      CHAR(64)    NOT NULL PRIMARY KEY,
    status      ENUM('pending','clean','infected','error') NOT NULL DEFAULT 'pending',
    scan_id     VARCHAR(64) NOT NULL DEFAULT '',
    positives   TINYINT     NOT NULL DEFAULT 0,
    total       SMALLINT    NOT NULL DEFAULT 0,
    scanned_at  DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### VtService

`web/src/VirusTotal/VtService.php` — single public method:

```php
public function check(string $sha256, string $filePath): array
// Returns: ['status' => 'clean'|'infected'|'pending'|'error'|'disabled', 'positives' => int]
```

Logic:
1. If `vt_api_key` is empty in config → return `['status' => 'disabled']`
2. Check `vt_cache` by sha256 — if hit and status is not `pending` → return cached
3. Cache miss → call VT `/file/report` with the hash
4. If VT says "not found" → submit file via `/file/scan`, store `pending`
5. If VT returns result → store `clean` (positives = 0) or `infected` (positives > 0)
6. On any API error → store `error`, return gracefully

Rate limiting: free tier is 4 req/min. Since scans are triggered on-demand (not batch), this is sufficient. No explicit sleep needed.

### download.php flow

1. **`?inline=1`** (for in-app preview): skip VT check, serve with `Content-Disposition: inline`
2. **Normal download:**
   - Check VT cache
   - `clean` → serve file
   - `infected` → HTTP 403 + German error page ("Datei blockiert — Schadsoftware erkannt")
   - `pending` → HTML page with `<meta http-equiv="refresh" content="4">`, shows "Wird von VirusTotal geprüft…"
   - Not in cache → trigger `VtService::check()`, redirect to same URL (user sees pending page on next load)

### In index.php detail panel

Each attachment chip shows a VT badge:
- `○` grey — not yet scanned
- `⏳` — pending
- `✓ Sauber` green/accent — clean
- `⚠ Infiziert` red — infected (chip is red, no download link)

Badges come from a single `SELECT sha256, status FROM vt_cache WHERE sha256 IN (...)` query when loading the email detail — `index.php` queries the DB directly, no need to instantiate `VtService` here. If VT key is not configured, no badges are shown.

---

## Section 4: Inline Attachment Preview

### Preview types

| MIME | Treatment |
|------|-----------|
| image/jpeg, image/png, image/gif, image/webp, image/svg+xml | `<img>` tag, max-height 300px |
| application/pdf | `<iframe src="download.php?...&inline=1">`, height 420px |
| Everything else | Download chip only |

### Layout in detail panel

Each attachment rendered as a block:
- **Header row:** 📎 filename · size · VT badge · download button (hidden if infected)
- **Preview area** (images/PDFs only): rendered below header row
  - Single attachment: preview open by default
  - Multiple attachments: all collapsed by default, click header row to expand
  - Images: clicking opens `download.php` in new tab (full size)

### download.php `?inline=1`

Sets `Content-Disposition: inline` instead of `attachment`. VT check is skipped — the file is only being rendered inside the app's own iframe, not handed to the OS. Explicit download (the button) still goes through VT.

---

## Section 5: Production Docker Hardening

### docker-compose.yml

```yaml
# web service ports — localhost only
ports:
  - "127.0.0.1:${MRIJA_WEB_PORT:-8080}:8080"

# add VT_API_KEY to web service environment
environment:
  VT_API_KEY: ${VT_API_KEY:-}
```

### .env.example

```
VT_API_KEY=          # Get a free key at virustotal.com
```

### local.php.docker

```php
'vt_api_key' => $_ENV['VT_API_KEY'] ?? getenv('VT_API_KEY') ?: '',
```

### What is already production-safe (no changes needed)

- Sync scripts (`sync-all`, `index-all`, `extract-attachments`) are in `devenv.nix` — never built into the Docker image
- Admin CLI tools (`import_archive.php`, `migrate.php`) are in the image but only accessible via `docker compose run`, not the browser
- Path traversal guard in `download.php` stays unchanged
- Session security in `SessionManager.php` stays unchanged (used if auth is ever added later)

### Coworker setup (final)

```
1. docker compose up -d --build
2. docker compose run --rm app php web/src/cli/migrate.php
3. Open http://localhost:8080
```

`.env` needs: `MRIJA_DB_ROOT_PASSWORD`, `MRIJA_DB_PASSWORD`, `COWORKER_PASSWORD_HASH` (optional, auth not enforced), `VT_API_KEY`.

---

## What is explicitly out of scope

- Login / authentication (app is localhost-only)
- Server-side email deletion
- IMAP sync from the coworker's machine
- Pagination increase beyond current 100/page
- Mobile / responsive layout
