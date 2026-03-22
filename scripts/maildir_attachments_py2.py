#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
maildir_attachments_py2.py  --  Python 2.7 compatible
Scan a Maildir tree, extract attachment metadata, generate HTML report.

Usage:
    python maildir_attachments_py2.py /email/mrija.org/andrii.karioti/.maildir
"""

from __future__ import print_function
import os, sys, email, hashlib, json, codecs
from email.header import decode_header, make_header
from datetime import datetime

# \u2500\u2500 helpers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def decode_str(s):
    if s is None:
        return u""
    try:
        return unicode(make_header(decode_header(s)))
    except Exception:
        try:
            return s.decode("utf-8", errors="replace")
        except Exception:
            return unicode(s)

def parse_date(date_str):
    if not date_str:
        return u""
    from email.utils import parsedate
    try:
        t = parsedate(date_str)
        if t:
            dt = datetime(*t[:6])
            return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return date_str[:30] if date_str else u""

def human_size(n):
    for unit in (u"B", u"KB", u"MB", u"GB"):
        if n < 1024:
            return u"%.1f %s" % (n, unit)
        n /= 1024.0
    return u"%.1f GB" % n

def to_unicode(s):
    if isinstance(s, unicode):
        return s
    if isinstance(s, str):
        return s.decode("utf-8", errors="replace")
    return unicode(s)

def esc(s):
    """HTML-escape, always returns unicode."""
    s = to_unicode(s)
    return s.replace(u"&", u"&amp;").replace(u"<", u"&lt;").replace(u">", u"&gt;").replace(u'"', u"&quot;")

SKIP_MIME = {
    "text/plain", "text/html", "multipart/mixed",
    "multipart/alternative", "multipart/related",
    "multipart/signed", "message/rfc822",
}

# \u2500\u2500 extraction \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def extract_attachments(filepath, mailbox_name):
    attachments = []
    try:
        with open(filepath, "rb") as f:
            msg = email.message_from_file(f)
    except Exception:
        return []

    date_fmt = parse_date(msg.get("Date"))
    subject  = decode_str(msg.get("Subject", "(no subject)"))
    sender   = decode_str(msg.get("From", ""))

    for part in msg.walk():
        ct   = part.get_content_type().lower()
        disp = part.get("Content-Disposition") or ""

        if ct in SKIP_MIME and disp not in ("attachment", "inline"):
            continue

        fname = part.get_filename()
        if fname:
            fname = decode_str(fname)
        else:
            if ct in SKIP_MIME:
                continue
            fname = u"[unnamed %s]" % ct

        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        size_bytes = len(payload)
        if size_bytes < 512:
            continue

        content_hash = hashlib.md5(payload).hexdigest()

        attachments.append({
            "mailbox":      to_unicode(mailbox_name),
            "date_fmt":     to_unicode(date_fmt),
            "from":         to_unicode(sender),
            "subject":      to_unicode(subject),
            "filename":     to_unicode(fname),
            "mime":         to_unicode(ct),
            "size_bytes":   size_bytes,
            "size_human":   human_size(size_bytes),
            "hash":         content_hash,
            "duplicate_of": u"",
        })

    return attachments

def scan_maildir(root):
    all_attachments = []
    total_files = 0

    # collect mailbox dirs \u2014 walk everything including dot-dirs
    for dirpath, dirs, files in os.walk(root):
        # include hidden dirs like .Sent .Trash etc
        dirs[:] = [d for d in dirs if d != "tmp"]
        # only process cur/new dirs (actual mail storage)
        base = os.path.basename(dirpath)
        if base not in ("cur", "new"):
            continue

        # mailbox name = part between root and cur/new
        rel = os.path.relpath(dirpath, root)
        parts = rel.replace("\\", "/").split("/")
        mailbox_name = parts[0] if parts[0] != "cur" else os.path.basename(root)

        for fname in files:
            if fname.startswith("."):
                continue
            fpath = os.path.join(dirpath, fname)
            total_files += 1
            atts = extract_attachments(fpath, mailbox_name)
            all_attachments.extend(atts)

    print("  Scanned %d mail files, found %d attachments." % (total_files, len(all_attachments)))
    return all_attachments

def dedup(attachments):
    seen = {}
    for a in attachments:
        h = a["hash"]
        if h in seen:
            a["duplicate_of"] = seen[h]
        else:
            seen[h] = a["filename"]
    return attachments

# \u2500\u2500 HTML \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

HTML_HEAD = u"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Mailbox Anh\u00e4nge</title>
<style>
  :root{--bg:#1d2021;--bg2:#282828;--bg3:#3c3836;--fg:#ebdbb2;--fg2:#a89984;--fg3:#7c6f64;--red:#fb4934;--grn:#b8bb26;--ylw:#fabd2f;--blu:#83a598;--pur:#d3869b;--org:#fe8019;--border:#504945}
  *{box-sizing:border-box;margin:0;padding:0}
  body{background:var(--bg);color:var(--fg);font:13px/1.5 'JetBrains Mono','Fira Mono',monospace;padding:2rem}
  h1{font-size:1.3rem;color:var(--ylw);margin-bottom:.25rem}
  .sub{color:var(--fg2);font-size:.8rem;margin-bottom:1.5rem}
  .stats{display:flex;gap:1.5rem;margin-bottom:1.5rem;flex-wrap:wrap}
  .stat{background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:.6rem 1rem}
  .stat-val{font-size:1.3rem;color:var(--org);font-weight:bold}
  .stat-lbl{font-size:.7rem;color:var(--fg3)}
  .controls{display:flex;gap:1rem;margin-bottom:1rem;flex-wrap:wrap;align-items:center}
  select,input{background:var(--bg2);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:.35rem .6rem;font:inherit}
  label{color:var(--fg2);font-size:.8rem}
  .btn{background:var(--bg3);color:var(--fg);border:1px solid var(--border);border-radius:4px;padding:.35rem .9rem;cursor:pointer;font:inherit}
  .btn:hover{border-color:var(--ylw);color:var(--ylw)}
  table{width:100%;border-collapse:collapse;font-size:.8rem}
  th{background:var(--bg3);color:var(--ylw);text-align:left;padding:.45rem .7rem;border-bottom:2px solid var(--border);cursor:pointer;user-select:none;white-space:nowrap}
  th:hover{color:var(--org)}
  td{padding:.4rem .7rem;border-bottom:1px solid var(--border);vertical-align:top;max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  tr:hover td{background:var(--bg2)}
  tr.dup td{opacity:.5}
  tr.hidden{display:none}
  .mb-tag{background:var(--bg3);color:var(--blu);border-radius:3px;padding:1px 5px;font-size:.72rem}
  .sz{color:var(--grn);text-align:right;font-variant-numeric:tabular-nums}
  .sz.big{color:var(--red);font-weight:bold}
  .date{color:var(--fg2);white-space:nowrap}
  .dup-badge{color:var(--pur);font-size:.68rem}
  .mc{color:var(--fg3);font-size:.72rem}
  #summary{margin-top:1rem;color:var(--fg2);font-size:.78rem}
</style>
</head><body>
"""

