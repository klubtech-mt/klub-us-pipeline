"""
KLUB × Frastea 美國市場業務開發 Pipeline
版本：20250528_us_pipeline_v9.py
改版內容：
  - SMTP 全部從環境變數讀取，無任何寫死預設值
  - 統一使用 SMTP_SSL (port 465) 或 SMTP+STARTTLS (port 587)，依 SMTP_PORT 自動切換
  - 清除 v6/v7/v8 所有殘留的 SMTP_CONFIG 舊邏輯
  - 其餘功能與 v8 完全一致
"""

import os, csv, sqlite3, smtplib, uuid, datetime, logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── 環境變數（全部必填，無預設值）─────────────────────────
def _require(key):
    v = os.getenv(key, "")
    if not v:
        logging.warning(f"環境變數 {key} 未設定")
    return v

SMTP_HOST         = _require("SMTP_HOST")          # mail.frastea.com
SMTP_PORT         = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER_EAST    = _require("SMTP_USER_EAST")     # mt08@frastea.com
SMTP_PASS_EAST    = _require("SMTP_PASSWORD_EAST")
SMTP_USER_WEST    = _require("SMTP_USER_WEST")     # mt04@frastea.com
SMTP_PASS_WEST    = _require("SMTP_PASSWORD_WEST")
MANAGER_EMAIL     = _require("MANAGER_EMAIL")      # mt08@klubtech.com
TRACKER_URL       = os.getenv("TRACKER_URL", "https://klub-frastea-tracker.zeabur.app")

REGION_ACCOUNTS = {
    "East":  {"user": SMTP_USER_EAST, "password": SMTP_PASS_EAST},
    "West":  {"user": SMTP_USER_WEST, "password": SMTP_PASS_WEST},
    "Default": {"user": SMTP_USER_EAST, "password": SMTP_PASS_EAST},
}

DB_PATH = "us_v9.db"
TODAY   = datetime.date.today().isoformat()

# ── 資料庫 ───────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS sent_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT, company TEXT, tier TEXT, region TEXT,
            subject TEXT, status TEXT, sent_at TEXT,
            tracking_id TEXT, open_count INTEGER DEFAULT 0
        )""")
    con.commit(); con.close()

def already_sent(email):
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT 1 FROM sent_log WHERE email=? AND sent_at LIKE ?",
        (email, TODAY + "%")).fetchone()
    con.close()
    return row is not None

def log_sent(email, company, tier, region, subject, status, tracking_id):
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO sent_log (email,company,tier,region,subject,status,sent_at,tracking_id) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (email, company, tier, region, subject, status,
         datetime.datetime.now().isoformat(), tracking_id))
    con.commit(); con.close()

# ── SMTP 核心（依 port 自動選 SSL 或 STARTTLS）──────────
def smtp_send(to_addr: str, subject: str, html_body: str, region: str = "East") -> bool:
    acct = REGION_ACCOUNTS.get(region, REGION_ACCOUNTS["Default"])
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"KLUB × Frastea <{acct['user']}>"
    msg["To"]      = to_addr
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        if SMTP_PORT == 465:
            conn = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20)
        else:
            conn = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20)
            conn.ehlo()
            conn.starttls()

        conn.ehlo()
        conn.login(acct["user"], acct["password"])
        conn.sendmail(acct["user"], [to_addr], msg.as_string())
        conn.quit()
        print(f"     ✅ 發送成功 ({acct['user']} → {to_addr})")
        return True
    except Exception as e:
        print(f"     ✗ SMTP 失敗 [{region}]: {e}")
        return False

# ── 追蹤像素 ─────────────────────────────────────────────
def pixel_html(tracking_id, email, tier):
    url = f"{TRACKER_URL}/track/{tracking_id}?email={email}&day=Day1&tier={tier}"
    return f'<img src="{url}" width="1" height="1" style="display:none" alt="" />'

def approve_url(tracking_id, target_email):
    return (f"{TRACKER_URL}/approve/{tracking_id}"
            f"?approver={MANAGER_EMAIL}&target={target_email}")

# ── 產品類別判斷 ──────────────────────────────────────────
def detect_product(industry: str) -> str:
    ind = industry.lower()
    raw_kw  = ["f&b","beverage","cafe","restaurant","bubble tea","tea","food","drink"]
    mach_kw = ["fitness","gym","wellness","spa","health club","sport"]
    if any(k in ind for k in raw_kw):    return "raw"
    if any(k in ind for k in mach_kw):   return "machinery"
    return "both"

# ── 郵件模版（6 種）──────────────────────────────────────
def build_email(row: dict) -> tuple[str, str]:
    company  = row["company"]
    fname    = row["contact_name"].split()[0]
    sender   = row.get("sender", SMTP_USER_EAST)
    tier     = row["tier"].strip().upper()
    product  = detect_product(row.get("industry", ""))
    tid      = str(uuid.uuid4())
    pixel    = pixel_html(tid, row["email"], tier)

    sig = f"""
