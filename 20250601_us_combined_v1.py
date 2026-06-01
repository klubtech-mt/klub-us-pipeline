#!/usr/bin/env python3
"""
KLUB x Frastea US Pipeline + Tracker Combined Service v1
Flask server that handles tracking pixels, VIP approvals, and pipeline execution
"""
import os, csv, sqlite3, smtplib, uuid, datetime, logging, time, random, threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, request, Response, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SMTP_HOST      = os.environ.get("SMTP_HOST", "mail.frastea.com")
SMTP_PORT      = int(os.environ.get("SMTP_PORT", "465"))
SMTP_USER_E    = os.environ.get("SMTP_USER_EAST", "mt08@frastea.com")
SMTP_PASS_E    = os.environ.get("SMTP_PASSWORD_EAST", "")
SMTP_USER_W    = os.environ.get("SMTP_USER_WEST", "mt04@frastea.com")
SMTP_PASS_W    = os.environ.get("SMTP_PASSWORD_WEST", "")
MANAGER_EMAIL  = os.environ.get("MANAGER_EMAIL", "mt08@frastea.com")
CSV_FILE       = os.environ.get("CSV_FILE", "/app/us_leads_pipeline.csv")
STATS_KEY      = os.environ.get("STATS_KEY", "klub2025")
BASE_URL       = os.environ.get("TRACKER_URL", "https://klub-us-tracker.zeabur.app")

DB_PATH        = "/app/us_v25.db"
TRACKER_DB     = "/app/tracker.db"

# Rate limiting
SEND_INTERVAL_MIN = 20
SEND_INTERVAL_MAX = 30
SEND_JITTER       = 5
HOURLY_LIMIT      = 120
BATCH_SIZE        = 30
BATCH_PAUSE       = 300
SEND_HOUR_START   = 12   # UTC 12 = Taiwan 20:00
SEND_HOUR_END     = 15   # UTC 15 = Taiwan 23:00

# ── Images (base64 placeholders - fetch from env or use URL) ──────────────────
IMG_MACHINE = os.environ.get("IMG_MACHINE_URL", "https://drive.google.com/uc?export=view&id=1zOB7-L6cc13a1NroonXlQ6dqKKEmDGxi")
IMG_RAW     = os.environ.get("IMG_RAW_URL", "https://drive.google.com/uc?export=view&id=1dmEHC5s4TpDlKqytzHRo41s2nitHYdkd")

# ── DB ────────────────────────────────────────────────────────────────────────
def init_pipeline_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS sent_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        email TEXT, company TEXT, tier TEXT, region TEXT,
        subject TEXT, status TEXT, tracking_id TEXT,
        sent_at TEXT DEFAULT (datetime('now'))
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vip_approvals (
        company TEXT PRIMARY KEY,
        approved_at TEXT,
        approved_by TEXT
    )""")
    con.commit(); con.close()

def init_tracker_db():
    con = sqlite3.connect(TRACKER_DB)
    con.execute("""CREATE TABLE IF NOT EXISTS opens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tracking_id TEXT, email TEXT, day TEXT, month TEXT,
        tier TEXT, opened_at TEXT DEFAULT (datetime('now')),
        ip TEXT, ua TEXT
    )""")
    con.commit(); con.close()

def already_sent(email, company):
    try:
        con = sqlite3.connect(DB_PATH)
        today = datetime.date.today().isoformat()
        r = con.execute(
            "SELECT 1 FROM sent_log WHERE email=? AND company=? AND DATE(sent_at)=?",
            (email, company, today)
        ).fetchone()
        con.close()
        return r is not None
    except: return False

def log_sent(email, company, tier, region, subject, status, tid):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO sent_log(email,company,tier,region,subject,status,tracking_id) VALUES(?,?,?,?,?,?,?)",
            (email, company, tier, region, subject, status, tid)
        )
        con.commit(); con.close()
    except Exception as e: log.error(f"log_sent: {e}")

def is_vip_approved(company):
    try:
        con = sqlite3.connect(DB_PATH)
        r = con.execute("SELECT 1 FROM vip_approvals WHERE company=?", (company,)).fetchone()
        con.close()
        return r is not None
    except: return False

def mark_vip_approved(company):
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT OR REPLACE INTO vip_approvals(company,approved_at) VALUES(?,datetime('now'))", (company,))
        con.commit(); con.close()
    except Exception as e: log.error(f"mark_vip_approved: {e}")

# ── SMTP ──────────────────────────────────────────────────────────────────────
def smtp_send(to, subject, html, region="East"):
    user = SMTP_USER_E if region.lower() != "west" else SMTP_USER_W
    pwd  = SMTP_PASS_E if region.lower() != "west" else SMTP_PASS_W
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"KLUB x Frastea <{user}>"
    msg["To"]      = to
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        if SMTP_PORT == 465:
            conn = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
        else:
            conn = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
            conn.ehlo(); conn.starttls()
        conn.login(user, pwd)
        conn.sendmail(user, [to], msg.as_string())
        conn.quit()
        log.info(f"SENT {user} -> {to}")
        return True, user
    except Exception as e:
        log.error(f"SMTP FAIL [{region}]: {e}")
        return False, str(e)

# ── Email templates ───────────────────────────────────────────────────────────
FRASTEA_HDR = """
<div style="background:#1a1a1a;padding:18px 28px;border-radius:6px 6px 0 0;">
  <span style="color:#fff;font-size:18px;font-weight:700;font-family:Arial,sans-serif;letter-spacing:1px;">
    KLUB <span style="color:#c8a96e;">×</span> Frastea
  </span>
