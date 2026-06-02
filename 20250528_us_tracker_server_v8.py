#!/usr/bin/env python3
"""KLUB x Frastea Tracker Server v8 - Fixed /approve route"""
import os, sqlite3, datetime, logging
from flask import Flask, request, Response, jsonify

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

DB_PATH  = os.environ.get("TRACKER_DB", "/app/tracker.db")
STATS_KEY = os.environ.get("STATS_KEY", "klub2025")

PIXEL = (
    b'\x47\x49\x46\x38\x39\x61\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff'
    b'\x00\x00\x00\x21\xf9\x04\x00\x00\x00\x00\x00\x2c\x00\x00\x00\x00'
    b'\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b'
)

def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS opens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tracking_id TEXT, email TEXT, day TEXT, month TEXT,
        tier TEXT, opened_at TEXT DEFAULT (datetime('now')),
        ip TEXT, ua TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS approvals (
        company TEXT PRIMARY KEY,
        approved_at TEXT DEFAULT (datetime('now')),
        region TEXT
    )""")
    con.commit(); con.close()

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "KLUB×Frastea Tracker v8"})

@app.route("/pixel.gif")
def pixel():
    tid   = request.args.get("tid", "")
    email = request.args.get("e", "")
    day   = request.args.get("d", "")
    month = request.args.get("m", "")
    tier  = request.args.get("t", "")
    ip    = request.remote_addr
    ua    = request.headers.get("User-Agent", "")
    try:
        init_db()
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT INTO opens(tracking_id,email,day,month,tier,ip,ua) VALUES(?,?,?,?,?,?,?)",
            (tid, email, day, month, tier, ip, ua)
        )
        con.commit(); con.close()
        log.info(f"open: {email} {tid}")
    except Exception as e:
        log.error(f"pixel error: {e}")
    return Response(PIXEL, mimetype="image/gif",
                    headers={"Cache-Control": "no-cache,no-store", "Pragma": "no-cache"})

@app.route("/approve")
def approve():
    company = request.args.get("company", "").strip()
    region  = request.args.get("region", "East")
    if not company:
        return "Missing company parameter", 400
    try:
        init_db()
        con = sqlite3.connect(DB_PATH)
        con.execute(
            "INSERT OR REPLACE INTO approvals(company, approved_at, region) VALUES(?, datetime('now'), ?)",
            (company, region)
        )
        con.commit(); con.close()
        log.info(f"VIP approved: {company} ({region})")
    except Exception as e:
        log.error(f"approve error: {e}")
    return f"""<!DOCTYPE html><html>
<head><meta charset="utf-8"><title>已確認</title></head>
<body style="margin:0;padding:40px;background:#f4f4f4;font-family:Arial,sans-serif;text-align:center;">
<div style="background:#27ae60;color:#fff;padding:30px 40px;border-radius:8px;max-width:420px;margin:0 auto;">
  <div style="font-size:48px;margin-bottom:12px;">✓</div>
  <h2 style="margin:0 0 10px;">已確認發送</h2>
  <p style="margin:0;opacity:0.9;">{company} 的開發信已記錄，將在下次發信批次中發出。</p>
</div>
</body></html>"""

@app.route("/approvals")
def list_approvals():
    key = request.args.get("key", "")
    if key != STATS_KEY:
        return "Unauthorized", 401
    try:
        init_db()
        con = sqlite3.connect(DB_PATH)
        rows = con.execute("SELECT company, approved_at, region FROM approvals ORDER BY approved_at DESC").fetchall()
        con.close()
        return jsonify([{"company": r[0], "approved_at": r[1], "region": r[2]} for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/stats")
def stats():
    key = request.args.get("key", "")
    if key != STATS_KEY:
        return "Unauthorized", 401
    try:
        init_db()
        con = sqlite3.connect(DB_PATH)
        opens     = con.execute("SELECT COUNT(*) FROM opens").fetchone()[0]
        approvals = con.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
        by_tier   = con.execute("SELECT tier, COUNT(*) FROM opens GROUP BY tier").fetchall()
        con.close()
        return jsonify({"opens": opens, "approvals": approvals, "by_tier": dict(by_tier)})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    log.info(f"Tracker v8 starting on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
