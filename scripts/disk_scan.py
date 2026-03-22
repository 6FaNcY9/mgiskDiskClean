#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
disk_scan.py  --  Python 2.7 compatible full disk scanner
Scans all accessible paths and generates a structured HTML report.

Usage:
    python disk_scan.py [scan_root]
    python disk_scan.py /var/www/mrija_org/data     # scan specific path
    python disk_scan.py                              # scan from home dir

Output: disk_report.html in current working directory
"""

from __future__ import print_function
import os, sys, stat, time, hashlib, json, codecs, platform
from datetime import datetime
from collections import defaultdict

# ── config ─────────────────────────────────────────────────────────────────

# directories to always skip (system/virtual filesystems)
SKIP_DIRS = {
    "/proc", "/sys", "/dev", "/run", "/snap",
    "/sys/kernel", "/sys/fs", "/proc/net",
}

# file extensions grouped by category
EXT_MAP = {
    "image":    {".jpg",".jpeg",".png",".gif",".bmp",".webp",".svg",".tiff",".tif",".ico",".heic",".raw"},
    "video":    {".mp4",".avi",".mkv",".mov",".wmv",".flv",".webm",".m4v",".mpg",".mpeg",".3gp"},
    "audio":    {".mp3",".wav",".flac",".aac",".ogg",".wma",".m4a",".opus"},
    "document": {".pdf",".doc",".docx",".odt",".rtf",".txt",".md",".pages"},
    "spreadsheet": {".xls",".xlsx",".ods",".csv"},
    "presentation": {".ppt",".pptx",".odp"},
    "archive":  {".zip",".tar",".gz",".bz2",".xz",".7z",".rar",".tgz",".tar.gz"},
    "code":     {".py",".php",".js",".ts",".html",".htm",".css",".sh",".bash",".rb",".go",".rs",".c",".cpp",".h",".java",".sql"},
    "mail":     {".eml",".mbox",".mbx"},
    "log":      {".log",".log1",".log2"},
    "tmp":      {".tmp",".temp",".swp",".swo",".bak",".old",".orig"},
    "db":       {".db",".sqlite",".sqlite3",".sql"},
    "config":   {".conf",".cfg",".ini",".env",".yaml",".yml",".toml",".json",".xml"},
}

# files/dirs that are safe-to-flag as cleanup candidates
CLEANUP_PATTERNS = [
    "*.log", "*.tmp", "*.temp", "*.bak", "*.old", "*.orig",
    "*.swp", "*.swo", "thumbs.db", ".ds_store",
    "core", "core.*",
]

AGE_WARN_DAYS   = 365    # flag files not accessed in 1 year
SIZE_LARGE_MB   = 50     # flag single files > 50MB
SIZE_MEDIUM_MB  = 10     # medium threshold

# ── helpers ────────────────────────────────────────────────────────────────

def to_u(s):
    if s is None: return u""
    if isinstance(s, unicode): return s
    try: return s.decode("utf-8", "replace")
    except: return unicode(repr(s))

def esc(s):
    s = to_u(s)
    return s.replace(u"&",u"&amp;").replace(u"<",u"&lt;").replace(u">",u"&gt;").replace(u'"',u"&quot;")

def human_size(n):
    for unit in (u"B",u"KB",u"MB",u"GB",u"TB"):
        if abs(n) < 1024.0:
            return u"%.1f %s" % (n, unit)
        n /= 1024.0
    return u"%.1f TB" % n

def file_ext(path):
    _, ext = os.path.splitext(path)
    return ext.lower()

def categorize(path):
    ext = file_ext(path)
    for cat, exts in EXT_MAP.items():
        if ext in exts:
            return cat
    return u"other"

def days_ago(ts):
    return int((time.time() - ts) / 86400)

def is_skip(path):
    for skip in SKIP_DIRS:
        if path.startswith(skip):
            return True
    return False

def is_cleanup_candidate(name, size, mtime):
    name_l = name.lower()
    ext = os.path.splitext(name_l)[1]
    if ext in (".log",".tmp",".temp",".bak",".old",".orig",".swp",".swo"):
        return True
    if name_l in ("thumbs.db", ".ds_store", "desktop.ini"):
        return True
    if name_l.startswith("core.") and name_l[5:].isdigit():
        return True
    if name_l == "core" and size > 0:
        return True
    return False

# ── scanning ───────────────────────────────────────────────────────────────

class Scanner(object):
    def __init__(self, root):
        self.root         = root
        self.total_size   = 0
        self.total_files  = 0
        self.total_dirs   = 0
        self.errors       = []
        self.by_cat       = defaultdict(lambda: {"count":0,"size":0})
        self.by_ext       = defaultdict(lambda: {"count":0,"size":0})
        self.by_dir       = {}          # top-level dir -> size
        self.large_files  = []          # files > SIZE_LARGE_MB
        self.old_files    = []          # not accessed in AGE_WARN_DAYS
        self.cleanup_files= []          # tmp/log/bak candidates
        self.top_dirs     = []          # largest subdirs
        self._dir_sizes   = defaultdict(int)

    def scan(self):
        print(u"  Scanning: %s" % self.root)
        start = time.time()

        for dirpath, dirs, files in os.walk(self.root, topdown=True, onerror=self._onerr):
            # prune skip dirs in-place
            dirs[:] = [d for d in dirs
                       if not is_skip(os.path.join(dirpath, d))
                       and not d.startswith(".") or d in (".maildir", ".Sent", ".Trash", ".Drafts")]

            self.total_dirs += 1

            for fname in files:
                fpath = os.path.join(dirpath, fname)
                try:
                    st = os.lstat(fpath)
                except OSError:
                    continue

                # skip symlinks
                if stat.S_ISLNK(st.st_mode):
                    continue

                size  = st.st_size
                mtime = st.st_mtime
                atime = st.st_atime
                cat   = categorize(fname)
                ext   = file_ext(fname) or u"(none)"

                self.total_files += 1
                self.total_size  += size
                self.by_cat[cat]["count"] += 1
                self.by_cat[cat]["size"]  += size
                self.by_ext[ext]["count"] += 1
                self.by_ext[ext]["size"]  += size

                # accumulate into top-level dirs
                rel = os.path.relpath(dirpath, self.root)
                top = rel.split(os.sep)[0]
                self._dir_sizes[top] += size

                now = time.time()
                age_days = days_ago(atime)

                entry = {
                    "path":     to_u(fpath),
                    "name":     to_u(fname),
                    "size":     size,
                    "size_h":   human_size(size),
                    "mtime":    datetime.fromtimestamp(mtime).strftime("%Y-%m-%d"),
                    "atime":    datetime.fromtimestamp(atime).strftime("%Y-%m-%d"),
                    "age_days": age_days,
                    "cat":      to_u(cat),
                    "ext":      to_u(ext),
                }

                if size > SIZE_LARGE_MB * 1024 * 1024:
                    self.large_files.append(entry)

                if age_days > AGE_WARN_DAYS and size > 1024:
                    self.old_files.append(entry)

                if is_cleanup_candidate(fname, size, mtime):
                    self.cleanup_files.append(entry)

        # sort and trim
        self.large_files.sort(key=lambda x: -x["size"])
        self.old_files.sort(key=lambda x: -x["size"])
        self.old_files = self.old_files[:100]
        self.cleanup_files.sort(key=lambda x: -x["size"])
        self.large_files = self.large_files[:50]

        # top dirs
        self.top_dirs = sorted(
            [{"name": to_u(k), "size": v, "size_h": human_size(v)}
             for k, v in self._dir_sizes.items()],
            key=lambda x: -x["size"]
        )[:30]

        elapsed = time.time() - start
        print(u"  Done in %.1fs — %d files, %d dirs, %s" % (
            elapsed, self.total_files, self.total_dirs, human_size(self.total_size)))

    def _onerr(self, err):
        self.errors.append(to_u(str(err)))

    def disk_info(self):
        try:
            st = os.statvfs(self.root)
            total = st.f_blocks * st.f_frsize
            free  = st.f_bavail * st.f_frsize
            used  = total - free
            pct   = int(100.0 * used / total) if total > 0 else 0
            return {"total": total, "used": used, "free": free, "pct": pct,
                    "total_h": human_size(total), "used_h": human_size(used), "free_h": human_size(free)}
        except Exception:
            return None

# ── HTML report ────────────────────────────────────────────────────────────

CAT_COLORS = {
    "image":        "#83a598",
    "video":        "#d3869b",
    "audio":        "#d3869b",
    "document":     "#fb4934",
    "spreadsheet":  "#b8bb26",
    "presentation": "#fe8019",
    "archive":      "#fabd2f",
    "code":         "#8ec07c",
    "mail":         "#83a598",
    "log":          "#a89984",
    "tmp":          "#665c54",
    "db":           "#d79921",
    "config":       "#458588",
    "other":        "#504945",
}

HTML_TEMPLATE = u"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Disk Scan Report</title>
<style>
:root{--bg:#1d2021;--bg1:#282828;--bg2:#3c3836;--bg3:#504945;--fg:#ebdbb2;--fg2:#a89984;--fg3:#7c6f64;--red:#fb4934;--grn:#b8bb26;--ylw:#fabd2f;--blu:#83a598;--pur:#d3869b;--org:#fe8019;--aqua:#8ec07c}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:13px/1.5 'JetBrains Mono','Fira Mono',monospace}
.topbar{background:var(--bg1);border-bottom:1px solid var(--bg3);padding:.75rem 1.5rem;display:flex;align-items:baseline;gap:1rem;flex-wrap:wrap;position:sticky;top:0;z-index:100}
.topbar h1{font-size:14px;font-weight:500;color:var(--ylw);flex:1}
.topbar span{font-size:11px;color:var(--fg2)}
.disk-bar-wrap{padding:.75rem 1.5rem;background:var(--bg1);border-bottom:1px solid var(--bg3)}
.disk-bar{height:18px;background:var(--bg3);border-radius:4px;overflow:hidden;position:relative}
.disk-bar-fill{height:100%;border-radius:4px;transition:width .3s}
.disk-bar-lbl{font-size:10px;color:var(--fg2);margin-top:4px;display:flex;justify-content:space-between}
.tabs{display:flex;gap:0;border-bottom:1px solid var(--bg3);background:var(--bg1);padding:0 1.5rem;overflow-x:auto}
.tab{padding:.55rem 1.1rem;font-size:11px;color:var(--fg2);cursor:pointer;border-bottom:2px solid transparent;white-space:nowrap;letter-spacing:.04em}
.tab:hover{color:var(--fg)}
.tab.active{color:var(--ylw);border-bottom-color:var(--ylw)}
.tab.warn{color:var(--org)}
.tab.warn.active{color:var(--org);border-bottom-color:var(--org)}
.pane{display:none;padding:1.25rem 1.5rem}
.pane.active{display:block}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:1.25rem}
.metric{background:var(--bg1);border:1px solid var(--bg3);border-radius:6px;padding:.7rem .9rem}
.metric-val{font-size:18px;font-weight:500;line-height:1.1}
.metric-lbl{font-size:10px;color:var(--fg2);margin-top:2px;text-transform:uppercase;letter-spacing:.05em}
.charts-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.chart-box{background:var(--bg1);border:1px solid var(--bg3);border-radius:6px;padding:1rem}
.chart-box.full{grid-column:1/-1}
.chart-title{font-size:10px;color:var(--fg2);letter-spacing:.08em;text-transform:uppercase;margin-bottom:.75rem}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:.6rem}
.leg-item{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--fg2)}
.leg-dot{width:8px;height:8px;border-radius:2px;flex-shrink:0}
table{width:100%;border-collapse:collapse;font-size:11px}
th{background:var(--bg2);color:var(--ylw);text-align:left;padding:.4rem .65rem;border-bottom:2px solid var(--bg3);cursor:pointer;user-select:none;white-space:nowrap;font-size:10px;text-transform:uppercase;letter-spacing:.05em}
th:hover{color:var(--org)}
td{padding:.38rem .65rem;border-bottom:1px solid var(--bg2);vertical-align:top;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:hover td{background:var(--bg1)}
tr.hidden{display:none}
.sz{text-align:right;font-variant-numeric:tabular-nums;color:var(--grn)}
.sz.big{color:var(--red);font-weight:500}
.sz.med{color:var(--ylw)}
.cat-tag{border-radius:3px;padding:1px 5px;font-size:10px;font-weight:500}
.age-warn{color:var(--org)}
.muted{color:var(--fg2);font-size:10px}
.warn-badge{color:var(--org);font-size:10px;margin-left:4px}
.controls{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin-bottom:1rem;padding:.65rem .9rem;background:var(--bg1);border:1px solid var(--bg3);border-radius:6px}
.controls label{font-size:11px;color:var(--fg2)}
.controls select,.controls input{background:var(--bg2);color:var(--fg);border:1px solid var(--bg3);border-radius:4px;padding:.28rem .55rem;font:inherit;font-size:11px}
.btn{background:var(--bg2);color:var(--fg);border:1px solid var(--bg3);border-radius:4px;padding:.28rem .7rem;cursor:pointer;font:inherit;font-size:11px}
.btn:hover{border-color:var(--ylw);color:var(--ylw)}
.section-title{font-size:10px;color:var(--ylw);letter-spacing:.08em;text-transform:uppercase;margin-bottom:.65rem;padding-bottom:.35rem;border-bottom:1px solid var(--bg3)}
.summary{margin-top:.65rem;font-size:10px;color:var(--fg2)}
.error-list{font-size:10px;color:var(--org);line-height:1.8}
@media(max-width:640px){.charts-row{grid-template-columns:1fr}}
</style>
</head>
<body>
"""