</div>
<div style="background:#fff;padding:28px 28px 0 28px;border:1px solid #e0e0e0;border-top:none;">
"""

FRASTEA_FTR_TEMPLATE = """
</div>
<div style="background:#f9f9f9;padding:16px 28px;border:1px solid #e0e0e0;border-top:none;border-radius:0 0 6px 6px;font-family:Arial,sans-serif;font-size:12px;color:#555;">
  <strong>Elena Chiang</strong><br>
  Frastea Co. Ltd.<br>
  Email./ <span style="font-family:Courier New,monospace;">{email}</span><br>
  Tel./ +886 963 710 172<br>
  Web./ <a href="https://www.frastea.com" style="color:#c8a96e;">www.frastea.com</a><br>
  Add./ 8F, No. 190, Ln. 461, Zhongfeng Rd., Longtan Dist., Taoyuan City 25025, Taiwan (R.O.C.)
</div>
"""

def frastea_ftr(region="East"):
    email = SMTP_USER_E if region.lower() != "west" else SMTP_USER_W
    return FRASTEA_FTR_TEMPLATE.format(email=email)

def product_image_html(product):
    p_style = "font-family:Arial,sans-serif;font-size:14px;color:#333;line-height:1.7;"
    if product == "raw":
        return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0;">
  <tr>
    <td width="45%" style="padding-right:16px;vertical-align:top;">
      <img src="{IMG_RAW}" width="100%" style="border-radius:4px;display:block;" alt="Frastea Raw Materials">
    </td>
    <td width="55%" style="vertical-align:top;">
      <p style="{p_style}"><strong style="color:#c8a96e;">Premium Tea & Herbal Ingredients</strong><br>
      Single-origin teas, botanical blends, and herbal ingredients sourced directly from certified farms. 
      Consistent quality, flexible MOQ, and full traceability.</p>
    </td>
  </tr>
</table>"""
    elif product == "machinery":
        return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0;">
  <tr>
    <td width="45%" style="padding-right:16px;vertical-align:top;">
      <img src="{IMG_MACHINE}" width="100%" style="border-radius:4px;display:block;" alt="Frastea Tea Machine">
    </td>
    <td width="55%" style="vertical-align:top;">
      <p style="{p_style}"><strong style="color:#c8a96e;">Tea & Espresso Equipment</strong><br>
      Commercial-grade brewing systems designed for high-volume operations. 
      Easy maintenance, consistent extraction, and full technical support.</p>
    </td>
  </tr>
</table>"""
    else:  # both
        return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="margin:20px 0;">
  <tr>
    <td width="48%" style="padding-right:8px;vertical-align:top;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td><img src="{IMG_RAW}" width="100%" style="border-radius:4px;display:block;" alt="Raw Materials"></td></tr>
        <tr><td style="padding-top:8px;"><p style="{p_style};font-size:13px;"><strong style="color:#c8a96e;">Premium Ingredients</strong><br>Single-origin teas &amp; botanicals</p></td></tr>
      </table>
    </td>
    <td width="4%"></td>
    <td width="48%" style="padding-left:8px;vertical-align:top;">
      <table width="100%" cellpadding="0" cellspacing="0">
        <tr><td><img src="{IMG_MACHINE}" width="100%" style="border-radius:4px;display:block;" alt="Tea Machine"></td></tr>
        <tr><td style="padding-top:8px;"><p style="{p_style};font-size:13px;"><strong style="color:#c8a96e;">Brewing Equipment</strong><br>Commercial-grade tea systems</p></td></tr>
      </table>
    </td>
  </tr>
</table>"""

