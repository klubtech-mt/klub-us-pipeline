#!/usr/bin/env python3
"""KLUB x Frastea Tracker + VIP Auto-Send Server v9
- Uses exact email templates from 20250529_us_pipeline_v29.py
- Sends VIP outreach after manager approval
- Rate limiting: 20-30s interval, ±5s jitter, 120/hr, 50 batch, Taiwan 20:00-23:00, no weekends
"""
import os, csv, sqlite3, smtplib, uuid, datetime, logging, time, random, threading, base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, request, Response, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
app = Flask(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SMTP_HOST    = os.environ.get("SMTP_HOST", "mail.frastea.com")
SMTP_PORT    = int(os.environ.get("SMTP_PORT", "465"))
SMTP_E_USER  = os.environ.get("SMTP_USER_EAST", "mt08@frastea.com")
SMTP_E_PASS  = os.environ.get("SMTP_PASSWORD_EAST", "")
SMTP_W_USER  = os.environ.get("SMTP_USER_WEST", "mt04@frastea.com")
SMTP_W_PASS  = os.environ.get("SMTP_PASSWORD_WEST", "")
MANAGER_EMAIL = os.environ.get("MANAGER_EMAIL", "mt08@frastea.com")
CSV_FILE     = os.environ.get("CSV_FILE", "/app/us_leads_pipeline.csv")
TRACKER_URL  = os.environ.get("TRACKER_URL", "https://klub-us-tracker.zeabur.app")
STATS_KEY    = os.environ.get("STATS_KEY", "klub2025")
DB_PATH      = "/app/tracker.db"

# Rate limiting
SEND_INTERVAL_MIN = 20
SEND_INTERVAL_MAX = 30
SEND_JITTER       = 5
HOURLY_LIMIT      = 120
BATCH_SIZE        = 50
BATCH_PAUSE       = 300
SEND_HOUR_START   = 12   # UTC 12 = Taiwan 20:00
SEND_HOUR_END     = 15   # UTC 15 = Taiwan 23:00

IMG_MACHINE = "https://drive.google.com/uc?export=view&id=1zOB7-L6cc13a1NroonXlQ6dqKKEmDGxi"
IMG_RAW     = "https://drive.google.com/uc?export=view&id=1dmEHC5s4TpDlKqytzHRo41s2nitHYdkd"

PIXEL_GIF = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff'
    b'\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00'
    b'\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
)

