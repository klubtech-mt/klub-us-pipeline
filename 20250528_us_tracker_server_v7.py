"""
KLUB x Frastea Tracking Server v7
- /track/<tid>  : 開信追蹤像素
- /approve/<tid>: 主管按 OK → 記錄 approval → 自動觸發發 VIP 開發信
- /health       : 健康檢查
pip install flask pillow requests
"""
from flask import Flask, request, jsonify
import sqlite3, io, os, subprocess, threading
from datetime import datetime
try:
    from PIL import Image
except:
    Image = None

app = Flask(__name__)
DB           = os.getenv("DB_PATH", "/app/us_v25.db")
PIPELINE_PY  = os.getenv("PIPELINE_PY", "/app/20250529_us_pipeline_v29.py")
CSV_FILE     = os.getenv("CSV_FILE", "/app/us_leads_pipeline.csv")

def get_conn():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def trigger_send_vip():
    """背景執行 send_vip"""
    def run():
        try:
            result = subprocess.run(
                ["python3", PIPELINE_PY, "send_vip", CSV_FILE],
                capture_output=True, text=True, timeout=600
            )
            print("[Tracker] send_vip 完成:", result.stdout[-200:])
        except Exception as e:
            print("[Tracker] send_vip 失敗:", e)
    t = threading.Thread(target=run, daemon=True)
    t.start()

@app.route("/track/<track_id>")
def track_open(track_id):
    try:
        conn = get_conn()
        conn.execute(
            "UPDATE sent_log SET open_count=open_count+1 WHERE tracking_id=?",
            (track_id,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print("[Tracker] track error:", e)
    if Image:
        img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        from flask import send_file
        return send_file(buf, mimetype="image/png")
    return "", 204

@app.route("/approve/<track_id>")
def approve(track_id):
    approver = request.args.get("approver", "")
    target   = request.args.get("target", "")
    try:
        conn = get_conn()
        # Mark this tracking ID as approved
        conn.execute(
            "UPDATE sent_log SET status='APPROVED' WHERE tracking_id=?",
            (track_id,)
        )
        row = conn.execute(
            "SELECT company FROM sent_log WHERE tracking_id=?",
            (track_id,)
        ).fetchone()
        conn.commit()
        conn.close()
        company = row["company"] if row else target

        # Trigger send_vip in background
        trigger_send_vip()

        return f"""<html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
</head>
<body style="font-family:Georgia,serif;text-align:center;padding:60px 20px;background:#f5f3ee;">
<div style="background:white;padding:40px;border-radius:12px;max-width:500px;
            margin:0 auto;box-shadow:0 4px 20px rgba(0,0,0,.1);">
  <div style="font-size:48px;">✅</div>
  <h2 style="color:#2e7d32;font-weight:400;letter-spacing:2px;">APPROVED</h2>
  <p style="font-size:15px;color:#333;"><strong>{company}</strong></p>
  <p style="color:#666;font-size:13px;">VIP 開發信已排入發送佇列。</p>
</div>
</body></html>"""
    except Exception as e:
        print("[Tracker] approve error:", e)
        return f"Error: {e}", 500

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "KLUB×Frastea Tracker v7"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