<p style="margin-top:24px;color:#555;font-size:13px;">
  Elena Chiang<br>
  Frastea &nbsp;│&nbsp; +886 963 710 172<br>
  {sender}
</p>"""

    # ── VIP 模版 ──
    if tier == "VIP":
        if product == "raw":
            subject = f"Tea & herbal supply for {company}"
            body = f"""<p>Hi {fname},</p>
<p>I'm reaching out from Frastea — we specialize in premium tea and herbal ingredients
for businesses like {company}.</p>
<p>Our sourcing network covers over 30 origins, with consistent quality and
flexible MOQ for scaling operations. Many of our VIP partners in the US have
significantly reduced their raw material costs while elevating product quality.</p>
<p>Would you be open to a brief 15-minute call to explore if there's a fit?</p>
<p>Best regards,</p>{sig}{pixel}"""

        elif product == "machinery":
            subject = "Brewing equipment built for your operations"
            body = f"""<p>Hi {fname},</p>
<p>I'm reaching out from Frastea — we provide commercial brewing and extraction
equipment tailored for multi-location beverage operations like {company}.</p>
<p>Our equipment line is trusted by VIP partners across the US for its reliability,
ease of maintenance, and support for high-volume output.</p>
<p>Would you have 15 minutes to connect and see if our solutions align with your
current setup?</p>
<p>Best regards,</p>{sig}{pixel}"""

        else:
            subject = f"One partner for ingredients & equipment — {company}"
            body = f"""<p>Hi {fname},</p>
<p>I'm reaching out from Frastea — we offer both premium tea/herbal ingredients
<em>and</em> commercial brewing equipment, making us a single trusted partner
for operations like {company}.</p>
<p>Our VIP clients appreciate the convenience of consolidated supply and the
cost savings from bundled solutions. I'd love to share what's worked well for
similar businesses.</p>
<p>Would a quick 15-minute call work for you this week?</p>
<p>Best regards,</p>{sig}{pixel}"""

    # ── General 模版 ──
    else:
        if product == "raw":
            subject = f"Free sample, tea & herbal ingredients for {company}"
            body = f"""<p>Hi {fname},</p>
<p>I came across {company} and wanted to introduce Frastea — we supply
specialty tea and herbal ingredients to beverage businesses across the US.</p>
<p>We'd love to send over a <strong>free sample pack</strong> so you can
experience the quality firsthand — no commitment needed.</p>
<p>Interested? Just reply and I'll get it arranged.</p>
<p>Best,</p>{sig}{pixel}"""

        elif product == "machinery":
            subject = f"Brewing equipment for {company}, quick question"
            body = f"""<p>Hi {fname},</p>
<p>Quick question — is {company} currently looking to upgrade or expand
brewing/extraction equipment?</p>
<p>We at Frastea supply commercial-grade equipment with strong after-sales
support across the US market. Happy to share specs or arrange a demo.</p>
<p>Worth a 10-minute call?</p>
<p>Best,</p>{sig}{pixel}"""

        else:
            subject = f"Ingredients & equipment for {company}"
            body = f"""<p>Hi {fname},</p>
