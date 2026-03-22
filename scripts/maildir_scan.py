#!/usr/bin/env python2
# -*- coding: utf-8 -*-
"""
maildir_scan.py  --  Python 2.7 compatible full mail scanner
Scans a Maildir tree and generates an HTML report of ALL mail content:
  - every mail with size, sender, subject, date
  - all parts: attachments, inline images, text bodies, calendars, etc.
  - duplicate detection across all binary parts
  - per-folder breakdown

Usage:
    python maildir_scan.py /email/mrija.org/andrii.karioti/.maildir
    python maildir_scan.py /email/mrija.org/andrii.karioti/.maildir/.Sent
"""

from __future__ import print_function
import os, sys, email, hashlib, json, codecs
from email.header import decode_header, make_header
from datetime import datetime
from collections import defaultdict

# ── helpers ────────────────────────────────────────────────────────────────

def to_unicode(s):
    if s is None:
        return u""
    if isinstance(s, unicode):
        return s
    if isinstance(s, str):
        return s.decode("utf-8", errors="replace")
    return unicode(s)

def decode_str(s):
    if s is None:
        return u""
    try:
        return to_unicode(unicode(make_header(decode_header(s))))
    except Exception:
        return to_unicode(s)

def parse_date(date_str):
    if not date_str:
        return u"", u""
    from email.utils import parsedate
    try:
        t = parsedate(date_str)
        if t:
            dt = datetime(*t[:6])
            return dt.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        pass
    return u"", to_unicode(date_str[:30])

def human_size(n):
    for unit in (u"B", u"KB", u"MB", u"GB"):
        if n < 1024:
            return u"%.1f %s" % (n, unit)
        n /= 1024.0
    return u"%.1f GB" % n

def esc(s):
    s = to_unicode(s)
    return s.replace(u"&", u"&amp;").replace(u"<", u"&lt;").replace(u">", u"&gt;").replace(u'"', u"&quot;")

# ── part classification ────────────────────────────────────────────────────

def classify_part(ct, disp, fname):
    """Return a category label for a MIME part."""
    if ct.startswith("image/"):
        return u"image"
    if ct == "application/pdf":
        return u"pdf"
    if ct in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",
              "application/msword"):
        return u"word"
    if ct in ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              "application/vnd.ms-excel"):
        return u"excel"
    if ct in ("application/vnd.openxmlformats-officedocument.presentationml.presentation",
              "application/vnd.ms-powerpoint"):
        return u"pptx"
    if ct == "text/calendar" or ct == "application/ics":
        return u"calendar"
    if ct == "application/pkcs7-signature" or ct == "application/x-pkcs7-signature":
        return u"signature"
    if ct.startswith("video/"):
        return u"video"
    if ct.startswith("audio/"):
        return u"audio"
    if ct == "application/zip" or ct == "application/x-zip-compressed":
        return u"archive"
    if ct.startswith("text/"):
        return u"text"
    if disp in ("attachment",) or (fname and fname != u"[inline]"):
        return u"attachment"
    return u"other"

# ── core extraction ────────────────────────────────────────────────────────

def parse_mail(filepath, folder_name):
    """
    Parse one mail file. Returns a dict with:
      - mail metadata (from, subject, date, total_size)
      - parts: list of all MIME parts with their metadata
    """
    try:
        raw = open(filepath, "rb").read()
    except Exception:
        return None

    total_size = len(raw)

    try:
        msg = email.message_from_string(raw)
    except Exception:
        return None

    date_day, date_fmt = parse_date(msg.get("Date"))
    subject  = decode_str(msg.get("Subject", u"(no subject)"))
    sender   = decode_str(msg.get("From", u""))
    to       = decode_str(msg.get("To", u""))

    parts = []
    seen_hashes = set()

    for part in msg.walk():
        ct   = to_unicode(part.get_content_type().lower())
        disp = to_unicode(part.get("Content-Disposition") or u"")
        disp_type = disp.split(";")[0].strip().lower()

        # get filename if any
        fname = part.get_filename()
        fname = decode_str(fname) if fname else u""
        if not fname:
            if ct not in (u"text/plain", u"text/html",
                          u"multipart/mixed", u"multipart/alternative",
                          u"multipart/related", u"multipart/signed",
                          u"message/rfc822"):
                fname = u"[inline %s]" % ct.split("/")[-1]
            else:
                fname = u""

        # get payload
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            payload = None

        part_size = len(payload) if payload else 0

        # skip empty/tiny
        if part_size < 64 and ct in (u"text/plain", u"text/html"):
            continue
        if part_size == 0:
            continue

        # dedup hash
        content_hash = u""
        is_dup = False
        if payload and part_size > 512:
            content_hash = hashlib.md5(payload).hexdigest()
            if content_hash in seen_hashes:
                is_dup = True
            else:
                seen_hashes.add(content_hash)

        category = classify_part(ct, disp_type, fname)

        parts.append({
            u"fname":    fname,
            u"mime":     ct,
            u"disp":     disp_type,
            u"category": category,
            u"size":     part_size,
            u"hash":     content_hash,
            u"is_dup":   is_dup,
        })

    return {
        u"folder":     to_unicode(folder_name),
        u"date_day":   to_unicode(date_day),
        u"date_fmt":   to_unicode(date_fmt),
        u"subject":    subject,
        u"from":       sender,
        u"to":         to,
        u"total_size": total_size,
        u"filepath":   to_unicode(filepath),
        u"parts":      parts,
    }

