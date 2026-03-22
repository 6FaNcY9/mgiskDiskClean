#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
maildir_viewer.py  --  Python 2.7 compatible
Interactive mail review viewer with duplicate grouping and decisions.

Scans a Maildir, groups cross-mail duplicates, generates a self-contained
interactive HTML report. Boss can mark each mail Keep / Delete / Flag,
then export decisions as JSON for the administrator to action.

Usage:
    python maildir_viewer.py /email/mrija.org/gabriel.hangel/.maildir
    python maildir_viewer.py /email/mrija.org/gabriel.hangel/.maildir .Sent

Output:
    ../mail_viewer.html   -- deploy this to nginx
    ../mail_viewer.json   -- raw data (for debugging)
"""

from __future__ import print_function
import os, sys, email, hashlib, json, codecs
from email.header import decode_header, make_header
from datetime import datetime
from collections import defaultdict

# ── helpers ────────────────────────────────────────────────────────────────

def to_u(s):
    if s is None: return u""
    if isinstance(s, unicode): return s
    if isinstance(s, str):
        try: return s.decode("utf-8", "replace")
        except: return s.decode("latin-1", "replace")
    return unicode(s)

def decode_str(s):
    if s is None: return u""
    try: return to_u(unicode(make_header(decode_header(s))))
    except: return to_u(s)

def parse_date(date_str):
    if not date_str: return u"", u""
    from email.utils import parsedate
    try:
        t = parsedate(date_str)
        if t:
            dt = datetime(*t[:6])
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d %H:%M")
    except: pass
    return u"", to_u(date_str[:30]) if date_str else u""

def human_size(n):
    for unit in (u"B", u"KB", u"MB", u"GB"):
        if n < 1024: return u"%.1f %s" % (n, unit)
        n /= 1024.0
    return u"%.1f GB" % n

SKIP_MIME = {
    "text/plain", "text/html", "multipart/mixed",
    "multipart/alternative", "multipart/related",
    "multipart/signed", "message/rfc822",
}

CAT_MAP = {
    "application/pdf":          "pdf",
    "application/msword":       "word",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "word",
    "application/vnd.ms-excel": "excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "excel",
    "application/vnd.ms-powerpoint": "pptx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/calendar":            "calendar",
    "application/ics":          "calendar",
    "application/zip":          "archive",
    "application/x-zip-compressed": "archive",
    "application/pkcs7-signature":  "signature",
    "application/x-pkcs7-signature": "signature",
}

def classify(ct, fname):
    if ct in CAT_MAP: return CAT_MAP[ct]
    if ct.startswith("image/"): return "image"
    if ct.startswith("video/"): return "video"
    if ct.startswith("audio/"): return "audio"
    if ct.startswith("text/"): return "text"
    if fname: return "attachment"
    return "other"

# ── parsing ────────────────────────────────────────────────────────────────

def parse_mail(filepath, folder_name):
    try:
        raw = open(filepath, "rb").read()
    except: return None
    total_size = len(raw)
    try:
        msg = email.message_from_string(raw)
    except: return None

    date_day, date_fmt = parse_date(msg.get("Date"))
    subject = decode_str(msg.get("Subject", "(no subject)"))
    sender  = decode_str(msg.get("From", ""))
    to      = decode_str(msg.get("To", ""))

    parts = []
    for part in msg.walk():
        ct   = to_u(part.get_content_type().lower())
        disp = to_u(part.get("Content-Disposition") or "")
        disp_type = disp.split(";")[0].strip().lower()
        fname = part.get_filename()
        fname = decode_str(fname) if fname else u""
        if not fname and ct not in SKIP_MIME:
            fname = u"[inline %s]" % ct.split("/")[-1]
        try:
            payload = part.get_payload(decode=True)
        except: payload = None
        size = len(payload) if payload else 0
        if size < 128: continue
        content_hash = u""
        if payload and size > 512:
            content_hash = hashlib.md5(payload).hexdigest()
        cat = classify(ct, fname)
        parts.append({
            u"fname":    fname,
            u"mime":     ct,
            u"category": cat,
            u"size":     size,
            u"hash":     content_hash,
            u"is_dup":   False,
            u"dup_group": None,
        })

    return {
        u"folder":     to_u(folder_name),
        u"date_day":   date_day,
        u"date":       date_fmt,
        u"subject":    subject,
        u"from":       sender,
        u"to":         to,
        u"total_size": total_size,
        u"filepath":   to_u(filepath),
        u"parts":      parts,
    }

def scan_maildir(root):
    mails = []
    total_files = 0
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "tmp"]
        base = os.path.basename(dirpath)
        if base not in ("cur", "new"): continue
        rel = os.path.relpath(dirpath, root)
        parts = rel.replace("\\", "/").split("/")
        folder = parts[0] if parts[0] not in ("cur","new") else u"INBOX"
        for fname in files:
            if fname.startswith("."): continue
            fpath = os.path.join(dirpath, fname)
            total_files += 1
            m = parse_mail(fpath, folder)
            if m: mails.append(m)
    print(u"  Scanned %d files, parsed %d mails." % (total_files, len(mails)))
    return mails

def assign_dup_groups(mails):
    """
    Cross-mail duplicate detection.
    Any part hash appearing in 2+ mails gets a group ID.
    Mails sharing group IDs are duplicates of each other.
    Returns: modified mails list, dup_groups dict
    """
    # hash -> list of (mail_idx, part_idx)
    hash_to_mails = defaultdict(list)
    for mi, m in enumerate(mails):
        seen_in_mail = set()
        for pi, p in enumerate(m[u"parts"]):
            h = p[u"hash"]
            if h and h not in seen_in_mail:
                hash_to_mails[h].append(mi)
                seen_in_mail.add(h)

    # find hashes that appear in multiple mails
    cross_hashes = {h: idxs for h, idxs in hash_to_mails.items() if len(idxs) > 1}

    # union-find to merge mail indices into groups
    parent = {}
    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), parent.get(x, x))
            x = parent.get(x, x)
        return x
    def union(a, b):
        pa, pb = find(a), find(b)
        if pa != pb: parent[pa] = pb

    for h, idxs in cross_hashes.items():
        for i in range(1, len(idxs)):
            union(idxs[0], idxs[i])

    # assign group IDs
    root_to_gid = {}
    gid_counter = [0]
    mail_to_gid = {}
    for mi in range(len(mails)):
        r = find(mi)
        if r in parent or any(find(mi) == find(j) for h, idxs in cross_hashes.items() for j in idxs if j != mi):
            pass
        # check if this mail is in any cross group
        in_group = False
        for h, idxs in cross_hashes.items():
            if mi in idxs:
                in_group = True
                break
        if in_group:
            r = find(mi)
            if r not in root_to_gid:
                root_to_gid[r] = gid_counter[0]
                gid_counter[0] += 1
            mail_to_gid[mi] = root_to_gid[r]

    # mark parts
    for mi, m in enumerate(mails):
        gid = mail_to_gid.get(mi)
        m[u"dup_group"] = gid
        for p in m[u"parts"]:
            h = p[u"hash"]
            if h and h in cross_hashes and mi in cross_hashes[h]:
                p[u"is_dup"] = True
                p[u"dup_group"] = gid

    # build group stats
    gid_to_mails = defaultdict(list)
    for mi, gid in mail_to_gid.items():
        gid_to_mails[gid].append(mi)

    dup_groups = {}
    for gid, idxs in gid_to_mails.items():
        total_sz = sum(mails[i][u"total_size"] for i in idxs)
        dup_groups[unicode(gid)] = {
            u"count": len(idxs),
            u"total_size": total_sz,
            u"total_size_h": human_size(total_sz),
            u"mail_ids": idxs,
        }

    # mark first_in_group
    for gid, idxs in gid_to_mails.items():
        # sort by date, earliest is "first"
        sorted_idxs = sorted(idxs, key=lambda i: mails[i][u"date"])
        for rank, mi in enumerate(sorted_idxs):
            mails[mi][u"dup_rank"] = rank  # 0 = oldest (keep candidate)
        for mi in idxs:
            mails[mi][u"dup_count"] = len(idxs)

    return mails, dup_groups

# ── HTML generation ────────────────────────────────────────────────────────

DUP_COLORS = [
    "#fb4934", "#fabd2f", "#83a598", "#d3869b", "#8ec07c",
    "#fe8019", "#458588", "#b16286", "#689d6a", "#d65d0e",
    "#cc241d", "#d79921", "#458588", "#b16286", "#98971a",
]

def build_html(mails, dup_groups, root_path):
    generated  = datetime.now().strftime("%Y-%m-%d %H:%M")
    total_size = sum(m[u"total_size"] for m in mails)
    dup_mail_count = sum(1 for m in mails if m.get(u"dup_group") is not None)

    # assign IDs to mails
    for i, m in enumerate(mails):
        m[u"id"] = i

    # slim data for JS — include filepath for export
    js_mails = []
    for m in mails:
        js_mails.append({
            u"id":         m[u"id"],
            u"folder":     m[u"folder"],
            u"date":       m[u"date"],
            u"date_day":   m[u"date_day"],
            u"from":       m[u"from"],
            u"to":         m[u"to"],
            u"subject":    m[u"subject"],
            u"total_size": m[u"total_size"],
            u"filepath":   m[u"filepath"],
            u"dup_group":  m.get(u"dup_group"),
            u"dup_rank":   m.get(u"dup_rank", 0),
            u"dup_count":  m.get(u"dup_count", 1),
            u"parts": [{
                u"fname":    p[u"fname"],
                u"mime":     p[u"mime"],
                u"category": p[u"category"],
                u"size":     p[u"size"],
                u"is_dup":   p[u"is_dup"],
            } for p in m[u"parts"]],
        })

    js_data = json.dumps({
        u"mailbox":       to_u(root_path),
        u"generated":     generated,
        u"total_mails":   len(mails),
        u"total_size":    total_size,
        u"dup_mail_count": dup_mail_count,
        u"dup_groups":    dup_groups,
        u"mails":         js_mails,
    }, ensure_ascii=True)

    dup_colors_js = json.dumps(DUP_COLORS)

    return HTML_TEMPLATE % {
        "js_data":      js_data,
        "dup_colors":   dup_colors_js,
        "mailbox":      to_u(root_path).replace('"', '&quot;'),
        "generated":    generated,
        "total_mails":  len(mails),
        "total_size_h": human_size(total_size),
        "dup_count":    dup_mail_count,
    }

# ── HTML template ──────────────────────────────────────────────────────────

HTML_TEMPLATE = u"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Mail Review</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet"/>
<style>
:root{
  --bg:#1d2021;--bg1:#282828;--bg2:#3c3836;--bg3:#504945;
  --fg:#ebdbb2;--fg2:#a89984;--fg3:#7c6f64;
  --red:#fb4934;--red-d:#cc241d;
  --grn:#b8bb26;--grn-d:#98971a;
  --ylw:#fabd2f;--ylw-d:#d79921;
  --blu:#83a598;--blu-d:#458588;
  --pur:#d3869b;--org:#fe8019;--aqua:#8ec07c;
  --mono:'JetBrains Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--fg);font-family:var(--mono);font-size:12px;line-height:1.5;min-height:100vh}

/* topbar */
.topbar{
  position:sticky;top:0;z-index:200;
  background:var(--bg1);border-bottom:2px solid var(--bg3);
  display:flex;align-items:center;gap:1.5rem;
  padding:.6rem 1.25rem;flex-wrap:wrap;
}
.topbar-title{font-size:.9rem;font-weight:700;color:var(--ylw);white-space:nowrap}
.topbar-sub{font-size:.7rem;color:var(--fg3);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stat-chip{
  background:var(--bg2);border:1px solid var(--bg3);border-radius:3px;
  padding:.2rem .6rem;font-size:.7rem;white-space:nowrap;
}
.stat-chip span{font-weight:700}
.stat-chip.danger span{color:var(--red)}
.stat-chip.warn   span{color:var(--ylw)}
.stat-chip.ok     span{color:var(--grn)}
.stat-chip.info   span{color:var(--blu)}

/* space freed counter */
.freed-bar{
  background:var(--bg2);border:1px solid var(--bg3);border-radius:3px;
  padding:.2rem .75rem;font-size:.7rem;white-space:nowrap;
  display:flex;align-items:center;gap:.5rem;
}
.freed-val{font-weight:700;color:var(--grn);font-size:.85rem;min-width:60px}
.freed-label{color:var(--fg3)}

/* filter bar */
.filterbar{
  background:var(--bg1);border-bottom:1px solid var(--bg2);
  padding:.6rem 1.25rem;display:flex;gap:.75rem;align-items:center;flex-wrap:wrap;
}
.filterbar label{color:var(--fg2);font-size:.7rem;display:flex;align-items:center;gap:.35rem}
.filterbar select,.filterbar input[type=text],.filterbar input[type=number]{
  background:var(--bg2);color:var(--fg);border:1px solid var(--bg3);
  border-radius:3px;padding:.25rem .5rem;font:inherit;font-size:.72rem;
}
.filterbar select:focus,.filterbar input:focus{outline:none;border-color:var(--ylw)}
.filterbar input[type=checkbox]{accent-color:var(--ylw);width:13px;height:13px;cursor:pointer}
.filter-sep{width:1px;background:var(--bg3);height:20px;flex-shrink:0}

/* action bar */
.actionbar{
  background:var(--bg2);border-bottom:1px solid var(--bg3);
  padding:.5rem 1.25rem;display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;
}
.actionbar-label{font-size:.7rem;color:var(--fg3);margin-right:.25rem}
.btn{
  font-family:var(--mono);font-size:.68rem;font-weight:700;
  padding:.3rem .75rem;border-radius:3px;border:1px solid;
  cursor:pointer;background:transparent;letter-spacing:.04em;
  text-transform:uppercase;transition:all .12s;
}
.btn-del{color:var(--red);border-color:var(--red-d)}
.btn-del:hover,.btn-del.active{background:var(--red);color:#1d2021;border-color:var(--red)}
.btn-keep{color:var(--grn);border-color:var(--grn-d)}
.btn-keep:hover,.btn-keep.active{background:var(--grn);color:#1d2021;border-color:var(--grn)}
.btn-flag{color:var(--ylw);border-color:var(--ylw-d)}
.btn-flag:hover,.btn-flag.active{background:var(--ylw);color:#1d2021;border-color:var(--ylw)}
.btn-neutral{color:var(--fg2);border-color:var(--bg3)}
.btn-neutral:hover{border-color:var(--fg3);color:var(--fg)}
.btn-export{color:var(--ylw);border-color:var(--ylw);padding:.35rem 1rem}
.btn-export:hover{background:var(--ylw);color:#1d2021}
.spacer{flex:1}

/* table */
.tbl-wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;table-layout:fixed}
colgroup col.c-check{width:34px}
colgroup col.c-dup{width:90px}
colgroup col.c-folder{width:110px}
colgroup col.c-date{width:105px}
colgroup col.c-from{width:180px}
colgroup col.c-subject{width:auto}
colgroup col.c-parts{width:55px}
colgroup col.c-size{width:80px}
colgroup col.c-actions{width:175px}
thead tr{position:sticky;top:0;z-index:100}
th{
  background:var(--bg2);color:var(--ylw);
  padding:.45rem .6rem;text-align:left;
  border-bottom:2px solid var(--bg3);
  font-size:.68rem;letter-spacing:.07em;text-transform:uppercase;
  cursor:pointer;user-select:none;white-space:nowrap;
}
th:hover{color:var(--org)}
th .si{font-size:.6rem;color:var(--fg3)}
td{
  padding:.42rem .6rem;border-bottom:1px solid var(--bg2);
  vertical-align:middle;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
tr.mail-row:hover td{background:rgba(255,255,255,.025)}
tr.mail-row.selected-del td{background:rgba(251,73,52,.08)}
tr.mail-row.selected-del td.td-subject{color:var(--red)}
tr.mail-row.selected-flag td{background:rgba(250,189,47,.06)}
tr.mail-row.selected-keep td.td-subject{color:var(--grn);opacity:.7}
tr.hidden{display:none}

/* checkbox */
.cb-wrap{display:flex;align-items:center;justify-content:center}
input[type=checkbox]{accent-color:var(--red);width:13px;height:13px;cursor:pointer}

/* dup badge */
.dup-badge{
  display:inline-flex;align-items:center;gap:3px;
  border-radius:3px;padding:1px 5px;font-size:.62rem;font-weight:700;
  cursor:pointer;border:1px solid;
  white-space:nowrap;
}
.dup-badge:hover{filter:brightness(1.2)}
.dup-rank-0::before{content:"★ ";font-size:.55rem}

/* folder tag */
.folder-tag{
  background:var(--bg2);color:var(--blu);
  border-radius:3px;padding:1px 5px;font-size:.65rem;
}

/* size */
.td-size{text-align:right;font-variant-numeric:tabular-nums}
.sz-big{color:var(--red);font-weight:700}
.sz-med{color:var(--ylw)}
.sz-ok{color:var(--grn)}

/* date */
.td-date{color:var(--fg2);white-space:nowrap}

/* from */
.td-from{color:var(--fg3);font-size:.7rem}

/* subject */
.td-subject{font-size:.78rem}
.expand-btn{
  background:none;border:none;color:var(--fg3);cursor:pointer;
  font-family:var(--mono);font-size:.75rem;margin-right:.35rem;
  padding:0 2px;transition:transform .15s;
}
.expand-btn.open{transform:rotate(90deg)}

/* decision buttons in row */
.row-actions{display:flex;gap:4px;align-items:center}
.rb{
  font-family:var(--mono);font-size:.6rem;font-weight:700;
  padding:.2rem .45rem;border-radius:2px;border:1px solid;
  cursor:pointer;background:transparent;text-transform:uppercase;
  letter-spacing:.04em;transition:all .1s;white-space:nowrap;
}
.rb-del{color:var(--red);border-color:var(--red-d)}
.rb-del:hover,.rb-del.active{background:var(--red);color:#1d2021;border-color:var(--red)}
.rb-keep{color:var(--grn);border-color:var(--grn-d)}
.rb-keep:hover,.rb-keep.active{background:var(--grn);color:#1d2021;border-color:var(--grn)}
.rb-flag{color:var(--ylw);border-color:var(--ylw-d)}
.rb-flag:hover,.rb-flag.active{background:var(--ylw);color:#1d2021;border-color:var(--ylw)}

/* parts row */
tr.parts-row td{
  background:var(--bg1);padding:0;
  border-bottom:1px solid var(--bg2);
}
.parts-inner{
  padding:.6rem 1rem .6rem 2.5rem;
  display:none;
}
.parts-inner.open{display:block}
.parts-list{display:flex;flex-wrap:wrap;gap:.5rem}
.part-chip{
  display:inline-flex;align-items:center;gap:.35rem;
  background:var(--bg2);border:1px solid var(--bg3);
  border-radius:3px;padding:.2rem .55rem;font-size:.65rem;
}
.part-chip.is-dup{border-color:var(--pur);background:rgba(211,134,155,.1)}
.part-cat{font-weight:700}
.part-cat-pdf{color:var(--red)}
.part-cat-image{color:var(--blu)}
.part-cat-word{color:var(--ylw)}
.part-cat-excel{color:var(--grn)}
.part-cat-pptx{color:var(--org)}
.part-cat-calendar{color:var(--aqua)}
.part-cat-archive{color:var(--ylw)}
.part-cat-video,.part-cat-audio{color:var(--pur)}
.part-cat-signature,.part-cat-other{color:var(--fg3)}
.part-cat-text{color:var(--fg3)}
.part-size{color:var(--fg3)}
.dup-mark{color:var(--pur);font-size:.6rem}

/* footer */
.footer{
  position:sticky;bottom:0;z-index:200;
  background:var(--bg1);border-top:2px solid var(--bg3);
  padding:.6rem 1.25rem;
  display:flex;align-items:center;gap:1rem;flex-wrap:wrap;
}
.footer-stats{font-size:.72rem;color:var(--fg2)}
.footer-stats strong{color:var(--fg)}
.decision-summary{display:flex;gap:.75rem;font-size:.7rem}
.ds-del{color:var(--red)}
.ds-flag{color:var(--ylw)}
.ds-keep{color:var(--fg3)}

/* modal */
.modal-overlay{
  display:none;position:fixed;inset:0;z-index:500;
  background:rgba(0,0,0,.75);
}
.modal-overlay.open{display:flex;align-items:center;justify-content:center}
.modal{
  background:var(--bg1);border:1px solid var(--bg3);border-radius:6px;
  width:min(680px,94vw);max-height:85vh;display:flex;flex-direction:column;
  box-shadow:0 16px 48px rgba(0,0,0,.6);
}
.modal-header{
  padding:1rem 1.25rem;border-bottom:1px solid var(--bg2);
  display:flex;align-items:center;justify-content:space-between;
}
.modal-title{font-size:.9rem;font-weight:700;color:var(--ylw)}
.modal-close{background:none;border:none;color:var(--fg3);font-size:1.2rem;cursor:pointer;font-family:var(--mono)}
.modal-close:hover{color:var(--fg)}
.modal-body{padding:1.25rem;overflow-y:auto;flex:1}
.modal-footer{padding:.9rem 1.25rem;border-top:1px solid var(--bg2);display:flex;gap:.75rem;justify-content:flex-end}
pre.json-out{
  background:var(--bg);border:1px solid var(--bg3);border-radius:4px;
  padding:.75rem;font-size:.68rem;line-height:1.7;overflow-x:auto;
  white-space:pre-wrap;word-break:break-all;color:var(--fg2);
  max-height:400px;overflow-y:auto;
}
.notice{
  background:rgba(251,73,52,.08);border:1px solid var(--red-d);
  border-radius:4px;padding:.6rem .9rem;font-size:.75rem;color:var(--fg2);
  margin-bottom:1rem;line-height:1.6;
}
.notice strong{color:var(--red)}
</style>
</head>
<body>

<!-- Topbar -->
<div class="topbar">
  <div class="topbar-title">📬 Mail Review</div>
  <div class="topbar-sub" title="%(mailbox)s">%(mailbox)s</div>
  <div class="stat-chip info">Mails <span id="cnt-total">%(total_mails)d</span></div>
  <div class="stat-chip warn">Size <span>%(total_size_h)s</span></div>
  <div class="stat-chip danger">Dups <span id="cnt-dups">%(dup_count)d</span></div>
  <div class="freed-bar">
    <div class="freed-val" id="freed-val">0 B</div>
    <div class="freed-label">selected to delete</div>
  </div>
  <div style="font-size:.65rem;color:var(--fg3)">%(generated)s</div>
</div>

<!-- Filter bar -->
<div class="filterbar">
  <label>Search <input type="text" id="fSearch" placeholder="subject / from…" style="width:170px"/></label>
  <div class="filter-sep"></div>
  <label>Folder <select id="fFolder"><option value="">All</option></select></label>
  <label>Type <select id="fCat"><option value="">All</option></select></label>
  <label>Min size (KB) <input type="number" id="fMinSize" value="0" min="0" style="width:70px"/></label>
  <div class="filter-sep"></div>
  <label><input type="checkbox" id="fDupOnly"/> Dups only</label>
  <div class="filter-sep"></div>
  <label>Show
    <select id="fDecision">
      <option value="">All</option>
      <option value="delete">Delete</option>
      <option value="flag">Flagged</option>
      <option value="keep">Keep</option>
      <option value="undecided">Undecided</option>
    </select>
  </label>
  <div class="filter-sep"></div>
  <button class="btn btn-neutral" onclick="resetFilters()">Reset</button>
  <div style="flex:1"></div>
  <div style="font-size:.7rem;color:var(--fg3)" id="visible-count"></div>
</div>

<!-- Action bar -->
<div class="actionbar">
  <span class="actionbar-label">Bulk:</span>
  <button class="btn btn-del" onclick="bulkDupDelete()">🗑 Delete all dups</button>
  <button class="btn btn-keep" onclick="bulkDupKeep()">✓ Keep oldest of each dup group</button>
  <div class="filter-sep" style="margin:0 .25rem"></div>
  <button class="btn btn-del" onclick="bulkVisibleDelete()">Delete visible</button>
  <button class="btn btn-neutral" onclick="bulkClear()">Clear all decisions</button>
  <div class="spacer"></div>
  <button class="btn btn-export" onclick="openExport()">⬇ Export JSON</button>
</div>

<!-- Table -->
<div class="tbl-wrap">
<table>
<colgroup>
  <col class="c-check"/><col class="c-dup"/><col class="c-folder"/>
  <col class="c-date"/><col class="c-from"/><col class="c-subject"/>
  <col class="c-parts"/><col class="c-size"/><col class="c-actions"/>
</colgroup>
<thead>
<tr>
  <th style="cursor:default"><input type="checkbox" id="selectAll" title="Select all visible"/></th>
  <th data-col="dup_group">Dup <span class="si">⇅</span></th>
  <th data-col="folder">Folder <span class="si">⇅</span></th>
  <th data-col="date">Date <span class="si">⇅</span></th>
  <th data-col="from">From <span class="si">⇅</span></th>
  <th data-col="subject">Subject <span class="si">⇅</span></th>
  <th data-col="parts" style="text-align:right">Parts <span class="si">⇅</span></th>
  <th data-col="total_size" style="text-align:right">Size <span class="si">⇅</span></th>
  <th style="cursor:default">Decision</th>
</tr>
</thead>
<tbody id="tbody"></tbody>
</table>
</div>

<!-- Footer -->
<div class="footer">
  <div class="footer-stats" id="footer-stats"></div>
  <div class="spacer"></div>
  <div class="decision-summary">
    <span class="ds-del">🗑 Delete: <strong id="cnt-del">0</strong></span>
    <span class="ds-flag">⚑ Flag: <strong id="cnt-flag">0</strong></span>
    <span class="ds-keep">✓ Keep: <strong id="cnt-keep">0</strong></span>
  </div>
  <button class="btn btn-export" onclick="openExport()">⬇ Export JSON</button>
</div>

<!-- Export Modal -->
<div class="modal-overlay" id="exportModal">
  <div class="modal">
    <div class="modal-header">
      <div class="modal-title">📋 Export Decisions</div>
      <button class="modal-close" onclick="closeExport()">✕</button>
    </div>
    <div class="modal-body">
      <div class="notice" id="export-notice"></div>
      <pre class="json-out" id="json-preview"></pre>
    </div>
    <div class="modal-footer">
      <button class="btn btn-neutral" onclick="closeExport()">Close</button>
      <button class="btn btn-neutral" onclick="copyJSON()">Copy</button>
      <button class="btn btn-export" onclick="downloadJSON()">⬇ Download JSON</button>
    </div>
  </div>
</div>

<script>
// ── data ──────────────────────────────────────────────────────────────────
var DATA = %(js_data)s;
var DUP_COLORS = %(dup_colors)s;
var mails = DATA.mails;
var decisions = {}; // id -> "delete"|"flag"|"keep"|null

// ── helpers ───────────────────────────────────────────────────────────────
function hs(n){
  var u=['B','KB','MB','GB'];
  for(var i=0;i<u.length;i++){if(n<1024)return n.toFixed(1)+' '+u[i];n/=1024;}
  return n.toFixed(1)+' GB';
}
function esc(s){
  if(!s)return'';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function dupColor(gid){
  if(gid===null||gid===undefined)return null;
  return DUP_COLORS[gid %% DUP_COLORS.length];
}
function szClass(n){
  return n>10*1024*1024?'sz-big':n>2*1024*1024?'sz-med':'sz-ok';
}

// ── populate filters ──────────────────────────────────────────────────────
(function(){
  var folders={}, cats={};
  mails.forEach(function(m){
    folders[m.folder]=1;
    m.parts.forEach(function(p){cats[p.category]=1;});
  });
  var fFolder=document.getElementById('fFolder');
  Object.keys(folders).sort().forEach(function(f){
    var o=document.createElement('option');o.value=f;o.textContent=f;fFolder.appendChild(o);
  });
  var fCat=document.getElementById('fCat');
  Object.keys(cats).sort().forEach(function(c){
    var o=document.createElement('option');o.value=c;o.textContent=c;fCat.appendChild(o);
  });
})();

// ── render table ──────────────────────────────────────────────────────────
var sortCol='total_size', sortDir=-1;
var visibleIds=[];

function renderTable(){
  var tbody=document.getElementById('tbody');
  var fSearch=document.getElementById('fSearch').value.toLowerCase();
  var fFolder=document.getElementById('fFolder').value;
  var fCat=document.getElementById('fCat').value;
  var fMinSize=Number(document.getElementById('fMinSize').value)*1024;
  var fDupOnly=document.getElementById('fDupOnly').checked;
  var fDecision=document.getElementById('fDecision').value;

  // filter
  var filtered=mails.filter(function(m){
    if(fDupOnly && m.dup_group===null) return false;
    if(fFolder && m.folder!==fFolder) return false;
    if(m.total_size<fMinSize) return false;
    if(fDecision){
      var d=decisions[m.id]||null;
      if(fDecision==='undecided'&&d!==null)return false;
      if(fDecision!=='undecided'&&d!==fDecision)return false;
    }
    if(fSearch){
      var hay=(m.subject+' '+m.from+' '+m.folder).toLowerCase();
      if(hay.indexOf(fSearch)===-1)return false;
    }
    if(fCat){
      var hasCat=m.parts.some(function(p){return p.category===fCat;});
      if(!hasCat)return false;
    }
    return true;
  });

  // sort
  filtered.sort(function(a,b){
    var va=sortCol==='dup_group'?(a.dup_group===null?999:a.dup_group):a[sortCol];
    var vb=sortCol==='dup_group'?(b.dup_group===null?999:b.dup_group):b[sortCol];
    if(va===null||va===undefined)va=sortDir>0?'':'\uFFFF';
    if(vb===null||vb===undefined)vb=sortDir>0?'':'\uFFFF';
    if(va<vb)return sortDir;
    if(va>vb)return -sortDir;
    return 0;
  });

  visibleIds=filtered.map(function(m){return m.id;});
  document.getElementById('visible-count').textContent=filtered.length+' / '+mails.length+' mails';

  // render rows
  var html='';
  filtered.forEach(function(m){
    var d=decisions[m.id]||null;
    var rowCls='mail-row'+(d?' selected-'+d:'');
    var dc=dupColor(m.dup_group);
    var dupStyle=dc?'border-left:3px solid '+dc+';':'border-left:3px solid transparent;';
    var dupCell='';
    if(m.dup_group!==null){
      var rankCls=m.dup_rank===0?' dup-rank-0':'';
      dupCell='<span class="dup-badge'+rankCls+'" style="color:'+dc+';border-color:'+dc+';background:'+dc+'22" '
        +'onclick="selectDupGroup('+m.dup_group+')" title="Click to highlight group">'
        +'G'+(m.dup_group+1)+' ('+m.dup_count+')'
        +(m.dup_rank===0?' ★':'')
        +'</span>';
    }
    var partsSummary='';
    if(m.parts.length>0){
      var hasDupPart=m.parts.some(function(p){return p.is_dup;});
      partsSummary=(hasDupPart?'<span style="color:var(--pur)" title="Has duplicate attachments">⚑ </span>':'')+m.parts.length;
    } else {
      partsSummary='<span style="color:var(--fg3)">—</span>';
    }

    html+='<tr class="'+rowCls+'" data-id="'+m.id+'" style="'+dupStyle+'" id="row-'+m.id+'">'
      +'<td class="cb-wrap"><input type="checkbox" class="row-cb" data-id="'+m.id+'"/></td>'
      +'<td>'+dupCell+'</td>'
      +'<td><span class="folder-tag">'+esc(m.folder)+'</span></td>'
      +'<td class="td-date">'+esc(m.date)+'</td>'
      +'<td class="td-from" title="'+esc(m.from)+'">'+esc((m.from||'').substring(0,30))+'</td>'
      +'<td class="td-subject">'
        +'<button class="expand-btn" onclick="toggleParts('+m.id+',this)" title="Show attachments">▶</button>'
        +esc((m.subject||'').substring(0,70))
      +'</td>'
      +'<td style="text-align:right">'+partsSummary+'</td>'
      +'<td class="td-size '+szClass(m.total_size)+'">'+hs(m.total_size)+'</td>'
      +'<td><div class="row-actions">'
        +'<button class="rb rb-del'+(d==='delete'?' active':'')+'" onclick="setDecision('+m.id+',\'delete\',this)">Del</button>'
        +'<button class="rb rb-keep'+(d==='keep'?' active':'')+'" onclick="setDecision('+m.id+',\'keep\',this)">Keep</button>'
        +'<button class="rb rb-flag'+(d==='flag'?' active':'')+'" onclick="setDecision('+m.id+',\'flag\',this)">Flag</button>'
      +'</div></td>'
      +'</tr>';

    // parts row (hidden)
    html+='<tr class="parts-row" id="parts-'+m.id+'">'
      +'<td colspan="9"><div class="parts-inner" id="pi-'+m.id+'">';
    if(m.parts.length>0){
      html+='<div class="parts-list">';
      m.parts.forEach(function(p){
        var pc='part-cat part-cat-'+p.category;
        var chipCls='part-chip'+(p.is_dup?' is-dup':'');
        html+='<div class="'+chipCls+'">'
          +'<span class="'+pc+'">'+esc(p.category)+'</span>'
          +(p.fname?'<span>'+esc(p.fname.substring(0,40))+'</span>':'')
          +'<span class="part-size">'+hs(p.size)+'</span>'
          +(p.is_dup?'<span class="dup-mark">DUP</span>':'')
          +'</div>';
      });
      html+='</div>';
    } else {
      html+='<span style="color:var(--fg3);font-size:.7rem">No attachments / text only</span>';
    }
    html+='</div></td></tr>';
  });

  tbody.innerHTML=html;
  updateStats();
}

function toggleParts(id, btn){
  var el=document.getElementById('pi-'+id);
  if(el){
    el.classList.toggle('open');
    btn.classList.toggle('open');
  }
}

// ── sorting ───────────────────────────────────────────────────────────────
document.querySelectorAll('th[data-col]').forEach(function(th){
  th.addEventListener('click',function(){
    var col=th.dataset.col;
    if(sortCol===col)sortDir*=-1;else{sortCol=col;sortDir=-1;}
    document.querySelectorAll('th .si').forEach(function(si){si.textContent='⇅';});
    th.querySelector('.si').textContent=sortDir>0?'▲':'▼';
    renderTable();
  });
});

// ── decisions ─────────────────────────────────────────────────────────────
function setDecision(id, dec, btn){
  var current=decisions[id]||null;
  if(current===dec){
    decisions[id]=null;
  } else {
    decisions[id]=dec;
  }
  renderTable();
}

function updateStats(){
  var delCount=0, flagCount=0, keepCount=0, delSize=0, undecided=0;
  mails.forEach(function(m){
    var d=decisions[m.id]||null;
    if(d==='delete'){delCount++;delSize+=m.total_size;}
    else if(d==='flag'){flagCount++;}
    else if(d==='keep'){keepCount++;}
    else undecided++;
  });
  document.getElementById('cnt-del').textContent=delCount;
  document.getElementById('cnt-flag').textContent=flagCount;
  document.getElementById('cnt-keep').textContent=keepCount;
  document.getElementById('freed-val').textContent=hs(delSize);
  document.getElementById('footer-stats').innerHTML=
    '<strong>'+visibleIds.length+'</strong> visible &nbsp;|&nbsp; '
    +'<strong>'+(delCount+flagCount+keepCount)+'</strong> decided &nbsp;|&nbsp; '
    +'<strong>'+undecided+'</strong> undecided';
  document.getElementById('cnt-dups').textContent=
    Object.keys(DATA.dup_groups).reduce(function(s,g){return s+DATA.dup_groups[g].count;},0);
}

// ── bulk actions ──────────────────────────────────────────────────────────
function bulkDupDelete(){
  // mark all non-oldest duplicates for deletion
  mails.forEach(function(m){
    if(m.dup_group!==null && m.dup_rank>0){
      decisions[m.id]='delete';
    }
  });
  renderTable();
}

function bulkDupKeep(){
  // keep oldest of each dup group, delete rest
  mails.forEach(function(m){
    if(m.dup_group!==null){
      decisions[m.id]=m.dup_rank===0?'keep':'delete';
    }
  });
  renderTable();
}

function bulkVisibleDelete(){
  visibleIds.forEach(function(id){decisions[id]='delete';});
  renderTable();
}

function bulkClear(){
  decisions={};
  renderTable();
}

function selectDupGroup(gid){
  // scroll to first mail in this group
  var first=mails.find(function(m){return m.dup_group===gid&&m.dup_rank===0;});
  if(first){
    var row=document.getElementById('row-'+first.id);
    if(row)row.scrollIntoView({behavior:'smooth',block:'center'});
  }
  // flash highlight group rows
  mails.forEach(function(m){
    if(m.dup_group===gid){
      var row=document.getElementById('row-'+m.id);
      if(row){
        row.style.outline='2px solid '+dupColor(gid);
        setTimeout(function(){row.style.outline='';},1500);
      }
    }
  });
}

// select all checkbox
document.getElementById('selectAll').addEventListener('change',function(){
  var checked=this.checked;
  visibleIds.forEach(function(id){decisions[id]=checked?'delete':null;});
  renderTable();
});

// ── filters ───────────────────────────────────────────────────────────────
['fSearch','fFolder','fCat','fMinSize','fDecision'].forEach(function(id){
  var el=document.getElementById(id);
  el.addEventListener(el.tagName==='SELECT'?'change':'input',renderTable);
});
document.getElementById('fDupOnly').addEventListener('change',renderTable);

function resetFilters(){
  document.getElementById('fSearch').value='';
  document.getElementById('fFolder').value='';
  document.getElementById('fCat').value='';
  document.getElementById('fMinSize').value='0';
  document.getElementById('fDupOnly').checked=false;
  document.getElementById('fDecision').value='';
  renderTable();
}

// ── export ────────────────────────────────────────────────────────────────
var _jsonOut='';

function buildJSON(){
  var del_mails=[], flag_mails=[], keep_mails=[], undecided_mails=[];
  var del_size=0, flag_size=0;
  mails.forEach(function(m){
    var d=decisions[m.id]||null;
    var entry={
      folder:m.folder,date:m.date,from:m.from,subject:m.subject,
      total_size:m.total_size,filepath:m.filepath,
      dup_group:m.dup_group!==null?('G'+(m.dup_group+1)):null,
      dup_rank:m.dup_group!==null?m.dup_rank:null,
    };
    if(d==='delete'){del_mails.push(entry);del_size+=m.total_size;}
    else if(d==='flag'){flag_mails.push(entry);flag_size+=m.total_size;}
    else if(d==='keep'){keep_mails.push(entry);}
    else{undecided_mails.push(entry);}
  });
  return {
    meta:{
      mailbox:DATA.mailbox,
      generated:DATA.generated,
      reviewed_at:new Date().toISOString().replace('T',' ').substring(0,16),
    },
    summary:{
      total_mails:mails.length,
      delete_count:del_mails.length,
      delete_size_bytes:del_size,
      delete_size_human:hs(del_size),
      flag_count:flag_mails.length,
      flag_size_human:hs(flag_size),
      undecided_count:undecided_mails.length,
    },
    decisions:{
      delete:del_mails,
      flag:flag_mails,
      keep:keep_mails,
      undecided:undecided_mails,
    }
  };
}

function openExport(){
  var out=buildJSON();
  _jsonOut=JSON.stringify(out,null,2);
  var notice=document.getElementById('export-notice');
  var del=out.decisions.delete.length;
  var undet=out.summary.undecided_count;
  var txt='<strong>'+del+' mails</strong> marked for deletion ('+out.summary.delete_size_human+')';
  if(undet>0)txt+=' — <strong style="color:var(--ylw)">'+undet+' still undecided</strong>';
  txt+='. Send this JSON to your administrator to action the deletions.';
  notice.innerHTML=txt;
  document.getElementById('json-preview').textContent=_jsonOut.substring(0,8000)+((_jsonOut.length>8000)?'\n\n... (truncated in preview, full file on download)':'');
  document.getElementById('exportModal').classList.add('open');
}

function closeExport(){
  document.getElementById('exportModal').classList.remove('open');
}

function copyJSON(){
  if(navigator.clipboard){navigator.clipboard.writeText(_jsonOut);}
  else{var ta=document.createElement('textarea');ta.value=_jsonOut;document.body.appendChild(ta);ta.select();document.execCommand('copy');document.body.removeChild(ta);}
  alert('Copied to clipboard!');
}

function downloadJSON(){
  var blob=new Blob([_jsonOut],{type:'application/json'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  a.href=url;
  a.download='mail_decisions_'+new Date().toISOString().slice(0,10)+'.json';
  a.click();
  URL.revokeObjectURL(url);
}

// close modal on overlay click
document.getElementById('exportModal').addEventListener('click',function(e){
  if(e.target===this)closeExport();
});

// keyboard
document.addEventListener('keydown',function(e){
  if(e.key==='Escape')closeExport();
});

// ── init ──────────────────────────────────────────────────────────────────
renderTable();
</script>
</body>
</html>
"""