<p>I wanted to introduce Frastea — we supply both specialty tea/herbal
ingredients and commercial brewing equipment to businesses like {company}.</p>
<p>Whether you're looking to source better ingredients or upgrade your
equipment, we might be a good fit. Happy to send over more details or
schedule a quick call.</p>
<p>Best,</p>{sig}{pixel}"""

    return subject, body, tid, product

# ── VIP 主管確認信 ────────────────────────────────────────
def build_vip_approval(row: dict, subject: str, product: str) -> str:
    approval_tid = str(uuid.uuid4())
    ok_url = approve_url(approval_tid, row["email"])
    pixel  = pixel_html(approval_tid, MANAGER_EMAIL, "VIP_APPROVAL")

    return f"""
<p>VIP 開發信待審核：</p>
<table style="border-collapse:collapse;font-size:14px;margin:12px 0">
  <tr><td style="padding:4px 16px 4px 0;font-weight:bold">公司</td>
      <td>{row['company']}</td></tr>
  <tr><td style="padding:4px 16px 4px 0;font-weight:bold">聯絡人</td>
      <td>{row['contact_name']} / {row.get('title','')}</td></tr>
  <tr><td style="padding:4px 16px 4px 0;font-weight:bold">信箱</td>
      <td>{row['email']}</td></tr>
  <tr><td style="padding:4px 16px 4px 0;font-weight:bold">產品類別</td>
      <td>{product}</td></tr>
  <tr><td style="padding:4px 16px 4px 0;font-weight:bold">主旨</td>
      <td>{subject}</td></tr>
</table>
<p>
  <a href="{ok_url}"
     style="background:#2e7d32;color:#fff;padding:12px 28px;border-radius:6px;
            text-decoration:none;font-size:15px;display:inline-block">
    ✅ OK — 確認發送
  </a>
</p>
{pixel}
"""

# ── 彙整報告 HTML ─────────────────────────────────────────
def build_report(results: list, open_rate: float) -> str:
    total   = len(results)
    success = sum(1 for r in results if r["status"] == "✅")
    fail    = total - success
    vip_ok  = sum(1 for r in results if r["tier"] == "VIP" and r["status"] == "✅")
    gen_ok  = sum(1 for r in results if r["tier"] != "VIP" and r["status"] == "✅")
    vip_n   = sum(1 for r in results if r["tier"] == "VIP")
    gen_n   = total - vip_n

    rows_html = "".join(f"""
    <tr style="border-bottom:1px solid #eee">
      <td style="padding:8px 12px">{r['company']}</td>
      <td style="padding:8px 12px">{r['contact']}</td>
      <td style="padding:8px 12px">{r['tier']}</td>
      <td style="padding:8px 12px;font-size:12px">{r['subject']}</td>
      <td style="padding:8px 12px">{r['email']}</td>
      <td style="padding:8px 12px">{r['status']}</td>
    </tr>""" for r in results)

    return f"""
<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;padding:20px;color:#333">
  <h2 style="color:#1a237e">KLUB × Frastea — 發信彙整報告</h2>
  <p style="color:#666">{TODAY}</p>

  <table style="border-collapse:collapse;margin-bottom:24px">
    <tr><td style="padding:6px 20px 6px 0;font-weight:bold">總發送</td>
        <td>{total} 封</td></tr>
    <tr><td style="padding:6px 20px 6px 0;font-weight:bold">成功</td>
        <td style="color:#2e7d32">{success} 封</td></tr>
    <tr><td style="padding:6px 20px 6px 0;font-weight:bold">失敗</td>
        <td style="color:#c62828">{fail} 封</td></tr>
    <tr><td style="padding:6px 20px 6px 0;font-weight:bold">VIP</td>
        <td>{vip_n} 封（成功 {vip_ok}）</td></tr>
    <tr><td style="padding:6px 20px 6px 0;font-weight:bold">General</td>
        <td>{gen_n} 封（成功 {gen_ok}）</td></tr>
    <tr><td style="padding:6px 20px 6px 0;font-weight:bold">開信率</td>
        <td>{open_rate:.1%}</td></tr>
  </table>

  <table style="border-collapse:collapse;width:100%;font-size:14px">
    <tr style="background:#1a237e;color:#fff">
      <th style="padding:10px 12px;text-align:left">公司</th>
      <th style="padding:10px 12px;text-align:left">聯絡人</th>
      <th style="padding:10px 12px;text-align:left">等級</th>
      <th style="padding:10px 12px;text-align:left">主旨</th>
      <th style="padding:10px 12px;text-align:left">收件信箱</th>
      <th style="padding:10px 12px;text-align:left">狀態</th>
    </tr>
    {rows_html}
  </table>