def scan_maildir(root):
    all_mails = []
    total_files = 0
    global_hashes = {}  # hash -> first mail subject (cross-mail dedup)

    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "tmp"]
        base = os.path.basename(dirpath)
        if base not in ("cur", "new"):
            continue

        # folder name from parent dir relative to root
        rel = os.path.relpath(dirpath, root)
        parts = rel.replace("\\", "/").split("/")
        folder = parts[0] if parts[0] not in ("cur", "new") else u"INBOX"

        for fname in files:
            if fname.startswith("."):
                continue
            fpath = os.path.join(dirpath, fname)
            total_files += 1
            mail = parse_mail(fpath, folder)
            if mail:
                # cross-mail dedup
                for p in mail[u"parts"]:
                    h = p[u"hash"]
                    if h and not p[u"is_dup"]:
                        if h in global_hashes:
                            p[u"is_dup"] = True
                        else:
                            global_hashes[h] = mail[u"subject"]
                all_mails.append(mail)

    print(u"  Scanned %d mail files, parsed %d mails." % (total_files, len(all_mails)))
    return all_mails

# ── stats ──────────────────────────────────────────────────────────────────

def compute_stats(mails):
    total_mail_size = sum(m[u"total_size"] for m in mails)
    total_parts     = sum(len(m[u"parts"]) for m in mails)
    total_dup_parts = sum(sum(1 for p in m[u"parts"] if p[u"is_dup"]) for m in mails)

    by_folder = defaultdict(lambda: {u"count": 0, u"size": 0, u"parts": 0})
    by_cat    = defaultdict(lambda: {u"count": 0, u"size": 0})
    by_year   = defaultdict(lambda: {u"count": 0, u"size": 0})

    for m in mails:
        f = m[u"folder"]
        by_folder[f][u"count"] += 1
        by_folder[f][u"size"]  += m[u"total_size"]
        by_folder[f][u"parts"] += len(m[u"parts"])
        yr = m[u"date_day"][:4] if m[u"date_day"] else u"unknown"
        by_year[yr][u"count"] += 1
        by_year[yr][u"size"]  += m[u"total_size"]
        for p in m[u"parts"]:
            by_cat[p[u"category"]][u"count"] += 1
            by_cat[p[u"category"]][u"size"]  += p[u"size"]

    # top 20 largest mails
    top_mails = sorted(mails, key=lambda m: -m[u"total_size"])[:20]

    # top 20 largest parts across all mails
    all_parts = []
    for m in mails:
        for p in m[u"parts"]:
            all_parts.append({
                u"fname":   p[u"fname"],
                u"mime":    p[u"mime"],
                u"category":p[u"category"],
                u"size":    p[u"size"],
                u"is_dup":  p[u"is_dup"],
                u"subject": m[u"subject"],
                u"from":    m[u"from"],
                u"date":    m[u"date_fmt"],
                u"folder":  m[u"folder"],
            })
    top_parts = sorted(all_parts, key=lambda p: -p[u"size"])[:20]

    return {
        u"total_mail_size": total_mail_size,
        u"total_mails":     len(mails),
        u"total_parts":     total_parts,
        u"total_dup_parts": total_dup_parts,
        u"by_folder":       dict(by_folder),
        u"by_cat":          dict(by_cat),
        u"by_year":         dict(by_year),
        u"top_mails":       top_mails,
        u"top_parts":       top_parts,
        u"all_parts":       all_parts,
    }

# ── HTML ───────────────────────────────────────────────────────────────────