def classify_product(industry):
    ind = (industry or "").lower()
    machinery_kw = ["equipment","machine","brew","roast","cafe","coffee","espresso","fitness","gym","hotel","spa","wellness"]
    raw_kw = ["tea","herbal","ingredient","spice","botanical","beverage","drink","bubble","boba","juice"]
    for k in raw_kw:
        if k in ind: return "raw"
    for k in machinery_kw:
        if k in ind: return "machinery"
    return "both"

def build_email(row):
    company  = row.get("company","").strip()
    fname    = (row.get("contact_name","") or "there").strip().split()[0] if (row.get("contact_name","") or "").strip() else "there"
    title    = row.get("title","")
    industry = row.get("industry","")
    product  = classify_product(industry)
    tid      = str(uuid.uuid4())[:8]
    p = "font-family:Arial,sans-serif;font-size:14px;color:#333;line-height:1.7;"

    if product == "raw":
        subject = f"Tea & herbal supply for {company}"
        body_text = f"""
<p style="{p}">Hi {fname},</p>
<p style="{p}">I hope this message finds you well. My name is Elena from Frastea — we specialize in premium tea and herbal ingredients for food &amp; beverage businesses across the US.</p>
<p style="{p}">I came across {company} and thought there might be a good fit. We work with cafes, restaurants, and wellness brands that care about ingredient quality and consistency.</p>
<p style="{p}">We offer:</p>
<ul style="{p}">
  <li>Single-origin teas and herbal blends</li>
  <li>Flexible MOQ with reliable lead times</li>
  <li>Full traceability and quality documentation</li>
  <li>Free samples to try before committing</li>
</ul>
<p style="{p}">Would you be open to a quick call this week to see if there's a fit?</p>"""
    elif product == "machinery":
        subject = f"Brewing equipment built for your operations"
        body_text = f"""
<p style="{p}">Hi {fname},</p>
<p style="{p}">I'm Elena from Frastea. We design and supply commercial tea and brewing equipment for businesses like {company}.</p>
<p style="{p}">Running multiple locations means ingredient consistency is everything — one off-batch and it shows across the board.</p>
<p style="{p}">Our equipment is built for high-volume, low-maintenance operation:</p>
<ul style="{p}">
  <li>Consistent extraction at scale</li>
  <li>Easy staff training and operation</li>
  <li>Full technical support and warranty</li>
  <li>Custom configurations available</li>
</ul>
<p style="{p}">Happy to send specs or arrange a demo — would that be useful?</p>"""
    else:
        subject = f"One partner for ingredients & equipment"
        body_text = f"""
<p style="{p}">Hi {fname},</p>
<p style="{p}">I'm Elena from Frastea. We supply both premium tea ingredients and commercial brewing equipment — so businesses like {company} can source from one trusted partner.</p>
<p style="{p}">Whether you're scaling a menu or upgrading equipment, having aligned quality on both sides makes a real difference.</p>
<p style="{p}">What we offer:</p>
<ul style="{p}">
  <li>Single-origin teas, herbals, and botanical blends</li>
  <li>Commercial brewing systems — reliable, low-maintenance</li>
  <li>Flexible MOQ and consistent supply</li>
  <li>Free samples and equipment demos available</li>
</ul>
<p style="{p}">Would it make sense to connect briefly this week?</p>"""

    return subject, body_text, tid, product

def build_full_email(row, region="East"):
    subject, body_text, tid, product = build_email(row)
    img_html = product_image_html(product)
    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:20px;background:#f4f4f4;">
<div style="max-width:600px;margin:0 auto;">
{FRASTEA_HDR}
{body_text}
{img_html}
{frastea_ftr(region)}
</div>
</body></html>"""
    return subject, html, tid, product

def build_vip_approval(company, subject, region="East"):
    approve_url = f"{BASE_URL}/approve?company={company.replace(' ','%20')}&region={region}"
    sender = SMTP_USER_E if region.lower() != "west" else SMTP_USER_W
    html = f"""<!DOCTYPE html><html><body style="margin:0;padding:20px;background:#f4f4f4;">
<div style="max-width:600px;margin:0 auto;">
{FRASTEA_HDR}
<p style="font-family:Arial,sans-serif;font-size:15px;color:#333;"><strong>[VIP 審核]</strong> 請確認以下開發信是否發送：</p>
<table width="100%" cellpadding="10" style="border:1px solid #e0e0e0;border-radius:4px;margin:16px 0;">
  <tr><td style="font-family:Arial,sans-serif;font-size:13px;color:#555;"><strong>公司：</strong> {company}</td></tr>
  <tr><td style="font-family:Arial,sans-serif;font-size:13px;color:#555;"><strong>主旨：</strong> {subject}</td></tr>
  <tr><td style="font-family:Arial,sans-serif;font-size:13px;color:#555;"><strong>發信帳號：</strong> <span style="font-family:Courier New,monospace;">{sender}</span></td></tr>
