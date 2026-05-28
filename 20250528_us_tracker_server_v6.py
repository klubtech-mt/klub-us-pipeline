"""
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
DB  = os.getenv("DB_PATH", "us_v6.db")

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
    return {"status": "ok", "service": "KLUB×Frastea Tracker v6"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