CAT_COLORS = {
    u"pdf":        u"#fb4934",
    u"image":      u"#83a598",
    u"word":       u"#fabd2f",
    u"excel":      u"#b8bb26",
    u"pptx":       u"#fe8019",
    u"calendar":   u"#8ec07c",
    u"signature":  u"#504945",
    u"video":      u"#d3869b",
    u"audio":      u"#d3869b",
    u"archive":    u"#a89984",
    u"text":       u"#7c6f64",
    u"attachment": u"#fb4934",
    u"other":      u"#504945",
}

HTML_HEAD = u"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Mail Scan Report</title>
<style>
:root{--bg:#1d2021;--bg1:#282828;--bg2:#3c3836;--bg3:#504945;--fg:#ebdbb2;--fg2:#a89984;--fg3:#7c6f64;--red:#fb4934;--grn:#b8bb26;--ylw:#fabd2f;--blu:#83a598;--pur:#d3869b;--org:#fe8019;--aqua:#8ec07c}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--fg);font:13px/1.5 'JetBrains Mono','Fira Mono',monospace;padding:0}
.topbar{background:var(--bg1);border-bottom:1px solid var(--bg3);padding:.75rem 1.5rem;display:flex;align-items:baseline;justify-content:space-between;gap:1rem;flex-wrap:wrap;position:sticky;top:0;z-index:100}
.topbar h1{font-size:14px;font-weight:500;color:var(--ylw)}
.topbar span{font-size:11px;color:var(--fg2)}
.tabs{display:flex;gap:0;border-bottom:1px solid var(--bg3);background:var(--bg1);padding:0 1.5rem}
.tab{padding:.6rem 1.25rem;font-size:12px;color:var(--fg2);cursor:pointer;border-bottom:2px solid transparent;letter-spacing:.04em}
.tab:hover{color:var(--fg)}
.tab.active{color:var(--ylw);border-bottom-color:var(--ylw)}
.pane{display:none;padding:1.25rem 1.5rem}
.pane.active{display:block}
.metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:1.25rem}
.metric{background:var(--bg1);border:1px solid var(--bg3);border-radius:6px;padding:.75rem 1rem}
.metric-val{font-size:20px;font-weight:500;line-height:1.1}
.metric-lbl{font-size:10px;color:var(--fg2);margin-top:3px;letter-spacing:.04em;text-transform:uppercase}
.charts-row{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px}
.chart-box{background:var(--bg1);border:1px solid var(--bg3);border-radius:6px;padding:1rem}
.chart-box.full{grid-column:1/-1}
.chart-title{font-size:10px;color:var(--fg2);letter-spacing:.08em;text-transform:uppercase;margin-bottom:.75rem}
.legend{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:.6rem}
.leg-item{display:flex;align-items:center;gap:4px;font-size:10px;color:var(--fg2)}
.leg-dot{width:8px;height:8px;border-radius:2px;flex-shrink:0}
.controls{display:flex;gap:.75rem;flex-wrap:wrap;align-items:center;margin-bottom:1rem;padding:.75rem 1rem;background:var(--bg1);border:1px solid var(--bg3);border-radius:6px}
.controls label{font-size:11px;color:var(--fg2)}
.controls select,.controls input[type=text],.controls input[type=number]{background:var(--bg2);color:var(--fg);border:1px solid var(--bg3);border-radius:4px;padding:.3rem .6rem;font:inherit;font-size:11px}
.controls input[type=checkbox]{accent-color:var(--ylw)}
.btn{background:var(--bg2);color:var(--fg);border:1px solid var(--bg3);border-radius:4px;padding:.3rem .8rem;cursor:pointer;font:inherit;font-size:11px}
.btn:hover{border-color:var(--ylw);color:var(--ylw)}
table{width:100%;border-collapse:collapse;font-size:11px}
th{background:var(--bg2);color:var(--ylw);text-align:left;padding:.45rem .7rem;border-bottom:2px solid var(--bg3);cursor:pointer;user-select:none;white-space:nowrap;font-size:10px;letter-spacing:.05em;text-transform:uppercase}
th:hover{color:var(--org)}
td{padding:.4rem .7rem;border-bottom:1px solid var(--bg2);vertical-align:top;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
tr:hover td{background:var(--bg1)}
tr.dup td{opacity:.45}
tr.hidden{display:none}
.folder-tag{background:var(--bg2);color:var(--blu);border-radius:3px;padding:1px 5px;font-size:10px}
.cat-tag{border-radius:3px;padding:1px 5px;font-size:10px;font-weight:500}
.sz{text-align:right;font-variant-numeric:tabular-nums;color:var(--grn)}
.sz.big{color:var(--red);font-weight:500}
.sz.med{color:var(--ylw)}
.date-col{color:var(--fg2);white-space:nowrap}
.dup-badge{color:var(--pur);font-size:10px}
.muted{color:var(--fg2)}
#summary-parts,#summary-mails{margin-top:.75rem;font-size:10px;color:var(--fg2)}
.section-title{font-size:11px;color:var(--ylw);letter-spacing:.06em;text-transform:uppercase;margin-bottom:.75rem;padding-bottom:.4rem;border-bottom:1px solid var(--bg3)}
</style>
</head>
<body>
"""

HTML_FOOT = u"""
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<script>
var STATS = window._STATS;
var CAT_COLORS = window._CAT_COLORS;

function humanSize(n){
  var u=['B','KB','MB','GB'];
  for(var i=0;i<u.length;i++){if(n<1024)return n.toFixed(1)+' '+u[i];n/=1024;}
  return n.toFixed(1)+' GB';
}

var grid={color:'rgba(80,73,69,0.4)'};
var tick={color:'#a89984',font:{size:10}};

// ── tab switching ──
document.querySelectorAll('.tab').forEach(function(t){
  t.addEventListener('click',function(){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active');});
    document.querySelectorAll('.pane').forEach(function(x){x.classList.remove('active');});
    t.classList.add('active');
    document.getElementById(t.dataset.pane).classList.add('active');
  });
});

// ── folder chart ──
(function(){
  var folders = Object.keys(STATS.by_folder).sort(function(a,b){
    return STATS.by_folder[b].size - STATS.by_folder[a].size;
  });
  var sizes = folders.map(function(f){return (STATS.by_folder[f].size/1024/1024).toFixed(1);});
  var counts = folders.map(function(f){return STATS.by_folder[f].count;});
  var colors = ['#fabd2f','#83a598','#fb4934','#d3869b','#8ec07c','#fe8019','#a89984'];
  new Chart(document.getElementById('cFolder'),{
    type:'bar',
    data:{labels:folders,datasets:[{data:sizes,backgroundColor:colors.slice(0,folders.length),borderRadius:3,borderSkipped:false}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false},tooltip:{callbacks:{label:function(c){return c.parsed.y+' MB ('+counts[c.dataIndex]+' mails)';},}}},scales:{x:{ticks:tick,grid:grid},y:{ticks:{color:'#a89984',font:{size:10},callback:function(v){return v+'MB';}},grid:grid}}}
  });
})();