def render_table(rows, cols, table_id, sort_col="size"):
    """Render a sortable HTML table."""
    lines = []
    lines.append(u'<table id="%s"><thead><tr>' % table_id)
    for key, label in cols:
        lines.append(u'<th data-col="%s">%s <span class="si">\u21c5</span></th>' % (key, label))
    lines.append(u'</tr></thead><tbody id="%s-body">' % table_id)
    for row in rows:
        sz = row.get("size", 0)
        big = sz > SIZE_LARGE_MB * 1024 * 1024
        med = sz > SIZE_MEDIUM_MB * 1024 * 1024
        szc = u"sz big" if big else (u"sz med" if med else u"sz")
        age = row.get("age_days", 0)
        agec = u" age-warn" if age > AGE_WARN_DAYS else u""
        col = CAT_COLORS.get(row.get("cat", "other"), "#504945")
        lines.append(u'<tr data-row="1">')
        for key, label in cols:
            v = row.get(key, u"")
            if key == "size":
                lines.append(u'<td class="%s" data-col="%s" data-raw="%d">%s</td>' % (szc, key, sz, esc(row.get("size_h",u""))))
            elif key == "cat":
                lines.append(u'<td data-col="%s"><span class="cat-tag" style="background:%s22;color:%s">%s</span></td>' % (key, col, col, esc(to_u(v))))
            elif key in ("mtime","atime","age_days"):
                lines.append(u'<td class="muted%s" data-col="%s">%s</td>' % (agec, key, esc(to_u(v))))
            elif key == "path":
                lines.append(u'<td class="muted" data-col="%s" title="%s">%s</td>' % (key, esc(to_u(v)), esc(to_u(v)[-60:])))
            elif key == "name":
                lines.append(u'<td data-col="%s" title="%s">%s</td>' % (key, esc(to_u(v)), esc(to_u(v)[:50])))
            else:
                lines.append(u'<td data-col="%s">%s</td>' % (key, esc(to_u(v))))
        lines.append(u'</tr>')
    lines.append(u'</tbody></table>')
    return u"\n".join(lines)

