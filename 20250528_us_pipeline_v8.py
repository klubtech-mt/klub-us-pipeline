"""
KLUB x Frastea — US Market Sales Outreach Pipeline v8
=======================================================
變更（相對 v5）：
- 新增：所有信件發完後，立即寄彙整報告給主管信箱
- 報告內容：摘要（VIP/General 各幾封、成功/失敗、開信率）+ 每封明細
- 報告格式：HTML 表格（與 dashboard 同風格）

用法：
  python 20250528_us_pipeline_v6.py run [leads.csv]   # 載入名單、發信、發報告
  python 20250528_us_pipeline_v6.py demo [leads.csv]  # 預覽，不發信
  python 20250528_us_pipeline_v6.py send              # 發今日待發信件
  python 20250528_us_pipeline_v6.py vip-notify        # 重發 VIP 確認信
  python 20250528_us_pipeline_v6.py tracker           # 輸出追蹤伺服器
"""

import os, csv, time, sqlite3, smtplib, uuid
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ═══════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════

SMTP_CONFIG = {
    "host": os.getenv("SMTP_HOST",     "smtp.office365.com"),
    "port": int(os.getenv("SMTP_PORT", "587")),
}

MANAGER_EMAIL = os.getenv("MANAGER_EMAIL", "mt08@klubtech.com")

REGION_SMTP = {
    "West": {
        "user":      os.getenv("SMTP_USER_WEST",     "mt04@frastea.com"),
        "password":  os.getenv("SMTP_PASSWORD_WEST", "YOUR_PASSWORD_WEST"),
        "from_name": "Elena Chiang - Frastea",
        "manager":   MANAGER_EMAIL,
    },
    "East": {
        "user":      os.getenv("SMTP_USER_EAST",     "mt08@frastea.com"),
        "password":  os.getenv("SMTP_PASSWORD_EAST", "YOUR_PASSWORD_EAST"),
        "from_name": "Elena Chiang - Frastea",
        "manager":   MANAGER_EMAIL,
    },
}

TRACKER_BASE_URL = os.getenv("TRACKER_URL", "https://your-tracker.zeabur.app")
DB_PATH          = "us_v8.db"

# ═══════════════════════════════════════════════
# PRODUCT CATEGORY — 依 industry 自動判斷
# ═══════════════════════════════════════════════

def detect_product(industry: str) -> str:
    ind = industry.lower()
    RAW_KEYWORDS      = ["f&b", "beverage", "cafe", "coffee", "restaurant",
                         "bubble tea", "tea", "food", "bar", "bakery", "juice"]
    MACHINERY_KEYWORDS = ["fitness", "wellness", "spa", "gym", "sport",
                          "health club", "beauty", "salon"]
    for k in RAW_KEYWORDS:
        if k in ind:
            return "raw"
    for k in MACHINERY_KEYWORDS:
        if k in ind:
            return "machinery"
    return "both"

# ═══════════════════════════════════════════════
# EMAIL TEMPLATES（來自 KLUB_Frastea_Emails模版.docx）
# ═══════════════════════════════════════════════