// ── category chart ──
(function(){
  var cats = Object.keys(STATS.by_cat).sort(function(a,b){
    return STATS.by_cat[b].size - STATS.by_cat[a].size;
  });
  var sizes = cats.map(function(c){return (STATS.by_cat[c].size/1024/1024).toFixed(1);});
  var counts = cats.map(function(c){return STATS.by_cat[c].count;});
  var colors = cats.map(function(c){return CAT_COLORS[c]||'#504945';});

  // legend
  var leg = document.getElementById('catLegend');
  cats.forEach(function(c,i){
    var el = document.createElement('span');
    el.className='leg-item';
    el.innerHTML='<span class="leg-dot" style="background:'+colors[i]+'"></span>'+c+' '+counts[i];
    leg.appendChild(el);
  });

  new Chart(document.getElementById('cCat'),{
    type:'doughnut',
    data:{labels:cats,datasets:[{data:sizes,backgroundColor:colors,borderWidth:1,borderColor:'#1d2021'}]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'55%',plugins:{legend:{display:false},tooltip:{callbacks:{label:function(c){return c.label+': '+c.parsed.toFixed(1)+' MB ('+counts[c.dataIndex]+' parts)';},}}}}
  });
})();

// ── year chart ──
(function(){
  var years = Object.keys(STATS.by_year).sort();
  var counts = years.map(function(y){return STATS.by_year[y].count;});
  var sizes  = years.map(function(y){return (STATS.by_year[y].size/1024/1024).toFixed(1);});
  new Chart(document.getElementById('cYear'),{
    type:'bar',
    data:{labels:years,datasets:[{data:counts,backgroundColor:'#83a598',borderRadius:3,borderSkipped:false}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:tick,grid:grid},y:{ticks:tick,grid:grid}}}
  });
  new Chart(document.getElementById('cYearSz'),{
    type:'bar',
    data:{labels:years,datasets:[{data:sizes,backgroundColor:'#fabd2f',borderRadius:3,borderSkipped:false}]},
    options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:tick,grid:grid},y:{ticks:{color:'#a89984',font:{size:10},callback:function(v){return v+'MB';}},grid:grid}}}
  });
})();