# ── main ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    root = os.path.realpath(sys.argv[1])
    if not os.path.exists(root):
        print("ERROR: path does not exist: %s" % root)
        sys.exit(1)

    print("\n maildir_viewer.py")
    print(" Scanning: %s\n" % root)

    mails = scan_maildir(root)
    if not mails:
        print("  No mails found.")
        sys.exit(0)

    print("  Building duplicate groups...")
    mails, dup_groups = assign_dup_groups(mails)
    dup_mail_count = sum(1 for m in mails if m.get(u"dup_group") is not None)
    print("  Found %d duplicate groups, %d affected mails." % (len(dup_groups), dup_mail_count))

    parent   = os.path.dirname(root)
    out_html = os.path.join(parent, "mail_viewer.html")
    out_json = os.path.join(parent, "mail_viewer.json")

    print("  Generating HTML...")
    html = build_html(mails, dup_groups, root)
    with codecs.open(out_html, "w", encoding="utf-8") as f:
        f.write(html)

    # slim JSON output
    slim = []
    for m in mails:
        slim.append({
            u"folder":     m[u"folder"],
            u"date":       m[u"date"],
            u"from":       m[u"from"],
            u"subject":    m[u"subject"],
            u"total_size": m[u"total_size"],
            u"filepath":   m[u"filepath"],
            u"dup_group":  m.get(u"dup_group"),
            u"dup_rank":   m.get(u"dup_rank", 0),
            u"dup_count":  m.get(u"dup_count", 1),
            u"parts": [{
                u"fname":    p[u"fname"],
                u"mime":     p[u"mime"],
                u"category": p[u"category"],
                u"size":     p[u"size"],
                u"is_dup":   p[u"is_dup"],
            } for p in m[u"parts"]],
        })
    with codecs.open(out_json, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=True, indent=2)

    total_size = sum(m[u"total_size"] for m in mails)
    print("\n Done.")
    print("   Mails      : %d" % len(mails))
    print("   Total size : %s" % human_size(total_size))
    print("   Dup groups : %d (%d mails affected)" % (len(dup_groups), dup_mail_count))
    print("   HTML       : %s" % out_html)
    print("   JSON       : %s" % out_json)
    print("")
    print("  Deploy with:")
    print("    scp mrija_org@s16.thehost.com.ua:%s ./mail_viewer.html" % out_html)
    print("    deploy-report ./mail_viewer.html")

if __name__ == "__main__":
    main()