TEMPLATES = {
    ("VIP", "raw"): (
        "Tea & herbal supply for {company}",
        """Hi {first_name},

This is Elena from Frastea Co. Ltd. from Taiwan.

Running {company} means ingredient consistency is everything, one off-batch and it shows across the board.

We're Frastea, and we supply premium tea leaves and herbal ingredients to multi-location beverage chains across the US. A few groups your size have made the switch and haven't looked back.

We can arrange a short discussion or meeting on how we can support your operation.

Elena Chiang
Frastea │ +886 963 710 172
{sender_email}"""
    ),
    ("VIP", "machinery"): (
        "Brewing equipment built for your operations",
        """Hi {first_name},

This is Elena from Frastea Co. Ltd. from Taiwan.

When equipment goes down at one of your {company} locations, it's not just that store that feels it.

KLUB Technology builds commercial brewing machines for high-volume chains — designed for reliability and easy to standardize across locations. Happy to share what that looks like in practice.

We can arrange a short discussion or meeting on how we can support your operation.

Elena Chiang
Frastea │ +886 963 710 172
{sender_email}"""
    ),
    ("VIP", "both"): (
        "One partner for ingredients & equipment",
        """Hi {first_name},

This is Elena from Frastea Co. Ltd. from Taiwan.

Most chains in your company size are managing separate vendors for ingredients and equipment which works, until it doesn't.

Frastea and KLUB Technology are sister companies offering premium tea & herbal ingredients alongside the commercial brewing machines to brew them right. One partner, end to end.

We can arrange a short discussion or meeting on how we can support your operation.

Elena Chiang
Frastea │ +886 963 710 172
{sender_email}"""
    ),
    ("General", "raw"): (
        "Free sample, tea & herbal ingredients for {company}",
        """Hi {first_name},

This is Elena from Frastea Co. Ltd. from Taiwan.

We supply premium tea leaves and herbal ingredients to cafes and beverage shops across the US. If you're ever looking for a reliable source or just want to try something new on your menu, we'd love to send over a sample. No commitment.

We can arrange a short discussion or meeting on how we can support your operation.

Elena Chiang
Frastea │ +886 963 710 172
{sender_email}"""
    ),
    ("General", "machinery"): (
        "Brewing equipment for {company}, quick question",
        """Hi {first_name},

This is Elena from Frastea Co. Ltd. from Taiwan.

We make commercial brewing machines for cafes and beverage shops that need something reliable and easy to run day-to-day. If your current setup ever gives you trouble, or you're thinking about expanding, we'd love to show you what we have.

We can arrange a short discussion or meeting on how we can support your operation.

Elena Chiang
Frastea │ +886 963 710 172
{sender_email}"""
    ),
    ("General", "both"): (
        "Ingredients & equipment for {company}",
        """Hi {first_name},

This is Elena from Frastea Co. Ltd. from Taiwan.

Frastea and KLUB Technology are sister companies, we handle premium tea & herbal ingredients and the brewing machines to go with them. If you're sourcing either right now (or just open to exploring), we'd love to connect. Happy to send samples or a quick overview, whichever is more useful.

We can arrange a short discussion or meeting on how we can support your operation.

Elena Chiang
Frastea │ +886 963 710 172
{sender_email}"""
    ),
}

def get_template(tier: str, product: str) -> tuple:
    tier_key = "VIP" if tier.upper() == "VIP" else "General"
    return TEMPLATES.get((tier_key, product), TEMPLATES[("General", "both")])

def render_template(subject_tpl, body_tpl, company, first_name, sender_email):
    ctx = {"company": company, "first_name": first_name, "sender_email": sender_email}
    return subject_tpl.format(**ctx), body_tpl.format(**ctx)

# ═══════════════════════════════════════════════
# TRACKING PIXEL & HTML EMAIL
# ═══════════════════════════════════════════════

def pixel(track_id):
    return (f'<img src="{TRACKER_BASE_URL}/track/open/{track_id}" '
            f'width="1" height="1" style="display:none;"/>')

def make_html(body_text, track_id):
    body = body_text.replace("\n", "<br/>")
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:600px;margin:0 auto;padding:20px;">
<div style="border-left:4px solid #028090;padding-left:16px;margin-bottom:20px;">
  <p style="color:#028090;font-size:12px;margin:0;">KLUB Technology × Frastea</p>
</div>
<div style="line-height:1.8;">{body}</div>
<div style="margin-top:30px;padding-top:16px;border-top:1px solid #eee;font-size:11px;color:#999;">
  <p>Frastea Co. Ltd. | <a href="https://frastea.com">frastea.com</a> &nbsp;|&nbsp;
     KLUB Technology | <a href="https://klubtech.com">klubtech.com</a></p>
  <p style="font-size:10px;">To unsubscribe, reply with "unsubscribe".</p>