// ── parts table ──
(function(){
  var rows = Array.from(document.querySelectorAll('#partsBody tr[data-row]'));
  var tbody = document.getElementById('partsBody');
  var sortCol='size', sortDir=-1;

  function val(row,col){
    var el=row.querySelector('[data-col="'+col+'"]');
    if(!el)return'';
    var r=el.dataset.raw!==undefined?el.dataset.raw:el.textContent;
    return isNaN(r)?r.toLowerCase():Number(r);
  }
  function sortTable(col){
    if(sortCol===col)sortDir*=-1;else{sortCol=col;sortDir=-1;}
    rows.sort(function(a,b){var va=val(a,col),vb=val(b,col);return va<vb?sortDir:va>vb?-sortDir:0;});
    rows.forEach(function(r){tbody.appendChild(r);});
    document.querySelectorAll('#partsTable th').forEach(function(th){
      var ic=th.querySelector('.si');
      if(ic)ic.textContent=th.dataset.col===col?(sortDir>0?'\u25b2':'\u25bc'):'\u21c5';
    });
    updatePartsSummary();
  }
  function applyPartsFilter(){
    var cat=document.getElementById('fCat').value.toLowerCase();
    var folder=document.getElementById('fFolder').value.toLowerCase();
    var minSz=Number(document.getElementById('fMinSize').value)*1024;
    var hideDup=document.getElementById('fDup').checked;
    var search=document.getElementById('fSearch').value.toLowerCase();
    rows.forEach(function(r){
      var isDup=r.classList.contains('dup');
      var sz=Number(r.querySelector('[data-col="size"]').dataset.raw);
      var catv=r.querySelector('[data-col="category"]').textContent.toLowerCase();
      var folderv=r.querySelector('[data-col="folder"]').textContent.toLowerCase();
      var fn=r.querySelector('[data-col="fname"]').textContent.toLowerCase();
      var sub=r.querySelector('[data-col="subject"]')?r.querySelector('[data-col="subject"]').textContent.toLowerCase():'';
      var show=(!cat||catv.includes(cat))&&(!folder||folderv.includes(folder))&&sz>=minSz&&!(hideDup&&isDup)&&(!search||fn.includes(search)||sub.includes(search));
      r.classList.toggle('hidden',!show);
    });
    updatePartsSummary();
  }
  function updatePartsSummary(){
    var visible=rows.filter(function(r){return!r.classList.contains('hidden');});
    var tot=visible.reduce(function(s,r){return s+Number(r.querySelector('[data-col="size"]').dataset.raw);},0);
    document.getElementById('summary-parts').textContent=visible.length+' of '+rows.length+' parts visible — total: '+humanSize(tot);
  }
  document.querySelectorAll('#partsTable th[data-col]').forEach(function(th){
    th.addEventListener('click',function(){sortTable(th.dataset.col);});
  });
  document.getElementById('fCat').addEventListener('change',applyPartsFilter);
  document.getElementById('fFolder').addEventListener('change',applyPartsFilter);
  document.getElementById('fMinSize').addEventListener('input',applyPartsFilter);
  document.getElementById('fDup').addEventListener('change',applyPartsFilter);
  document.getElementById('fSearch').addEventListener('input',applyPartsFilter);
  document.getElementById('btnReset').addEventListener('click',function(){
    document.getElementById('fCat').value='';
    document.getElementById('fFolder').value='';
    document.getElementById('fMinSize').value='0';
    document.getElementById('fDup').checked=false;
    document.getElementById('fSearch').value='';
    applyPartsFilter();
  });
  sortTable('size');
  updatePartsSummary();
})();