HTML_FOOT = u"""
<script>
var rows=Array.from(document.querySelectorAll('tbody tr[data-row]'));
var tbody=document.querySelector('tbody');
var sortCol='size_bytes',sortDir=-1;
function val(row,col){var el=row.querySelector('[data-col="'+col+'"]');if(!el)return'';var r=el.dataset.raw||el.textContent;return isNaN(r)?r.toLowerCase():Number(r);}
function sortTable(col){
  if(sortCol===col)sortDir*=-1;else{sortCol=col;sortDir=-1;}
  rows.sort(function(a,b){var va=val(a,col),vb=val(b,col);return va<vb?sortDir:va>vb?-sortDir:0;});
  rows.forEach(function(r){tbody.appendChild(r);});
  document.querySelectorAll('th').forEach(function(th){var ic=th.querySelector('.si');if(ic)ic.textContent=th.dataset.col===col?(sortDir>0?'\u25b2':'\u25bc'):'\u21c5';});
  updateSummary();
}
function applyFilters(){
  var mb=document.getElementById('fMb').value.toLowerCase();
  var minSz=Number(document.getElementById('fMinSize').value)*1024;
  var hideDup=document.getElementById('fDup').checked;
  var search=document.getElementById('fSearch').value.toLowerCase();
  rows.forEach(function(r){
    var isDup=r.classList.contains('dup');
    var sz=Number(r.querySelector('[data-col="size_bytes"]').dataset.raw);
    var mbv=r.querySelector('[data-col="mailbox"]').textContent.toLowerCase();
    var fn=r.querySelector('[data-col="filename"]').textContent.toLowerCase();
    var sub=r.querySelector('[data-col="subject"]').textContent.toLowerCase();
    var show=(!mb||mbv.includes(mb))&&sz>=minSz&&!(hideDup&&isDup)&&(!search||fn.includes(search)||sub.includes(search));
    r.classList.toggle('hidden',!show);
  });
  updateSummary();
}
function humanSize(n){var u=['B','KB','MB','GB'];for(var i=0;i<u.length;i++){if(n<1024)return n.toFixed(1)+' '+u[i];n/=1024;}return n.toFixed(1)+' GB';}
function updateSummary(){
  var visible=rows.filter(function(r){return!r.classList.contains('hidden');});
  var tot=visible.reduce(function(s,r){return s+Number(r.querySelector('[data-col="size_bytes"]').dataset.raw);},0);
  document.getElementById('summary').textContent=visible.length+' von '+rows.length+' Anh\u00e4ngen \u2014 Gesamt: '+humanSize(tot);
}
document.querySelectorAll('th[data-col]').forEach(function(th){th.addEventListener('click',function(){sortTable(th.dataset.col);});});
document.getElementById('fMb').addEventListener('change',applyFilters);
document.getElementById('fMinSize').addEventListener('input',applyFilters);
document.getElementById('fDup').addEventListener('change',applyFilters);
document.getElementById('fSearch').addEventListener('input',applyFilters);
document.getElementById('btnReset').addEventListener('click',function(){
  document.getElementById('fMb').value='';
  document.getElementById('fMinSize').value='0';
  document.getElementById('fDup').checked=false;
  document.getElementById('fSearch').value='';
  applyFilters();
});
sortTable('size_bytes');
updateSummary();
</script></body></html>
"""