# ── DB ────────────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS opens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tracking_id TEXT, email TEXT, day TEXT, month TEXT,
        tier TEXT, opened_at TEXT, ip TEXT, ua TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS approvals (
        company TEXT PRIMARY KEY, approved_at TEXT, region TEXT, sent INTEGER DEFAULT 0
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS vip_sent (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company TEXT, email TEXT, sent_at TEXT, status TEXT
    )""")
    con.commit(); con.close()

# ── Rate limiting ─────────────────────────────────────────────────────────────
_sent_this_hour = 0
_hour_start     = None
_lock           = threading.Lock()

def in_send_window():
    now = datetime.datetime.utcnow()
    if now.weekday() >= 5:
        return False, "週末暫停"
    if not (SEND_HOUR_START <= now.hour < SEND_HOUR_END):
        tw = (now.hour + 8) % 24
        return False, f"台灣時間 {tw:02d}:{now.minute:02d}，發信時段 20:00-23:00"
    return True, "ok"

def rate_wait(count):
    global _sent_this_hour, _hour_start
    with _lock:
        now = time.time()
        if _hour_start is None:
            _hour_start = now; _sent_this_hour = 0
        if now - _hour_start >= 3600:
            _hour_start = now; _sent_this_hour = 0
        if _sent_this_hour >= HOURLY_LIMIT:
            wait = 3600 - (now - _hour_start)
            log.info(f"Rate limit, waiting {wait:.0f}s")
            time.sleep(wait + 5)
            _hour_start = time.time(); _sent_this_hour = 0
        if count > 0 and count % BATCH_SIZE == 0:
            log.info(f"Batch pause {BATCH_PAUSE}s")
            time.sleep(BATCH_PAUSE)
        interval = random.uniform(SEND_INTERVAL_MIN, SEND_INTERVAL_MAX) + random.uniform(-SEND_JITTER, SEND_JITTER)
        _sent_this_hour += 1
    time.sleep(max(10, interval))

# ── SMTP ──────────────────────────────────────────────────────────────────────
def smtp_send(to, subject, html, region="East"):
    user = SMTP_E_USER if region.lower() != "west" else SMTP_W_USER
    pwd  = SMTP_E_PASS if region.lower() != "west" else SMTP_W_PASS
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
        log.error(f"SMTP fail [{region}]: {e}")
        return False, str(e)

# ── Email templates (from v29) ────────────────────────────────────────────────
HDR = ("<div style='max-width:600px;margin:0 auto;font-family:Georgia,serif'>"
       "<div style='background:#1a1a1a;padding:22px 32px'>"
       "<h1 style='color:#c9b97a;font-size:16px;font-weight:400;letter-spacing:4px;margin:0;font-family:Georgia,serif'>FRASTEA</h1>"
       "</div>"
       "<div style='padding:32px;background:#fff;border:1px solid #e8e8e8;border-top:none'>")

def sig(sender_email):
    return (f"<div style='margin-top:28px;padding-top:20px;border-top:1px solid #e8e8e8'>"
            f"<p style='font-size:13px;color:#555;margin:0;line-height:1.8;font-family:Arial,sans-serif'>"
            f"Elena Chiang<br>"
            f"Frastea Co. Ltd.<br>"
            f"Email./ <span style='font-family:Courier New,monospace'>{sender_email}</span><br>"
            f"Tel./ +886 963 710 172<br>"
            f"Web./ <a href='https://www.frastea.com' style='color:#c9b97a'>www.frastea.com</a><br>"
            f"Add./ 8F, No. 190, Ln. 461, Zhongfeng Rd., Longtan Dist., Taoyuan City 25025, Taiwan (R.O.C.)"
            f"</p></div></div></div>")

def banner(product):
    p_style = "font-size:13px;line-height:1.7;color:#333;margin:8px 0 0;font-family:Arial,sans-serif"
    if product == "raw":
        return (f"<div style='margin:24px 0'>"
                f"<table width='100%' cellpadding='0' cellspacing='0'><tr>"
                f"<td width='45%' style='padding-right:14px;vertical-align:top'>"
                f"<img src='{IMG_RAW}' width='100%' style='border-radius:4px;display:block' alt='Frastea Ingredients'>"
                f"</td><td width='55%' style='vertical-align:top'>"
                f"<p style='font-size:13px;font-weight:700;color:#c9b97a;margin:0;font-family:Arial,sans-serif'>Premium Tea &amp; Herbal Ingredients</p>"
                f"<p style='{p_style}'>Single-origin teas, botanical blends sourced from certified farms. Consistent quality, flexible MOQ, full traceability.</p>"
                f"</td></tr></table></div>")
    elif product == "machinery":
        return (f"<div style='margin:24px 0'>"
                f"<table width='100%' cellpadding='0' cellspacing='0'><tr>"
                f"<td width='45%' style='padding-right:14px;vertical-align:top'>"
                f"<img src='{IMG_MACHINE}' width='100%' style='border-radius:4px;display:block' alt='KLUB Equipment'>"
                f"</td><td width='55%' style='vertical-align:top'>"
                f"<p style='font-size:13px;font-weight:700;color:#c9b97a;margin:0;font-family:Arial,sans-serif'>Tea &amp; Espresso Equipment</p>"
                f"<p style='{p_style}'>Commercial-grade brewing systems for high-volume operations. Easy maintenance, consistent extraction, full technical support.</p>"
                f"</td></tr></table></div>")
    else:
        return (f"<div style='margin:24px 0'>"
                f"<table width='100%' cellpadding='0' cellspacing='0'><tr>"
                f"<td width='48%' style='padding-right:8px;vertical-align:top'>"
                f"<img src='{IMG_RAW}' width='100%' style='border-radius:4px;display:block'>"
                f"<p style='font-size:12px;font-weight:700;color:#c9b97a;margin:6px 0 2px;font-family:Arial,sans-serif'>Premium Ingredients</p>"
                f"<p style='{p_style}'>Single-origin teas &amp; botanicals</p>"
                f"</td><td width='4%'></td>"
                f"<td width='48%' style='padding-left:8px;vertical-align:top'>"
                f"<img src='{IMG_MACHINE}' width='100%' style='border-radius:4px;display:block'>"
                f"<p style='font-size:12px;font-weight:700;color:#c9b97a;margin:6px 0 2px;font-family:Arial,sans-serif'>Brewing Equipment</p>"
                f"<p style='{p_style}'>Commercial-grade tea systems</p>"
                f"</td></tr></table></div>")

def pixel_html(tid, email, tier):
    url = f"{TRACKER_URL}/pixel.gif?tid={tid}&e={email}&d=Day1&m=2026-06&t={tier}"
    return f'<img src="{url}" width="1" height="1" style="display:none">'

def detect_product(industry):
    ind = (industry or "").lower()
    for k in ["tea","herbal","ingredient","spice","botanical","beverage","drink","bubble","boba","juice","raw"]:
        if k in ind: return "raw"
    for k in ["equipment","machine","brew","roast","cafe","coffee","espresso","fitness","gym","hotel","spa","wellness"]:
        if k in ind: return "machinery"
    return "both"

def build_email(row):
    company = row.get("company","").strip()
    cn      = str(row.get("contact_name","") or "").strip()
    fname   = cn.split()[0] if cn else "there"
    sender  = row.get("sender", SMTP_E_USER) or SMTP_E_USER
    tier    = row.get("tier","General").strip().upper()
    product = detect_product(row.get("industry",""))
    tid     = str(uuid.uuid4())
    pixel   = pixel_html(tid, row.get("email",""), tier)
    p  = "font-size:14px;line-height:1.9;color:#333;margin:0 0 14px;font-family:Georgia,serif"
    pl = "font-size:14px;line-height:1.9;color:#333;margin:0;font-family:Georgia,serif"
    b  = banner(product)
    s  = sig(sender)

    if tier == "VIP":
        if product == "raw":
            subject = f"Tea & herbal supply for {company}"
            body = (f"{HDR}<p style='{p}'>Hi {fname},</p>"
                    f"<p style='{p}'>This is Elena from Frastea Co. Ltd. from Taiwan.</p>"
                    f"<p style='{p}'>Running multiple locations means ingredient consistency is everything — one off-batch and it shows across the board.</p>"
                    f"<p style='{p}'>We're Frastea, and we supply premium tea leaves and herbal ingredients to multi-location beverage chains across the US.</p>"
                    f"<p style='{p}'>A few groups your size have made the switch and haven't looked back.</p>"
                    f"<p style='{pl}'>We can arrange a short discussion or meeting on how we can support your operation.</p>"
                    f"{b}{s}{pixel}</div>")
        elif product == "machinery":
            subject = "Brewing equipment built for your operations"
            body = (f"{HDR}<p style='{p}'>Hi {fname},</p>"
                    f"<p style='{p}'>This is Elena from Frastea Co. Ltd. from Taiwan.</p>"
                    f"<p style='{p}'>When equipment goes down at one of your locations, it's not just that store that feels it.</p>"
                    f"<p style='{p}'>KLUB Technology builds commercial brewing machines for high-volume chains — designed for reliability and easy to standardize across locations.</p>"
                    f"<p style='{p}'>Happy to share what that looks like in practice.</p>"
                    f"<p style='{pl}'>We can arrange a short discussion or meeting on how we can support your operation.</p>"
                    f"{b}{s}{pixel}</div>")
        else:
            subject = "One partner for ingredients & equipment"
            body = (f"{HDR}<p style='{p}'>Hi {fname},</p>"
                    f"<p style='{p}'>This is Elena from Frastea Co. Ltd. from Taiwan.</p>"
                    f"<p style='{p}'>Most chains in your company size are managing separate vendors for ingredients and equipment — which works, until it doesn't.</p>"
                    f"<p style='{p}'>Frastea and KLUB Technology are sister companies offering premium tea &amp; herbal ingredients alongside the commercial brewing machines to brew them right.</p>"
                    f"<p style='{p}'>One partner, end to end.</p>"
                    f"<p style='{pl}'>We can arrange a short discussion or meeting on how we can support your operation.</p>"
                    f"{b}{s}{pixel}</div>")
    else:
        if product == "raw":
            subject = f"Free sample, tea & herbal ingredients for {company}"
            body = (f"{HDR}<p style='{p}'>Hi {fname},</p>"
                    f"<p style='{p}'>This is Elena from Frastea Co. Ltd. from Taiwan.</p>"
                    f"<p style='{p}'>We supply premium tea leaves and herbal ingredients to cafes and beverage shops across the US.</p>"
                    f"<p style='{p}'>If you're ever looking for a reliable source or just want to try something new on your menu, we'd love to send over a sample. No commitment.</p>"
                    f"<p style='{pl}'>We can arrange a short discussion or meeting on how we can support your operation.</p>"
                    f"{b}{s}{pixel}</div>")
        elif product == "machinery":
            subject = f"Brewing equipment for {company}, quick question"
            body = (f"{HDR}<p style='{p}'>Hi {fname},</p>"
                    f"<p style='{p}'>This is Elena from Frastea Co. Ltd. from Taiwan.</p>"
                    f"<p style='{p}'>We make commercial brewing machines for cafes and beverage shops that need something reliable and easy to run day-to-day.</p>"
                    f"<p style='{p}'>If your current setup ever gives you trouble, or you're thinking about expanding, we'd love to show you what we have.</p>"
                    f"<p style='{pl}'>We can arrange a short discussion or meeting on how we can support your operation.</p>"
                    f"{b}{s}{pixel}</div>")
        else:
            subject = f"Ingredients & equipment for {company}"
            body = (f"{HDR}<p style='{p}'>Hi {fname},</p>"
                    f"<p style='{p}'>This is Elena from Frastea Co. Ltd. from Taiwan.</p>"
                    f"<p style='{p}'>Frastea and KLUB Technology are sister companies — we handle premium tea &amp; herbal ingredients and the brewing machines to go with them.</p>"
                    f"<p style='{p}'>If you're sourcing either right now (or just open to exploring), we'd love to connect. Happy to send samples or a quick overview, whichever is more useful.</p>"
                    f"<p style='{pl}'>We can arrange a short discussion or meeting on how we can support your operation.</p>"
                    f"{b}{s}{pixel}</div>")

    return subject, body, tid, product

# ── VIP send logic ────────────────────────────────────────────────────────────
def send_vip_email(company, region):
    ok_window, reason = in_send_window()
    if not ok_window:
        log.info(f"Outside send window ({reason}), waiting...")
        while True:
            time.sleep(300)
            ok_window, reason = in_send_window()
            if ok_window: break
            log.info(f"Still waiting: {reason}")

    if not os.path.exists(CSV_FILE):
        log.error(f"CSV not found: {CSV_FILE}"); return

    with open(CSV_FILE, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        if row.get("company","").strip() == company:
            email = row.get("email","").strip()
            if not email or "@" not in email:
                log.error(f"Invalid email for {company}"); return
            subject, html, tid, product = build_email(row)
            ok, sender = smtp_send(email, subject, html, region)
            status = "sent" if ok else "fail"
            try:
                con = sqlite3.connect(DB_PATH)
                con.execute("INSERT INTO vip_sent(company,email,sent_at,status) VALUES(?,?,datetime('now'),?)",
                            (company, email, status))
                con.execute("UPDATE approvals SET sent=1 WHERE company=?", (company,))
                con.commit(); con.close()
            except Exception as e:
                log.error(f"DB error: {e}")
            log.info(f"VIP outreach {company} -> {email}: {status}")
            return
    log.error(f"Company not found: {company}")

# ── Flask routes ──────────────────────────────────────────────────────────────
@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "KLUB×Frastea Tracker v9"})

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
        init_db()
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT INTO opens(tracking_id,email,day,month,tier,opened_at,ip,ua) VALUES(?,?,?,?,?,datetime('now'),?,?)",
                    (tid,email,day,month,tier,ip,ua))
        con.commit(); con.close()
    except Exception as e:
        log.error(f"pixel error: {e}")
    return Response(PIXEL_GIF, mimetype="image/gif",
                    headers={"Cache-Control":"no-cache,no-store","Pragma":"no-cache"})

@app.route("/approve")
def approve():
    company = request.args.get("company","").strip()
    region  = request.args.get("region","East")
    if not company:
        return "Missing company", 400
    try:
        init_db()
        con = sqlite3.connect(DB_PATH)
        con.execute("INSERT OR REPLACE INTO approvals(company,approved_at,region,sent) VALUES(?,datetime('now'),?,0)",
                    (company, region))
        con.commit(); con.close()
        log.info(f"VIP approved: {company} ({region})")
        threading.Thread(target=send_vip_email, args=(company, region), daemon=True).start()
    except Exception as e:
        log.error(f"approve error: {e}")
        return f"Error: {e}", 500
    return f"""<!DOCTYPE html><html>
<head><meta charset="utf-8"><title>已確認</title></head>
<body style="margin:0;padding:40px;background:#f4f4f4;font-family:Arial,sans-serif;text-align:center;">
<div style="background:#27ae60;color:#fff;padding:30px 40px;border-radius:8px;max-width:420px;margin:0 auto;">
  <div style="font-size:48px;margin-bottom:12px;">✓</div>
  <h2 style="margin:0 0 10px;">已確認發送</h2>
  <p style="margin:0;opacity:0.9;">{company} 的開發信將在發信時段內自動寄出。</p>
</div>
</body></html>"""

@app.route("/stats")
def stats():
    key = request.args.get("key","")
    if key != STATS_KEY:
        return "Unauthorized", 401
    try:
        init_db()
        con = sqlite3.connect(DB_PATH)
        opens     = con.execute("SELECT COUNT(*) FROM opens").fetchone()[0]
        approvals = con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        vip_sent  = con.execute("SELECT COUNT(*) FROM vip_sent WHERE status='sent'").fetchone()[0]
        pending   = con.execute("SELECT COUNT(*) FROM approvals WHERE sent=0").fetchone()[0]
        con.close()
        return jsonify({"opens":opens,"approvals":approvals,"vip_sent":vip_sent,"pending":pending})
    except Exception as e:
        return jsonify({"error":str(e)})

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    log.info(f"Tracker v9 starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