</table>
<p style="font-family:Arial,sans-serif;font-size:14px;color:#333;">確認後點擊按鈕，系統將自動發送開發信：</p>
<!--[if mso]>
<v:roundrect xmlns:v="urn:schemas-microsoft-com:vml" href="{approve_url}" style="height:44px;v-text-anchor:middle;width:160px;" arcsize="10%" stroke="f" fillcolor="#27ae60">
<w:anchorlock/>
<center style="color:#ffffff;font-family:Arial,sans-serif;font-size:16px;font-weight:bold;">✓ 確認發送</center>
</v:roundrect>
<![endif]-->
<a href="{approve_url}" style="background:#27ae60;border-radius:4px;color:#fff;display:inline-block;font-family:Arial,sans-serif;font-size:16px;font-weight:bold;line-height:44px;text-align:center;text-decoration:none;width:160px;-webkit-text-size-adjust:none;mso-hide:all;">✓ 確認發送</a>
{frastea_ftr(region)}
</div>
</body></html>"""
    return html

# ── Flask routes ──────────────────────────────────────────────────────────────
PIXEL = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff'
    b'\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00'
    b'\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "KLUB×Frastea Combined v1"})

@app.route("/pixel.gif")
def pixel():
    tid   = request.args.get("tid","")
    email = request.args.get("e","")
    day   = request.args.get("d","")
    month = request.args.get("m","")
    tier  = request.args.get("t","")
    ip    = request.remote_addr
    ua    = request.headers.get("User-Agent","")
    try:
        init_tracker_db()
        con = sqlite3.connect(TRACKER_DB)
        con.execute("INSERT INTO opens(tracking_id,email,day,month,tier,ip,ua) VALUES(?,?,?,?,?,?,?)",
                    (tid,email,day,month,tier,ip,ua))
        con.commit(); con.close()
    except Exception as e:
        log.error(f"pixel: {e}")
    return Response(PIXEL, mimetype="image/gif",
                    headers={"Cache-Control":"no-cache,no-store","Pragma":"no-cache"})

@app.route("/approve")
def approve():
    company = request.args.get("company","").strip()
    region  = request.args.get("region","East")
    if not company:
        return "Missing company", 400
    init_pipeline_db()
    mark_vip_approved(company)
    log.info(f"VIP approved: {company} ({region})")
    # Trigger send_vip in background
    threading.Thread(target=send_vip_for_company, args=(company, region), daemon=True).start()
    return f"""<!DOCTYPE html><html><body style="font-family:Arial;text-align:center;padding:60px;">