</div>
{pixel(track_id)}
</body></html>"""

def make_vip_approval_email(row):
    approve_url  = f"{TRACKER_BASE_URL}/approve/{row['track_id']}"
    body_preview = row["body_html"]
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:680px;margin:0 auto;padding:20px;">
<div style="background:#1C2B4A;padding:16px 20px;border-radius:8px;margin-bottom:20px;">
  <p style="color:#02C39A;margin:0;font-size:12px;">VIP Email Approval Request</p>
  <h2 style="color:white;margin:6px 0 0;font-size:18px;">請審核並確認以下開發信件</h2>
</div>
<table style="width:100%;border-collapse:collapse;margin-bottom:20px;font-size:13px;">
  <tr style="background:#F2F6FA;"><td style="padding:8px 12px;font-weight:bold;width:120px;">公司</td><td style="padding:8px 12px;">{row['company']}</td></tr>
  <tr><td style="padding:8px 12px;font-weight:bold;">等級</td><td style="padding:8px 12px;"><span style="background:#6B3FA0;color:white;padding:2px 8px;border-radius:10px;font-size:11px;">VIP</span></td></tr>
  <tr style="background:#F2F6FA;"><td style="padding:8px 12px;font-weight:bold;">產業</td><td style="padding:8px 12px;">{row['industry']}</td></tr>
  <tr><td style="padding:8px 12px;font-weight:bold;">產品類別</td><td style="padding:8px 12px;">{row['product']}</td></tr>
  <tr style="background:#F2F6FA;"><td style="padding:8px 12px;font-weight:bold;">主旨</td><td style="padding:8px 12px;"><strong>{row['subject']}</strong></td></tr>
  <tr><td style="padding:8px 12px;font-weight:bold;">收件人</td><td style="padding:8px 12px;">{row['email_to']}</td></tr>
  <tr style="background:#F2F6FA;"><td style="padding:8px 12px;font-weight:bold;">排程日</td><td style="padding:8px 12px;">{row['send_date']}</td></tr>
</table>
<div style="border:1px solid #ddd;border-radius:8px;padding:20px;margin-bottom:24px;background:#FAFAFA;">
  <p style="font-size:11px;color:#999;margin:0 0 12px;">— 信件內容預覽 —</p>
  {body_preview}
</div>
<div style="text-align:center;margin:28px 0;">
  <a href="{approve_url}"
     style="background:#028090;color:white;padding:14px 48px;border-radius:8px;
            text-decoration:none;font-size:16px;font-weight:bold;display:inline-block;
            box-shadow:0 4px 12px rgba(2,128,144,0.4);">
    ✅ &nbsp; 確認 OK，立即排程發送
  </a>
</div>
<p style="font-size:11px;color:#999;text-align:center;">
  點擊後系統自動排入發送佇列。如需修改請回覆此信。
</p>
</body></html>"""

# ═══════════════════════════════════════════════
# 彙整報告
# ═══════════════════════════════════════════════