</body></html>"""

# ── 主流程 ────────────────────────────────────────────────
def process_leads(csv_file: str, dry_run: bool = False):
    init_db()

    with open(csv_file, encoding="utf-8-sig") as f:
        leads = list(csv.DictReader(f))

    print(f"\n載入 {len(leads)} 筆名單")
    print("=" * 60)

    vip_rows = []
    results  = []

    # ── 預覽 ──
    for row in leads:
        tier    = row.get("tier", "General").strip().upper()
        subject, body, tid, product = build_email(row)
        tag = "VIP    " if tier == "VIP" else "General"
        print(f"  [{tag}] {row['company']:<30} | {row.get('industry',''):<15} → {product}")
        print(f"           主旨: {subject}")
        if tier == "VIP":
            vip_rows.append((row, subject, body, tid, product))

    if dry_run:
        print("\n(dry_run 模式，未寫入 DB 也未發信)")
        return

    # ── VIP 主管確認信 ──
    if vip_rows:
        print(f"\n[VIP 確認信] 寄送主管審核...")
        print(f"  寄送 {len(vip_rows)} 封 VIP 確認信給主管...")
        for row, subject, body, tid, product in vip_rows:
            region = row.get("region", "East")
            approval_html = build_vip_approval(row, subject, product)
            ok = smtp_send(
                MANAGER_EMAIL,
                f"[VIP審核] {row['company']} — {subject}",
                approval_html,
                region
            )
            status = "✅" if ok else "✗"
            print(f"  {status} {row['company']} → 主管 {MANAGER_EMAIL} ({region})")

    # ── 發開發信 ──
    print(f"\n[發信] 發送今日信件...")
    print(f"[{TODAY}] 待發 {len(leads)} 封")

    for row in leads:
        email  = row["email"]
        region = row.get("region", "East")
        tier   = row.get("tier", "General").strip().upper()

        if already_sent(email):
            print(f"  [已發過] {row['company']} → {email} 跳過")
            continue

        subject, body, tid, product = build_email(row)
        pad = f"  [{'VIP    ' if tier == 'VIP' else 'General'}] {row['company']:<30} → {email} ... "
        print(pad, end="", flush=True)

        ok = smtp_send(email, subject, body, region)
        status = "✅" if ok else "✗"
        print(status)

        log_sent(email, row["company"], tier, region, subject, status, tid)
        results.append({
            "company": row["company"],
            "contact": row.get("contact_name", ""),
            "tier": tier,
            "subject": subject,
            "email": email,
            "status": status,
        })

    # ── 開信率 ──
    con = sqlite3.connect(DB_PATH)
    total_sent = con.execute("SELECT COUNT(*) FROM sent_log WHERE status='✅'").fetchone()[0]
    opened     = con.execute("SELECT COUNT(*) FROM sent_log WHERE open_count > 0").fetchone()[0]
    con.close()
    open_rate = opened / total_sent if total_sent else 0

    # ── 彙整報告 ──
    print(f"\n[彙整報告] 寄送發信報告給主管...")
    report_html = build_report(results, open_rate)
    ok = smtp_send(MANAGER_EMAIL, f"[發信報告] {TODAY} KLUB×Frastea US Pipeline", report_html, "East")
    print(f"{'✅ 彙整報告已寄出' if ok else '✗ 報告寄送失敗'} → {MANAGER_EMAIL}")


# ── 入口 ─────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    mode     = sys.argv[1] if len(sys.argv) > 1 else "demo"
    csv_file = sys.argv[2] if len(sys.argv) > 2 else "test_leads.csv"

    if mode == "demo":
        process_leads(csv_file, dry_run=True)
    elif mode == "run":
        process_leads(csv_file, dry_run=False)
    else:
        print("用法: python 20250528_us_pipeline_v9.py [demo|run] [csv檔案]")