def build_html(scanner, disk_info, scan_root):
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    s = scanner

    # category data for JS
    cats    = sorted(s.by_cat.keys(), key=lambda c: -s.by_cat[c]["size"])
    cat_js  = u"{" + u",".join(u'"%s":{"count":%d,"size":%d}' % (c, s.by_cat[c]["count"], s.by_cat[c]["size"]) for c in cats) + u"}"
    colors_js = u"{" + u",".join(u'"%s":"%s"' % (k, v) for k, v in CAT_COLORS.items()) + u"}"
    dirs_js = u"[" + u",".join(u'{"name":"%s","size":%d}' % (esc(d["name"]), d["size"]) for d in s.top_dirs[:15]) + u"]"

    disk_pct = disk_info["pct"] if disk_info else 0
    disk_color = "#fb4934" if disk_pct > 85 else "#fabd2f" if disk_pct > 70 else "#b8bb26"

    lines = [HTML_TEMPLATE]

    # inject JS data
    lines.append(u'<script>var _CAT=%s;var _COLORS=%s;var _DIRS=%s;</script>' % (cat_js, colors_js, dirs_js))

    # topbar
    lines.append(u'<div class="topbar"><h1>\U0001f4be Disk Scan &mdash; %s</h1><span>%s</span></div>' % (esc(to_u(scan_root)), generated))

    # disk usage bar
    if disk_info:
        lines.append(u'<div class="disk-bar-wrap">')
        lines.append(u'<div class="disk-bar"><div class="disk-bar-fill" style="width:%d%%;background:%s"></div></div>' % (disk_pct, disk_color))
        lines.append(u'<div class="disk-bar-lbl"><span>Used: %s of %s (%d%%)</span><span>Free: %s</span></div>' % (
            disk_info["used_h"], disk_info["total_h"], disk_pct, disk_info["free_h"]))
        lines.append(u'</div>')

    # tabs
    cleanup_count = len(s.cleanup_files)
    large_count   = len(s.large_files)
    old_count     = len(s.old_files)
    lines.append(u'<div class="tabs">')
    tabs = [
        (u"pOverview", u"Overview",                        u""),
        (u"pLarge",    u"Large Files (%d)" % large_count,  u" warn" if large_count else u""),
        (u"pOld",      u"Old Files (%d)" % old_count,      u""),
        (u"pCleanup",  u"Cleanup (%d)" % cleanup_count,    u" warn" if cleanup_count else u""),
        (u"pAll",      u"All Files",                        u""),
        (u"pErrors",   u"Errors (%d)" % len(s.errors),     u""),
    ]
    for i, (pane_id, label, extra) in enumerate(tabs):
        active = u" active" if i == 0 else u""
        lines.append(u'<div class="tab%s%s" data-pane="%s">%s</div>' % (active, extra, pane_id, label))
    lines.append(u'</div>')

    # ── overview pane ──────────────────────────────────────────────────────
    lines.append(u'<div class="pane active" id="pOverview">')
    lines.append(u'<div class="metrics">')
    metrics = [
        (human_size(s.total_size), u"scanned size",  u"var(--org)"),
        (unicode(s.total_files),   u"files",          u"var(--ylw)"),
        (unicode(s.total_dirs),    u"directories",    u"var(--blu)"),
        (unicode(large_count),     u"large files",    u"var(--red)"),
        (unicode(old_count),       u"old files",      u"var(--pur)"),
        (unicode(cleanup_count),   u"cleanup cands.", u"var(--org)"),
        (unicode(len(s.errors)),   u"errors",         u"var(--fg2)"),
    ]
    for val, lbl, color in metrics:
        lines.append(u'<div class="metric"><div class="metric-val" style="color:%s">%s</div><div class="metric-lbl">%s</div></div>' % (color, val, lbl))
    lines.append(u'</div>')

    lines.append(u'<div class="charts-row">')
    lines.append(u'<div class="chart-box"><div class="chart-title">size by category</div><div class="legend" id="catLeg"></div><div style="position:relative;height:200px"><canvas id="cCat"></canvas></div></div>')
    lines.append(u'<div class="chart-box"><div class="chart-title">top 15 directories</div><div style="position:relative;height:220px"><canvas id="cDirs"></canvas></div></div>')
    lines.append(u'</div>')

    # top dirs table
    lines.append(u'<div class="section-title" style="margin-top:1.25rem">top directories by size</div>')
    lines.append(u'<table><thead><tr><th>Directory</th><th>Size</th></tr></thead><tbody>')
    for d in s.top_dirs:
        big = d["size"] > SIZE_LARGE_MB * 1024 * 1024
        szc = u"sz big" if big else u"sz"
        lines.append(u'<tr><td>%s</td><td class="%s">%s</td></tr>' % (esc(d["name"]), szc, esc(d["size_h"])))
    lines.append(u'</tbody></table>')
    lines.append(u'</div>')

    # ── large files pane ───────────────────────────────────────────────────
    lines.append(u'<div class="pane" id="pLarge">')
    lines.append(u'<div class="section-title">files larger than %dMB</div>' % SIZE_LARGE_MB)
    cols = [(u"name",u"filename"),(u"path",u"path"),(u"cat",u"type"),(u"mtime",u"modified"),(u"size",u"size")]
    lines.append(render_table(s.large_files, cols, u"tLarge"))
    lines.append(u'</div>')

    # ── old files pane ─────────────────────────────────────────────────────
    lines.append(u'<div class="pane" id="pOld">')
    lines.append(u'<div class="section-title">files not accessed in over %d days (top 100 by size)</div>' % AGE_WARN_DAYS)
    cols = [(u"name",u"filename"),(u"path",u"path"),(u"cat",u"type"),(u"atime",u"last access"),(u"age_days",u"days ago"),(u"size",u"size")]
    lines.append(render_table(s.old_files, cols, u"tOld"))
    lines.append(u'</div>')

    # ── cleanup pane ───────────────────────────────────────────────────────
    lines.append(u'<div class="pane" id="pCleanup">')
    lines.append(u'<div class="section-title">safe cleanup candidates &mdash; tmp, log, bak, old, swp files</div>')
    total_cleanup = sum(f["size"] for f in s.cleanup_files)
    lines.append(u'<p style="font-size:11px;color:var(--org);margin-bottom:.75rem">\u26a0 Always review before deleting. Total recoverable: <strong>%s</strong></p>' % human_size(total_cleanup))
    cols = [(u"name",u"filename"),(u"path",u"path"),(u"ext",u"ext"),(u"mtime",u"modified"),(u"size",u"size")]
    lines.append(render_table(s.cleanup_files, cols, u"tCleanup"))
    lines.append(u'</div>')

    # ── all files pane ─────────────────────────────────────────────────────
    lines.append(u'<div class="pane" id="pAll">')
    cat_opts = u"".join(u'<option value="%s">%s</option>' % (c, c) for c in sorted(s.by_cat.keys()))
    lines.append(u'''<div class="controls">
  <label>Category: <select id="fAllCat"><option value="">All</option>%s</select></label>
  <label>Min size (KB): <input type="number" id="fAllSz" value="0" min="0" style="width:80px"/></label>
  <label>Search: <input type="text" id="fAllSearch" placeholder="filename or path" style="width:180px"/></label>
  <button class="btn" id="fAllReset">Reset</button>
</div>''' % cat_opts)

    # all files — limit to top 2000 by size to keep HTML manageable
    all_files_sorted = sorted(
        [{"name": to_u(os.path.basename(f["path"])),
          "path": f["path"], "cat": f["cat"], "ext": f["ext"],
          "size": f["size"], "size_h": f["size_h"],
          "mtime": f["mtime"], "atime": f["atime"], "age_days": f["age_days"]}
         for f in (s.large_files + s.old_files + s.cleanup_files)],
        key=lambda x: -x["size"]
    )
    # deduplicate by path
    seen_paths = set()
    deduped = []
    for f in all_files_sorted:
        if f["path"] not in seen_paths:
            seen_paths.add(f["path"])
            deduped.append(f)

    cols = [(u"name",u"filename"),(u"path",u"path"),(u"cat",u"type"),(u"ext",u"ext"),(u"mtime",u"modified"),(u"size",u"size")]
    lines.append(render_table(deduped[:500], cols, u"tAll"))
    lines.append(u'<div class="summary" id="sum-all"></div>')
    lines.append(u'</div>')

    # ── errors pane ────────────────────────────────────────────────────────
    lines.append(u'<div class="pane" id="pErrors">')
    if s.errors:
        lines.append(u'<div class="error-list">')
        for e in s.errors[:200]:
            lines.append(u'<div>%s</div>' % esc(e))
        lines.append(u'</div>')
    else:
        lines.append(u'<p style="color:var(--grn);font-size:12px">\u2713 No errors during scan.</p>')
    lines.append(u'</div>')

    # JS
    lines.append(u"""
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
var grid={color:'rgba(80,73,69,0.4)'};
var tick={color:'#a89984',font:{size:10}};
function hs(n){var u=['B','KB','MB','GB','TB'];for(var i=0;i<u.length;i++){if(Math.abs(n)<1024)return n.toFixed(1)+' '+u[i];n/=1024;}return n.toFixed(1)+' TB';}

// tabs
document.querySelectorAll('.tab').forEach(function(t){
  t.addEventListener('click',function(){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active');});
    document.querySelectorAll('.pane').forEach(function(x){x.classList.remove('active');});
    t.classList.add('active');
    document.getElementById(t.dataset.pane).classList.add('active');
  });
});

// category donut
(function(){
  var cats=Object.keys(_CAT).sort(function(a,b){return _CAT[b].size-_CAT[a].size;});
  var sizes=cats.map(function(c){return (_CAT[c].size/1024/1024).toFixed(2);});
  var counts=cats.map(function(c){return _CAT[c].count;});
  var colors=cats.map(function(c){return _COLORS[c]||'#504945';});
  var leg=document.getElementById('catLeg');
  cats.forEach(function(c,i){
    var el=document.createElement('span');el.className='leg-item';
    el.innerHTML='<span class="leg-dot" style="background:'+colors[i]+'"></span>'+c+' ('+counts[i]+')';
    leg.appendChild(el);
  });
  new Chart(document.getElementById('cCat'),{
    type:'doughnut',
    data:{labels:cats,datasets:[{data:sizes,backgroundColor:colors,borderWidth:1,borderColor:'#1d2021'}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'55%',plugins:{legend:{display:false},tooltip:{callbacks:{label:function(c){return c.label+': '+c.parsed.toFixed(1)+' MB ('+counts[c.dataIndex]+' files)';}}}}}
  });
})();

// dirs bar chart
(function(){
  var labels=_DIRS.map(function(d){return d.name;});
  var sizes=_DIRS.map(function(d){return (d.size/1024/1024).toFixed(1);});
  new Chart(document.getElementById('cDirs'),{
    type:'bar',
    data:{labels:labels,datasets:[{data:sizes,backgroundColor:'#fabd2f',borderRadius:3,borderSkipped:false}]},
    options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:function(c){return c.parsed.x.toFixed(1)+' MB';}}}},scales:{x:{ticks:{...tick,callback:function(v){return v+'MB';}},grid:grid},y:{ticks:tick,grid:grid}}}
  });
})();

// generic sortable table
function makeTableSortable(tableId,summaryId){
  var rows=Array.from(document.querySelectorAll('#'+tableId+'-body tr[data-row]'));
  var tbody=document.getElementById(tableId+'-body');
  var sortCol='size',sortDir=-1;
  function val(row,col){var el=row.querySelector('[data-col="'+col+'"]');if(!el)return'';var r=el.dataset.raw!==undefined?el.dataset.raw:el.textContent;return isNaN(r)?r.toLowerCase():Number(r);}
  function sort(col){
    if(sortCol===col)sortDir*=-1;else{sortCol=col;sortDir=-1;}
    rows.sort(function(a,b){var va=val(a,col),vb=val(b,col);return va<vb?sortDir:va>vb?-sortDir:0;});
    rows.forEach(function(r){tbody.appendChild(r);});
    document.querySelectorAll('#'+tableId+' th').forEach(function(th){var ic=th.querySelector('.si');if(ic)ic.textContent=th.dataset.col===col?(sortDir>0?'\u25b2':'\u25bc'):'\u21c5';});
    if(summaryId)updateSummary();
  }
  function updateSummary(){
    if(!summaryId)return;
    var vis=rows.filter(function(r){return!r.classList.contains('hidden');});
    var tot=vis.reduce(function(s,r){var el=r.querySelector('[data-col="size"]');return s+(el?Number(el.dataset.raw||0):0);},0);
    var el=document.getElementById(summaryId);
    if(el)el.textContent=vis.length+' of '+rows.length+' files — '+hs(tot);
  }
  document.querySelectorAll('#'+tableId+' th[data-col]').forEach(function(th){th.addEventListener('click',function(){sort(th.dataset.col);});});
  sort(sortCol);
  if(summaryId)updateSummary();
  return {rows:rows,sort:sort,updateSummary:updateSummary};
}
makeTableSortable('tLarge');
makeTableSortable('tOld');
makeTableSortable('tCleanup');
var allTable=makeTableSortable('tAll','sum-all');

// all files filter
document.getElementById('fAllCat').addEventListener('change',filterAll);
document.getElementById('fAllSz').addEventListener('input',filterAll);
document.getElementById('fAllSearch').addEventListener('input',filterAll);
document.getElementById('fAllReset').addEventListener('click',function(){
  document.getElementById('fAllCat').value='';
  document.getElementById('fAllSz').value='0';
  document.getElementById('fAllSearch').value='';
  filterAll();
});
function filterAll(){
  var cat=document.getElementById('fAllCat').value.toLowerCase();
  var minSz=Number(document.getElementById('fAllSz').value)*1024;
  var search=document.getElementById('fAllSearch').value.toLowerCase();
  allTable.rows.forEach(function(r){
    var catv=r.querySelector('[data-col="cat"]').textContent.toLowerCase();
    var sz=Number(r.querySelector('[data-col="size"]').dataset.raw||0);
    var name=(r.querySelector('[data-col="name"]')||{textContent:''}).textContent.toLowerCase();
    var path=(r.querySelector('[data-col="path"]')||{textContent:''}).textContent.toLowerCase();
    var show=(!cat||catv.includes(cat))&&sz>=minSz&&(!search||name.includes(search)||path.includes(search));
    r.classList.toggle('hidden',!show);
  });
  allTable.updateSummary();
}
</script>
</body></html>""")

    return u"\n".join(lines)