def make_report_html(results: list, sent_time: str) -> str:
    """
    results = [
      {"company": ..., "contact_name": ..., "email_to": ...,
       "tier": ..., "subject": ..., "status": "sent"|"failed"}
    ]
    """
    total   = len(results)
    success = sum(1 for r in results if r["status"] == "sent")
    failed  = total - success
    vip     = sum(1 for r in results if r["tier"].upper() == "VIP")
    general = total - vip
    vip_ok  = sum(1 for r in results if r["tier"].upper() == "VIP"  and r["status"] == "sent")
    gen_ok  = sum(1 for r in results if r["tier"].upper() != "VIP"  and r["status"] == "sent")

    # 開信率：從 DB 即時讀取
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM sequence WHERE status='sent'")
    total_sent = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM sequence WHERE status='sent' AND open_count>0")
    total_opened = c.fetchone()[0]
    conn.close()
    open_rate = f"{total_opened/total_sent*100:.1f}%" if total_sent else "—"

    tbody = ""
    for r in results:
        sc = "#2D6A4F" if r["status"] == "sent" else "#AE2012"
        tc = "#6B3FA0" if r["tier"].upper() == "VIP" else "#028090"
        icon = "✅" if r["status"] == "sent" else "✗"
        tbody += f"""<tr>
          <td>{r['company']}</td>
          <td>{r.get('contact_name','')}</td>
          <td><span style="background:{tc};color:white;padding:2px 8px;
              border-radius:10px;font-size:11px;">{r['tier']}</span></td>
          <td style="max-width:220px;overflow:hidden;text-overflow:ellipsis;
              white-space:nowrap">{r['subject']}</td>
          <td>{r['email_to']}</td>
          <td style="color:{sc};font-weight:bold;text-align:center">{icon} {r['status']}</td>
        </tr>"""

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>KLUB × Frastea 發信報告</title>
<style>
body{{font-family:Arial;margin:0;background:#F2F6FA}}
.hdr{{background:#1C2B4A;color:white;padding:20px 30px}}
.hdr p{{margin:4px 0;font-size:13px;color:#9DBBDA}}
.stats{{display:flex;gap:16px;padding:16px 20px;flex-wrap:wrap}}
.stat{{background:white;padding:12px 20px;border-radius:8px;text-align:center;min-width:110px}}
.stat .n{{font-size:26px;font-weight:bold;color:#028090}}
.stat .n.fail{{color:#AE2012}}
.stat .n.vip{{color:#6B3FA0}}
.stat .l{{font-size:11px;color:#666;margin-top:4px}}
.section{{padding:0 20px 20px}}
h3{{color:#1C2B4A;font-size:14px;margin:16px 0 8px}}
table{{width:100%;border-collapse:collapse;background:white;
       border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08)}}
th{{background:#1C2B4A;color:white;padding:10px 12px;font-size:12px;text-align:left}}
td{{padding:9px 12px;font-size:12px;border-bottom:1px solid #eee}}
tr:last-child td{{border-bottom:none}}
tr:hover{{background:#F8FAFC}}
</style></head>
<body>
<div class="hdr">
  <h2 style="margin:0 0 4px">📊 KLUB × Frastea — 發信彙整報告</h2>
  <p>發送時間：{sent_time}</p>
</div>
<div class="stats">
  <div class="stat"><div class="n">{total}</div><div class="l">總發送數</div></div>
  <div class="stat"><div class="n">{success}</div><div class="l">成功</div></div>
  <div class="stat"><div class="n fail">{failed}</div><div class="l">失敗</div></div>
  <div class="stat"><div class="n vip">{vip}</div><div class="l">VIP</div></div>
  <div class="stat"><div class="n">{general}</div><div class="l">General</div></div>
  <div class="stat"><div class="n">{vip_ok}</div><div class="l">VIP 成功</div></div>
  <div class="stat"><div class="n">{gen_ok}</div><div class="l">General 成功</div></div>
  <div class="stat"><div class="n">{open_rate}</div><div class="l">開信率</div></div>
</div>
<div class="section">
  <h3>每封明細</h3>
  <table>
    <thead><tr>
      <th>公司</th><th>聯絡人</th><th>等級</th>
      <th>主旨</th><th>收件信箱</th><th>狀態</th>
    </tr></thead>
    <tbody>{tbody}</tbody>
  </table>
</div>
</body></html>"""

def send_report(results: list):
    sent_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    html = make_report_html(results, sent_time)

    # 用 East 帳號寄報告給主管
    rcfg = REGION_SMTP["East"]
    msg  = MIMEMultipart("alternative")
    msg["Subject"] = f"[發信報告] KLUB × Frastea — {sent_time}"
    msg["From"]    = f"Sales System <{rcfg['user']}>"
    msg["To"]      = MANAGER_EMAIL
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        _port=int(os.getenv("SMTP_PORT","465"))
        _host=os.getenv("SMTP_HOST","smtp.office365.com")
        _cls=smtplib.SMTP_SSL if _port==465 else smtplib.SMTP
        with _cls(_host,_port,timeout=15) as s:
            s.ehlo()
            if _port!=465: s.starttls(); s.ehlo()
            s.login(rcfg["user"],rcfg["password"])
            s.sendmail(rcfg["user"],MANAGER_EMAIL,msg.as_string())
        print(f"\n✅ 彙整報告已寄出 → {MANAGER_EMAIL}")
    except Exception as e:
        print(f"\n✗ 報告寄送失敗：{e}")

# ═══════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS sequence (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        company    TEXT, tier TEXT, industry TEXT, product TEXT,
        email_to   TEXT, subject TEXT, body_html TEXT,
        send_date  TEXT, status TEXT DEFAULT 'pending',
        approved   INTEGER DEFAULT 0, track_id TEXT UNIQUE,
        sent_at    TEXT, opened_at TEXT,
        open_count INTEGER DEFAULT 0,
        region     TEXT DEFAULT 'East', sender TEXT,
        contact_name TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS tracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        track_id TEXT, event TEXT, ip TEXT, user_agent TEXT, timestamp TEXT
    )""")
    conn.commit(); conn.close()

def save_lead(lead, subject, body_html, track_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    tier     = lead.get("tier", "General").strip()
    approved = 1 if tier.upper() != "VIP" else 0
    c.execute("""INSERT OR IGNORE INTO sequence
        (company, tier, industry, product, email_to, subject, body_html,
         send_date, approved, track_id, region, sender, contact_name)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (lead.get("company",""), tier,
         lead.get("industry",""), lead.get("_product",""),
         lead.get("email",""), subject, body_html,
         datetime.now().strftime("%Y-%m-%d"),
         approved, track_id,
         lead.get("region","East"),
         lead.get("sender",""),
         lead.get("contact_name","")))
    conn.commit(); conn.close()

def get_pending_emails(today=None):
    today = today or datetime.now().strftime("%Y-%m-%d")
    conn  = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""SELECT * FROM sequence
                 WHERE status='pending' AND approved=1 AND send_date<=?
                 ORDER BY send_date, company""", (today,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close(); return rows

def get_vip_pending():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""SELECT * FROM sequence
                 WHERE tier='VIP' AND approved=0 AND status='pending'
                 ORDER BY company""")
    rows = [dict(r) for r in c.fetchall()]
    conn.close(); return rows

def mark_sent(row_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE sequence SET status='sent', sent_at=? WHERE id=?",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), row_id))
    conn.commit(); conn.close()

# ═══════════════════════════════════════════════
# SMTP
# ═══════════════════════════════════════════════

def send_html(to_addr, subject, html, from_name=None, region="East"):
    if not to_addr or "@" not in to_addr: return False
    rcfg = REGION_SMTP.get(region, REGION_SMTP["East"])
    msg  = MIMEMultipart("alternative")
    msg["Subject"]  = subject
    msg["From"]     = f"{from_name or rcfg['from_name']} <{rcfg['user']}>"
    msg["To"]       = to_addr
    msg["Reply-To"] = rcfg["user"]
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        _port=int(os.getenv("SMTP_PORT","465"))
        _host=os.getenv("SMTP_HOST","smtp.office365.com")
        _cls=smtplib.SMTP_SSL if _port==465 else smtplib.SMTP
        with _cls(_host,_port,timeout=15) as s:
            s.ehlo()
            if _port!=465: s.starttls(); s.ehlo()
            s.login(rcfg["user"],rcfg["password"])
            s.sendmail(rcfg["user"],to_addr,msg.as_string())
        print(f"     (發信帳號: {rcfg['user']})")
        return True
    except Exception as e:
        print(f"  ✗ SMTP [{region}]: {e}")
        return False

# ═══════════════════════════════════════════════
# CORE FLOW
# ═══════════════════════════════════════════════

def process_leads(csv_file, dry_run=False):
    init_db()
    with open(csv_file, encoding="utf-8-sig") as f:
        leads = list(csv.DictReader(f))

    print(f"載入 {len(leads)} 筆名單")
    print("=" * 60)

    for lead in leads:
        company      = lead.get("company","").strip()
        contact      = lead.get("contact_name","").strip()
        first_name   = contact.split()[0] if contact else "there"
        tier         = lead.get("tier","General").strip()
        industry     = lead.get("industry","").strip()
        sender_email = lead.get("sender","").strip()
        product      = detect_product(industry)
        lead["_product"] = product

        subj_tpl, body_tpl = get_template(tier, product)
        subject, body_text = render_template(subj_tpl, body_tpl, company, first_name, sender_email)
        track_id  = str(uuid.uuid4())
        body_html = make_html(body_text, track_id)

        print(f"  [{tier:7}] {company:30} | {industry:15} → {product}")
        print(f"           主旨: {subject}")

        if not dry_run:
            save_lead(lead, subject, body_html, track_id)

    if not dry_run:
        print("\n[VIP 確認信] 寄送主管審核...")
        send_vip_notifications()
        print("\n[General 發信] 發送今日信件...")
        results = run_daily_send()
        print("\n[彙整報告] 寄送發信報告給主管...")
        send_report(results)
    else:
        print("\n(dry_run 模式，未寫入 DB 也未發信)")

def run_daily_send(dry_run=False):
    today   = datetime.now().strftime("%Y-%m-%d")
    pending = get_pending_emails(today)
    results = []
    if not pending:
        print(f"[{today}] 今日無待發信件。")
        return results
    print(f"[{today}] 待發 {len(pending)} 封")
    for row in pending:
        label = f"  [{row['tier']:7}] {row['company']:30} → {row['email_to']}"
        if dry_run:
            print(f"{label}  (dry-run)")
            continue
        print(f"{label} ...", end=" ")
        ok = send_html(row["email_to"], row["subject"], row["body_html"],
                       region=row.get("region","East"))
        status = "sent" if ok else "failed"
        if ok:
            mark_sent(row["id"])
            print("✅")
        else:
            print("✗")
        results.append({
            "company":      row.get("company",""),
            "contact_name": row.get("contact_name",""),
            "tier":         row.get("tier",""),
            "subject":      row.get("subject",""),
            "email_to":     row.get("email_to",""),
            "status":       status,
        })
        time.sleep(2)
    return results

def send_vip_notifications():
    rows = get_vip_pending()
    if not rows:
        print("  無 VIP 待審核信件。"); return
    print(f"  寄送 {len(rows)} 封 VIP 確認信給主管...")
    for row in rows:
        region = row.get("region","East")
        mgr    = REGION_SMTP.get(region, REGION_SMTP["East"])["manager"]
        html   = make_vip_approval_email(row)
        ok     = send_html(mgr,
                           f"[VIP審核] {row['company']} — {row['subject']}",
                           html, "Sales System", region=region)
        print(f"  {'✅' if ok else '✗'} {row['company']} → 主管 {mgr} ({region})")
        time.sleep(1)

# ═══════════════════════════════════════════════
# TRACKER SERVER
# ═══════════════════════════════════════════════

TRACKER_CODE = '''"""
KLUB × Frastea Tracking Server v5 — 部署到 Zeabur
pip install flask pillow
"""
from flask import Flask, request, send_file
import sqlite3, io, os
from datetime import datetime
try:
    from PIL import Image
except:
    Image = None

app = Flask(__name__)
DB  = os.getenv("DB_PATH", "us_v8.db")

def log(track_id, event, req):
    conn = sqlite3.connect(DB)
    c    = conn.cursor()
    c.execute("INSERT INTO tracking (track_id,event,ip,user_agent,timestamp) VALUES (?,?,?,?,?)",
              (track_id, event, req.remote_addr,
               req.headers.get("User-Agent",""), datetime.now().isoformat()))
    if event == "open":
        c.execute("""UPDATE sequence SET
                     opened_at=COALESCE(opened_at,?), open_count=open_count+1
                     WHERE track_id=?""", (datetime.now().isoformat(), track_id))
    conn.commit(); conn.close()

@app.route("/track/open/<track_id>")
def track_open(track_id):
    log(track_id, "open", request)
    if Image:
        img = Image.new("RGBA",(1,1),(0,0,0,0))
        buf = io.BytesIO(); img.save(buf,"PNG"); buf.seek(0)
        return send_file(buf, mimetype="image/png")
    return "", 204

@app.route("/approve/<track_id>")
def approve(track_id):
    conn = sqlite3.connect(DB)
    c    = conn.cursor()
    c.execute("UPDATE sequence SET approved=1 WHERE track_id=?", (track_id,))
    c.execute("SELECT company, email_to FROM sequence WHERE track_id=?", (track_id,))
    row = c.fetchone()
    conn.commit(); conn.close()
    if row:
        company, email_to = row
        return f"""<html><head><meta charset="UTF-8"></head>
<body style="font-family:Arial;text-align:center;padding:60px;background:#F2F6FA;">
<div style="background:white;padding:40px;border-radius:12px;max-width:500px;
            margin:0 auto;box-shadow:0 4px 20px rgba(0,0,0,.1);">
  <div style="font-size:48px;">✅</div>
  <h2 style="color:#028090;">完成測試！！</h2>
  <p style="font-size:15px;color:#333;"><strong>{company}</strong></p>
  <p style="color:#666;font-size:13px;">信件已排入發送佇列。<br/>收件人：{email_to}</p>
</div>
<script>alert("完成測試！！");</script>
</body></html>"""
    return "Track ID not found", 404

@app.route("/health")
def health():
    return {"status": "ok", "service": "KLUB×Frastea Tracker v7"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
'''

# ═══════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "help"

    if mode == "run":
        csv_file = sys.argv[2] if len(sys.argv) > 2 else "test_leads.csv"
        process_leads(csv_file, dry_run=False)

    elif mode == "demo":
        csv_file = sys.argv[2] if len(sys.argv) > 2 else "test_leads.csv"
        process_leads(csv_file, dry_run=True)

    elif mode == "send":
        init_db()
        results = run_daily_send(dry_run=False)
        if results:
            print("\n[彙整報告] 寄送發信報告給主管...")
            send_report(results)

    elif mode == "vip-notify":
        init_db()
        send_vip_notifications()

    elif mode == "tracker":
        out = "20250528_us_tracker_server_v7.py"
        with open(out, "w", encoding="utf-8") as f:
            f.write(TRACKER_CODE)
        print(f"✅ 已輸出：{out}")

    else:
        print("""用法：
  run [leads.csv]    # 載入名單、發信、發彙整報告（預設 test_leads.csv）
  demo [leads.csv]   # 預覽，不發信
  send               # 發今日待發信件 + 寄報告
  vip-notify         # 重發 VIP 確認信
  tracker            # 輸出追蹤伺服器""")