// ── mails table ──
(function(){
  var rows = Array.from(document.querySelectorAll('#mailsBody tr[data-row]'));
  var tbody = document.getElementById('mailsBody');
  var sortCol='size', sortDir=-1;

  function val(row,col){
    var el=row.querySelector('[data-col="'+col+'"]');
    if(!el)return'';
    var r=el.dataset.raw!==undefined?el.dataset.raw:el.textContent;
    return isNaN(r)?r.toLowerCase():Number(r);
  }
  function sortMails(col){
    if(sortCol===col)sortDir*=-1;else{sortCol=col;sortDir=-1;}
    rows.sort(function(a,b){var va=val(a,col),vb=val(b,col);return va<vb?sortDir:va>vb?-sortDir:0;});
    rows.forEach(function(r){tbody.appendChild(r);});
    document.querySelectorAll('#mailsTable th').forEach(function(th){
      var ic=th.querySelector('.si');
      if(ic)ic.textContent=th.dataset.col===col?(sortDir>0?'\u25b2':'\u25bc'):'\u21c5';
    });
    updateMailsSummary();
  }
  function applyMailsFilter(){
    var folder=document.getElementById('mfFolder').value.toLowerCase();
    var search=document.getElementById('mfSearch').value.toLowerCase();
    rows.forEach(function(r){
      var folderv=r.querySelector('[data-col="folder"]').textContent.toLowerCase();
      var sub=r.querySelector('[data-col="subject"]').textContent.toLowerCase();
      var from=r.querySelector('[data-col="from"]').textContent.toLowerCase();
      var show=(!folder||folderv.includes(folder))&&(!search||sub.includes(search)||from.includes(search));
      r.classList.toggle('hidden',!show);
    });
    updateMailsSummary();
  }
  function updateMailsSummary(){
    var visible=rows.filter(function(r){return!r.classList.contains('hidden');});
    var tot=visible.reduce(function(s,r){return s+Number(r.querySelector('[data-col="size"]').dataset.raw);},0);
    document.getElementById('summary-mails').textContent=visible.length+' of '+rows.length+' mails visible — total: '+humanSize(tot);
  }
  document.querySelectorAll('#mailsTable th[data-col]').forEach(function(th){
    th.addEventListener('click',function(){sortMails(th.dataset.col);});
  });
  document.getElementById('mfFolder').addEventListener('change',applyMailsFilter);
  document.getElementById('mfSearch').addEventListener('input',applyMailsFilter);
  document.getElementById('mfReset').addEventListener('click',function(){
    document.getElementById('mfFolder').value='';
    document.getElementById('mfSearch').value='';
    applyMailsFilter();
  });
  sortMails('size');
  updateMailsSummary();
})();
</script>
</body></html>
"""

def build_html(mails, stats, root_path):
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    folders   = sorted(stats[u"by_folder"].keys())
    cats      = sorted(stats[u"by_cat"].keys())

    lines = [HTML_HEAD]

    # inject data for JS
    lines.append(u'<script>')
    lines.append(u'window._STATS = %s;' % json.dumps({
        u"by_folder": {k: {u"count": v[u"count"], u"size": v[u"size"]} for k, v in stats[u"by_folder"].items()},
        u"by_cat":    {k: {u"count": v[u"count"], u"size": v[u"size"]} for k, v in stats[u"by_cat"].items()},
        u"by_year":   {k: {u"count": v[u"count"], u"size": v[u"size"]} for k, v in stats[u"by_year"].items()},
    }, ensure_ascii=True))
    color_js = u"{" + u",".join(u'"%s":"%s"' % (k, v) for k, v in CAT_COLORS.items()) + u"}"
    lines.append(u'window._CAT_COLORS = %s;' % color_js)
    lines.append(u'</script>')

    # topbar
    lines.append(u'<div class="topbar"><h1>%s</h1><span>%s &mdash; %d mails &mdash; %d parts</span></div>' % (
        esc(to_unicode(root_path)),
        generated,
        stats[u"total_mails"],
        stats[u"total_parts"],
    ))

    # tabs
    lines.append(u'<div class="tabs">')
    for tab_id, tab_label in [(u"pOverview", u"Overview"), (u"pParts", u"All Parts"), (u"pMails", u"All Mails")]:
        active = u" active" if tab_id == u"pOverview" else u""
        lines.append(u'<div class="tab%s" data-pane="%s">%s</div>' % (active, tab_id, tab_label))
    lines.append(u'</div>')

    # ── pane: overview ──
    lines.append(u'<div class="pane active" id="pOverview">')

    lines.append(u'<div class="metrics">')
    for val, lbl, color in [
        (human_size(stats[u"total_mail_size"]), u"total mail size", u"var(--org)"),
        (unicode(stats[u"total_mails"]),        u"mails",          u"var(--ylw)"),
        (unicode(stats[u"total_parts"]),        u"content parts",  u"var(--blu)"),
        (unicode(stats[u"total_dup_parts"]),    u"duplicate parts",u"var(--pur)"),
        (unicode(len(folders)),                 u"folders",        u"var(--grn)"),
    ]:
        lines.append(u'<div class="metric"><div class="metric-val" style="color:%s">%s</div><div class="metric-lbl">%s</div></div>' % (color, val, lbl))
    lines.append(u'</div>')

    lines.append(u'<div class="charts-row">')
    lines.append(u'<div class="chart-box"><div class="chart-title">size by folder (MB)</div><div style="position:relative;height:220px"><canvas id="cFolder"></canvas></div></div>')
    lines.append(u'<div class="chart-box"><div class="chart-title">content by category</div><div class="legend" id="catLegend"></div><div style="position:relative;height:180px"><canvas id="cCat"></canvas></div></div>')
    lines.append(u'</div>')
    lines.append(u'<div class="charts-row">')
    lines.append(u'<div class="chart-box"><div class="chart-title">mails per year</div><div style="position:relative;height:160px"><canvas id="cYear"></canvas></div></div>')
    lines.append(u'<div class="chart-box"><div class="chart-title">size per year (MB)</div><div style="position:relative;height:160px"><canvas id="cYearSz"></canvas></div></div>')
    lines.append(u'</div>')

    # top 20 largest mails
    lines.append(u'<div class="section-title" style="margin-top:1.25rem">top 20 largest mails</div>')
    lines.append(u'<table><thead><tr>')
    for h in [u"folder", u"date", u"from", u"subject", u"parts", u"size"]:
        lines.append(u'<th>%s</th>' % h)
    lines.append(u'</tr></thead><tbody>')
    for m in stats[u"top_mails"]:
        big = m[u"total_size"] > 10*1024*1024
        szc = u"sz big" if big else (u"sz med" if m[u"total_size"] > 2*1024*1024 else u"sz")
        lines.append(u'<tr>')
        lines.append(u'<td><span class="folder-tag">%s</span></td>' % esc(m[u"folder"]))
        lines.append(u'<td class="date-col">%s</td>' % esc(m[u"date_fmt"]))
        lines.append(u'<td class="muted" title="%s">%s</td>' % (esc(m[u"from"]), esc(m[u"from"][:35])))
        lines.append(u'<td title="%s">%s</td>' % (esc(m[u"subject"]), esc(m[u"subject"][:55])))
        lines.append(u'<td class="muted" style="text-align:right">%d</td>' % len(m[u"parts"]))
        lines.append(u'<td class="%s" data-raw="%d">%s</td>' % (szc, m[u"total_size"], esc(human_size(m[u"total_size"]))))
        lines.append(u'</tr>')
    lines.append(u'</tbody></table>')
    lines.append(u'</div>')  # end pOverview

    # ── pane: all parts ──
    lines.append(u'<div class="pane" id="pParts">')
    folder_opts = u"".join(u'<option value="%s">%s</option>' % (esc(f), esc(f)) for f in folders)
    cat_opts    = u"".join(u'<option value="%s">%s</option>' % (esc(c), esc(c)) for c in cats)
    lines.append(u'''<div class="controls">
  <label>Category: <select id="fCat"><option value="">All</option>%s</select></label>
  <label>Folder: <select id="fFolder"><option value="">All</option>%s</select></label>
  <label>Min size (KB): <input type="number" id="fMinSize" value="0" min="0" style="width:75px"/></label>
  <label><input type="checkbox" id="fDup"/> Hide duplicates</label>
  <label>Search: <input type="text" id="fSearch" placeholder="filename / subject" style="width:180px"/></label>
  <button class="btn" id="btnReset">Reset</button>
</div>''' % (cat_opts, folder_opts))

    lines.append(u'<table id="partsTable"><thead><tr>')
    for col, lbl in [(u"folder",u"folder"),(u"date",u"date"),(u"from",u"from"),(u"subject",u"subject"),(u"fname",u"filename"),(u"category",u"type"),(u"size",u"size")]:
        lines.append(u'<th data-col="%s">%s <span class="si">\u21c5</span></th>' % (col, lbl))
    lines.append(u'</tr></thead><tbody id="partsBody">')

    for m in mails:
        for p in m[u"parts"]:
            dup_cls = u"dup" if p[u"is_dup"] else u""
            big = p[u"size"] > 10*1024*1024
            szc = u"sz big" if big else (u"sz med" if p[u"size"] > 2*1024*1024 else u"sz")
            col = CAT_COLORS.get(p[u"category"], u"#504945")
            dup_note = u' <span class="dup-badge">[DUP]</span>' if p[u"is_dup"] else u""
            lines.append(u'<tr class="%s" data-row="1">' % dup_cls)
            lines.append(u'<td data-col="folder"><span class="folder-tag">%s</span></td>' % esc(m[u"folder"]))
            lines.append(u'<td class="date-col" data-col="date">%s</td>' % esc(m[u"date_fmt"]))
            lines.append(u'<td class="muted" data-col="from" title="%s">%s</td>' % (esc(m[u"from"]), esc(m[u"from"][:30])))
            lines.append(u'<td data-col="subject" title="%s">%s</td>' % (esc(m[u"subject"]), esc(m[u"subject"][:50])))
            lines.append(u'<td data-col="fname">%s%s</td>' % (esc(p[u"fname"]), dup_note))
            lines.append(u'<td data-col="category"><span class="cat-tag" style="background:%s22;color:%s">%s</span></td>' % (col, col, esc(p[u"category"])))
            lines.append(u'<td class="%s" data-col="size" data-raw="%d">%s</td>' % (szc, p[u"size"], esc(human_size(p[u"size"]))))
            lines.append(u'</tr>')

    lines.append(u'</tbody></table>')
    lines.append(u'<div id="summary-parts"></div>')
    lines.append(u'</div>')  # end pParts

    # ── pane: all mails ──
    lines.append(u'<div class="pane" id="pMails">')
    mfolder_opts = u"".join(u'<option value="%s">%s</option>' % (esc(f), esc(f)) for f in folders)
    lines.append(u'''<div class="controls">
  <label>Folder: <select id="mfFolder"><option value="">All</option>%s</select></label>
  <label>Search: <input type="text" id="mfSearch" placeholder="subject / from" style="width:200px"/></label>
  <button class="btn" id="mfReset">Reset</button>
</div>''' % mfolder_opts)

    lines.append(u'<table id="mailsTable"><thead><tr>')
    for col, lbl in [(u"folder",u"folder"),(u"date",u"date"),(u"from",u"from"),(u"subject",u"subject"),(u"parts",u"parts"),(u"size",u"size")]:
        lines.append(u'<th data-col="%s">%s <span class="si">\u21c5</span></th>' % (col, lbl))
    lines.append(u'</tr></thead><tbody id="mailsBody">')

    for m in mails:
        big = m[u"total_size"] > 10*1024*1024
        szc = u"sz big" if big else (u"sz med" if m[u"total_size"] > 2*1024*1024 else u"sz")
        lines.append(u'<tr data-row="1">')
        lines.append(u'<td data-col="folder"><span class="folder-tag">%s</span></td>' % esc(m[u"folder"]))
        lines.append(u'<td class="date-col" data-col="date">%s</td>' % esc(m[u"date_fmt"]))
        lines.append(u'<td class="muted" data-col="from" title="%s">%s</td>' % (esc(m[u"from"]), esc(m[u"from"][:35])))
        lines.append(u'<td data-col="subject" title="%s">%s</td>' % (esc(m[u"subject"]), esc(m[u"subject"][:55])))
        lines.append(u'<td class="muted" data-col="parts" data-raw="%d" style="text-align:right">%d</td>' % (len(m[u"parts"]), len(m[u"parts"])))
        lines.append(u'<td class="%s" data-col="size" data-raw="%d">%s</td>' % (szc, m[u"total_size"], esc(human_size(m[u"total_size"]))))
        lines.append(u'</tr>')

    lines.append(u'</tbody></table>')
    lines.append(u'<div id="summary-mails"></div>')
    lines.append(u'</div>')  # end pMails

    lines.append(HTML_FOOT)
    return u"\n".join(lines)

# ── main ───────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    root = os.path.realpath(sys.argv[1])
    if not os.path.exists(root):
        print("ERROR: path does not exist: %s" % root)
        sys.exit(1)

    print("\n Scanning: %s\n" % root)
    mails = scan_maildir(root)
    stats = compute_stats(mails)

    parent   = os.path.dirname(root)
    out_html = os.path.join(parent, "mail_report.html")
    out_json = os.path.join(parent, "mail_raw.json")

    total_parts = stats[u"total_parts"]
    total_dups  = stats[u"total_dup_parts"]

    with codecs.open(out_html, "w", encoding="utf-8") as f:
        f.write(build_html(mails, stats, root))

    # slim json — skip raw parts payload info, just metadata
    slim = []
    for m in mails:
        slim.append({
            u"folder":     m[u"folder"],
            u"date":       m[u"date_fmt"],
            u"from":       m[u"from"],
            u"to":         m[u"to"],
            u"subject":    m[u"subject"],
            u"total_size": m[u"total_size"],
            u"parts":      [{u"fname":p[u"fname"],u"mime":p[u"mime"],u"category":p[u"category"],u"size":p[u"size"],u"is_dup":p[u"is_dup"]} for p in m[u"parts"]],
        })
    with codecs.open(out_json, "w", encoding="utf-8") as f:
        json.dump(slim, f, ensure_ascii=True, indent=2)

    print("\n Done.")
    print("   Mails    : %d" % stats[u"total_mails"])
    print("   Parts    : %d (%d duplicates)" % (total_parts, total_dups))
    print("   Total sz : %s" % human_size(stats[u"total_mail_size"]))
    print("   Report   : %s" % out_html)
    print("   JSON     : %s" % out_json)

if __name__ == "__main__":
    main()