# ── main ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        scan_root = os.path.realpath(sys.argv[1])
    else:
        scan_root = os.path.realpath(os.path.expanduser("~"))

    if not os.path.exists(scan_root):
        print("ERROR: path does not exist: %s" % scan_root)
        sys.exit(1)

    print("\n=== disk_scan.py ===")
    print("  Root   : %s" % scan_root)
    print("  Python : %s" % platform.python_version())
    print("")

    scanner = Scanner(scan_root)
    scanner.scan()
    disk_info = scanner.disk_info()

    if disk_info:
        print(u"  Disk   : %s used / %s total (%d%%)" % (
            disk_info["used_h"], disk_info["total_h"], disk_info["pct"]))

    print(u"  Large  : %d files > %dMB" % (len(scanner.large_files), SIZE_LARGE_MB))
    print(u"  Old    : %d files not accessed in %d+ days" % (len(scanner.old_files), AGE_WARN_DAYS))
    print(u"  Cleanup: %d candidates (%s)" % (
        len(scanner.cleanup_files),
        human_size(sum(f["size"] for f in scanner.cleanup_files))))

    out = os.path.join(os.getcwd(), "disk_report.html")
    with codecs.open(out, "w", encoding="utf-8") as f:
        f.write(build_html(scanner, disk_info, scan_root))

    print(u"\n  Report : %s" % out)
    print(u"  Done.\n")

if __name__ == "__main__":
    main()