<div style="background:#27ae60;color:#fff;padding:30px;border-radius:8px;max-width:400px;margin:0 auto;">
<h2>✓ 已確認</h2>
<p>{company} 的開發信將在 30 秒內發送。</p>
</div></body></html>"""

@app.route("/stats")
def stats():
    key = request.args.get("key","")
    if key != STATS_KEY:
        return "Unauthorized", 401
    try:
        init_tracker_db()
        init_pipeline_db()
        con_t = sqlite3.connect(TRACKER_DB)
        opens = con_t.execute("SELECT COUNT(*) FROM opens").fetchone()[0]
        con_t.close()
        con_p = sqlite3.connect(DB_PATH)
        sent  = con_p.execute("SELECT COUNT(*) FROM sent_log").fetchone()[0]
        by_tier = con_p.execute("SELECT tier, COUNT(*) FROM sent_log GROUP BY tier").fetchall()
        con_p.close()
        return jsonify({"opens":opens,"sent":sent,"by_tier":dict(by_tier)})
    except Exception as e:
        return jsonify({"error":str(e)})

@app.route("/run_pipeline", methods=["POST"])
def run_pipeline_api():
    key = request.headers.get("X-API-Key","")
    if key != STATS_KEY:
        return "Unauthorized", 401
    mode = request.json.get("mode","run")
    threading.Thread(target=run_pipeline, args=(mode,), daemon=True).start()
    return jsonify({"status":"started","mode":mode})

# ── Pipeline logic ────────────────────────────────────────────────────────────
def send_vip_for_company(company, region="East"):
    """Send VIP outreach email for a specific company after approval"""
    time.sleep(5)
    init_pipeline_db()
    if not os.path.exists(CSV_FILE):
        log.error(f"CSV not found: {CSV_FILE}")
        return
    with open(CSV_FILE, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if row.get("company","").strip() == company:
            email   = row.get("email","").strip()
            r       = row.get("region","East")
            subject, html, tid, product = build_full_email(row, r)
            pixel_url = f"{BASE_URL}/pixel.gif?tid={tid}&e={email}&d=Day1&m=2026-06&t=VIP"
            html = html.replace("</body>", f'<img src="{pixel_url}" width="1" height="1" style="display:none;"></body>')
            ok, sender = smtp_send(email, subject, html, r)
            status = "sent" if ok else "fail"
            log_sent(email, company, "VIP", r, subject, status, tid)
            log.info(f"VIP send_after_approval: {company} -> {email} [{status}]")
            break

def run_pipeline(mode="run"):
    """Main pipeline execution"""
    init_pipeline_db()
    if not os.path.exists(CSV_FILE):
        log.error(f"CSV not found: {CSV_FILE}")
        return

    with open(CSV_FILE, encoding="utf-8-sig") as f:
        leads = list(csv.DictReader(f))

    # Fix empty contact names
    for row in leads:
        if not (row.get("contact_name","") or "").strip():
            row["contact_name"] = "there"

    sent_count = 0

    # VIP notifications
    vip_rows = [r for r in leads if r.get("tier","").strip().upper() == "VIP"]
    if vip_rows and mode in ("run", "vip_notify"):
        log.info(f"Sending {len(vip_rows)} VIP approval emails to manager")
        for row in vip_rows:
            company = row.get("company","").strip()
            region  = row.get("region","East")
            subject, html_body, tid, product = build_full_email(row, region)
            approval_html = build_vip_approval(company, subject, region)
            ok, sender = smtp_send(MANAGER_EMAIL, f"[VIP審核] {company} — {subject}", approval_html, region)
            log.info(f"VIP approval -> {MANAGER_EMAIL}: {'OK' if ok else 'FAIL'}")

    if mode == "vip_notify":
        log.info("vip_notify complete")
        return

    # Send outreach emails
    results = []
    for row in leads:
        email   = row.get("email","").strip()
        company = row.get("company","").strip()
        region  = row.get("region","East")
        tier    = row.get("tier","General").strip().upper()

        if not email or "@" not in email:
            continue

        # VIP: only send if approved (or in send_vip/send_all mode)
        if tier == "VIP" and mode == "run":
            log.info(f"  [VIP locked] {company} — waiting for manager approval")
            continue

        if already_sent(email, company):
            log.info(f"  [skip] {company} -> {email} already sent")
            continue

        subject, html, tid, product = build_full_email(row, region)
        pixel_url = f"{BASE_URL}/pixel.gif?tid={tid}&e={email}&d=Day1&m=2026-06&t={tier}"
        html = html.replace("</body>", f'<img src="{pixel_url}" width="1" height="1" style="display:none;"></body>')

        ok, sender = smtp_send(email, subject, html, region)
        status = "sent" if ok else "fail"
        log_sent(email, company, tier, region, subject, status, tid)
        results.append((company, email, status))
        sent_count += 1

        # Rate limiting
        interval = random.uniform(SEND_INTERVAL_MIN, SEND_INTERVAL_MAX) + random.uniform(-SEND_JITTER, SEND_JITTER)
        time.sleep(max(10, interval))
        if sent_count > 0 and sent_count % BATCH_SIZE == 0:
            log.info(f"  [batch pause] {sent_count} sent, pausing {BATCH_PAUSE}s")
            time.sleep(BATCH_PAUSE)

    # Summary report
    ok_count = sum(1 for _,_,s in results if s=="sent")
    fail_count = len(results) - ok_count
    report_html = f"""<!DOCTYPE html><html><body style="font-family:Arial;padding:20px;">
<h2>KLUB x Frastea — 發信報告</h2>
<p>日期: {datetime.date.today().isoformat()} | 模式: {mode}</p>
<p>✅ 成功: {ok_count} | ❌ 失敗: {fail_count} | 總計: {len(results)}</p>
</body></html>"""
    smtp_send(MANAGER_EMAIL, f"[發信報告] {datetime.date.today()} — {ok_count}/{len(results)} 封", report_html)
    log.info(f"Pipeline complete: {ok_count}/{len(results)} sent")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_pipeline_db()
    init_tracker_db()
    port = int(os.environ.get("PORT", 5000))
    log.info(f"Starting KLUB x Frastea Combined Service on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