def build_html(attachments, root_path):
    total_size = sum(a["size_bytes"] for a in attachments)
    unique     = sum(1 for a in attachments if not a["duplicate_of"])
    dups       = len(attachments) - unique
    mailboxes  = sorted(set(a["mailbox"] for a in attachments))
    generated  = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [HTML_HEAD]
    lines.append(u"<h1>\u1f4ce Anh\u00e4nge \u2014 %s</h1>" % esc(unicode(root_path)))
    lines.append(u'<div class="sub">Generiert: %s</div>' % generated)

    lines.append(u'<div class="stats">')
    for val, lbl in [
        (human_size(total_size), u"Gesamtgr\u00f6\u00dfe"),
        (unicode(len(attachments)), u"Anh\u00e4nge total"),
        (unicode(unique),           u"Eindeutig"),
        (unicode(dups),             u"Duplikate"),
        (unicode(len(mailboxes)),   u"Ordner"),
    ]:
        lines.append(u'<div class="stat"><div class="stat-val">%s</div><div class="stat-lbl">%s</div></div>' % (val, lbl))
    lines.append(u'</div>')

    mb_opts = u"".join(u'<option value="%s">%s</option>' % (esc(m), esc(m)) for m in mailboxes)
    lines.append(u"""
<div class="controls">
  <label>Ordner: <select id="fMb"><option value="">Alle</option>%s</select></label>
  <label>Min. Gr\u00f6\u00dfe (KB): <input id="fMinSize" type="number" value="0" min="0" style="width:80px"/></label>
  <label><input id="fDup" type="checkbox"/> Duplikate ausblenden</label>
  <label>Suche: <input id="fSearch" type="text" placeholder="Dateiname / Betreff" style="width:180px"/></label>
  <button class="btn" id="btnReset">Reset</button>
</div>""" % mb_opts)

    lines.append(u"<table><thead><tr>")
    cols = [
        (u"mailbox",    u"Ordner"),
        (u"date_fmt",   u"Datum"),
        (u"from",       u"Von"),
        (u"subject",    u"Betreff"),
        (u"filename",   u"Dateiname"),
        (u"mime",       u"MIME"),
        (u"size_bytes", u"Gr\u00f6\u00dfe"),
    ]
    for key, label in cols:
        lines.append(u'<th data-col="%s">%s <span class="si">\u21c5</span></th>' % (key, label))
    lines.append(u"</tr></thead><tbody>")

    for a in attachments:
        dup_cls  = u"dup" if a["duplicate_of"] else u""
        sz_cls   = u"sz big" if a["size_bytes"] > 5*1024*1024 else u"sz"
        dup_note = u' <span class="dup-badge" title="Duplikat von: %s">[DUP]</span>' % esc(a["duplicate_of"]) if a["duplicate_of"] else u""

        lines.append(u'<tr class="%s" data-row="1">' % dup_cls)
        lines.append(u'<td data-col="mailbox"><span class="mb-tag">%s</span></td>' % esc(a["mailbox"]))
        lines.append(u'<td class="date" data-col="date_fmt">%s</td>' % esc(a["date_fmt"]))
        lines.append(u'<td data-col="from" title="%s">%s</td>' % (esc(a["from"]), esc(a["from"][:40])))
        lines.append(u'<td data-col="subject" title="%s">%s</td>' % (esc(a["subject"]), esc(a["subject"][:55])))
        lines.append(u'<td data-col="filename">%s%s</td>' % (esc(a["filename"]), dup_note))
        lines.append(u'<td class="mc" data-col="mime">%s</td>' % esc(a["mime"]))
        lines.append(u'<td class="%s" data-col="size_bytes" data-raw="%d">%s</td>' % (sz_cls, a["size_bytes"], esc(a["size_human"])))
        lines.append(u"</tr>")

    lines.append(u"</tbody></table>")
    lines.append(u'<div id="summary"></div>')
    lines.append(HTML_FOOT)
    return u"\n".join(lines)

# \u2500\u2500 main \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    root = os.path.realpath(sys.argv[1])
    if not os.path.exists(root):
        print("ERROR: path does not exist: %s" % root)
        sys.exit(1)

    print("\n Scanning: %s\n" % root)
    attachments = scan_maildir(root)
    attachments = dedup(attachments)
    attachments.sort(key=lambda a: a["size_bytes"], reverse=True)

    parent   = os.path.dirname(root)
    out_html = os.path.join(parent, "attachments_report.html")
    out_json = os.path.join(parent, "attachments_raw.json")

    with codecs.open(out_html, "w", encoding="utf-8") as f:
        f.write(build_html(attachments, root))

    with codecs.open(out_json, "w", encoding="utf-8") as f:
        clean = [{k: v for k, v in a.items() if k != "date_obj"} for a in attachments]
        json.dump(clean, f, ensure_ascii=False, indent=2)

    total = sum(a["size_bytes"] for a in attachments)
    print("\n Done.")
    print("   Attachments : %d" % len(attachments))
    print("   Total size  : %s" % human_size(total))
    print("   HTML report : %s" % out_html)
    print("   JSON raw    : %s" % out_json)

if __name__ == "__main__":
    main()
