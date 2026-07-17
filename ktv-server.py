#!/usr/bin/env python3
import os, sys, json, sqlite3, subprocess, threading, time, socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path
from datetime import datetime
from socketserver import ThreadingMixIn

PORT = 3456
KTV_DIR = Path(r"H:\KTVSong")
DB_PATH = KTV_DIR / "song_V3.2.3.db"
SCRIPT_DIR = Path(__file__).parent
song_queue = []; queue_counter = 0; current_song_id = None
play_count = {}; play_lock = threading.Lock()

def get_db():
    if not DB_PATH.exists(): return None
    db = sqlite3.connect(str(DB_PATH)); db.row_factory = sqlite3.Row; return db

def search_songs(q):
    db = get_db()
    if not db: return []
    try:
        if not q or not q.strip(): rows = db.execute("SELECT * FROM song LIMIT 50").fetchall()
        else:
            q2 = f"%{q.strip()}%"
            rows = db.execute("SELECT * FROM song WHERE name LIKE ? OR singer_names LIKE ? OR acronym LIKE ? LIMIT 100", (q2, q2, q2)).fetchall()
        db.close(); return [dict(r) for r in rows]
    except: db.close(); return []

def get_song(sid):
    db = get_db()
    if not db: return None
    try:
        row = db.execute("SELECT * FROM song WHERE id = ?", (sid,)).fetchone()
        db.close(); return dict(row) if row else None
    except: db.close(); return None

def find_video(fn):
    ktv = str(KTV_DIR)
    for d in os.listdir(ktv):
        sub = os.path.join(ktv, d)
        if not os.path.isdir(sub) or not d.startswith('song'): continue
        for f in os.listdir(sub):
            fp = os.path.join(sub, f)
            if os.path.isfile(fp) and f == str(fn): return fp
    return None

def add_to_queue(sid, nk="手机点歌"):
    global queue_counter; s = get_song(sid)
    with play_lock:
        queue_counter += 1
        e = {"id": queue_counter, "song_id": sid, "title": s.get("name","未知") if s else "未知", "artist": s.get("singer_names","未知歌手") if s else "未知歌手", "nickname": nk, "status": "waiting", "time": time.time()}
        song_queue.append(e)
        if len(song_queue) == 1: e["status"] = "playing"; global current_song_id; current_song_id = sid; play_count[sid] = play_count.get(sid, 0) + 1
        return e["id"]

def rm_from_queue(qid):
    with play_lock:
        for i, e in enumerate(song_queue):
            if e["id"] == qid:
                if e["status"] == "playing": global current_song_id; current_song_id = None
                song_queue.pop(i); return True
    return False

def skip():
    with play_lock:
        for i, e in enumerate(song_queue):
            if e["status"] == "playing":
                e["status"] = "finished"; global current_song_id; current_song_id = None
                for j in range(i+1, len(song_queue)):
                    if song_queue[j]["status"] == "waiting": song_queue[j]["status"] = "playing"; current_song_id = song_queue[j]["song_id"]; play_count[current_song_id] = play_count.get(current_song_id, 0) + 1; break
                return True
    return False

def get_queue():
    with play_lock: return [dict(e) for e in song_queue]

def get_now():
    with play_lock:
        for e in song_queue:
            if e["status"] == "playing": return {"song_id": e["song_id"], "title": e["title"], "artist": e["artist"]}
    return None

class Th(ThreadingMixIn, HTTPServer): pass

class H(BaseHTTPRequestHandler):
    def log_message(self, f, *a):
        if "/api/" in a[0]: print(f"  [{datetime.now().strftime('%H:%M:%S')}] {a[0]}")
    def _j(self, d, s=200):
        self.send_response(s); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers(); self.wfile.write(json.dumps(d, ensure_ascii=False).encode())
    def _b(self):
        l = int(self.headers.get('Content-Length', 0)); return json.loads(self.rfile.read(l).decode()) if l > 0 else {}
    def do_OPTIONS(self):
        self.send_response(200); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers()
    def do_GET(self):
        p = urlparse(self.path).path; q = parse_qs(urlparse(self.path).query)
        if p == "/api/songs":
            r = search_songs(q.get("q", [""])[0]); self._j([{"id": x["id"], "title": x.get("name","未知"), "artist": x.get("singer_names","未知歌手")} for x in r])
        elif p.startswith("/api/songs/") and len(p.split("/")) == 4:
            s = get_song(int(p.split("/")[3]))
            if s: self._j({"id": s["id"], "title": s.get("name","未知"), "artist": s.get("singer_names","未知歌手"), "file_number": s.get("number", s["id"])})
            else: self._j({"error": "不存在"}, 404)
        elif p == "/api/queue": self._j([{"queue_id": e["id"], "song_id": e["song_id"], "title": e["title"], "artist": e["artist"], "nickname": e["nickname"], "status": e["status"]} for e in get_queue()])
        elif p == "/api/now-playing": self._j(get_now())
        elif p.startswith("/api/stream/"):
            sid = p.split("/")[3]
            s = get_song(sid)
            if not s: self._j({"error": "不存在"}, 404); return
            fn = s.get("number", s["id"]); fp = find_video(fn)
            if not fp: self._j({"error": f"视频不存在"}, 404); return
            # Stream MP4 with ffmpeg - pipe directly to browser
            mode = q.get("mode", ["original"])[0]
            track = 0 if mode == "original" else 1
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            cmd = ["ffmpeg", "-i", fp, "-map", "0:v:0", "-c:v", "copy", "-map", f"0:a:{track}", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-movflags", "+frag_keyframe+empty_moov", "-f", "mp4", "-loglevel", "error", "pipe:1"]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                while True:
                    chunk = proc.stdout.read(65536)
                    if not chunk: break
                    try: self.wfile.write(chunk)
                    except: break
            finally: proc.kill()
        elif p in ("/", "/tv"): self._send_file(str(SCRIPT_DIR / "public" / "tv.html"))
        elif p == "/m": self._send_file(str(SCRIPT_DIR / "public" / "m.html"))
        else: self._j({"error": "Not found"}, 404)
    def _send_file(self, fp):
        m = {".html": "text/html; charset=utf-8", ".js": "application/javascript", ".css": "text/css"}
        e = os.path.splitext(fp)[1].lower(); mt = m.get(e, "application/octet-stream")
        try:
            with open(fp, 'rb') as f: c = f.read()
            self.send_response(200); self.send_header("Content-Type", mt); self.send_header("Content-Length", str(len(c))); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(c)
        except: self._j({"error": "Not found"}, 404)
    def do_POST(self):
        p = urlparse(self.path).path
        if p == "/api/queue":
            b = self._b(); sid = b.get("song_id"); nk = b.get("nickname", "手机点歌")
            if not sid: self._j({"error": "缺少 song_id"}, 400); return
            self._j({"success": True, "queue_id": add_to_queue(sid, nk)})
        elif p == "/api/playback/ended": skip(); self._j({"success": True})
        elif p == "/api/voice/switch":
            b = self._b(); m = b.get("mode", "original")
            if m not in ("original", "accompaniment"): self._j({"error": "mode错误"}, 400); return
            self._j({"success": True, "mode": m})
        else: self._j({"error": "Not found"}, 404)
    def do_DELETE(self):
        p = urlparse(self.path).path
        if p.startswith("/api/queue/"): rm_from_queue(int(p.split("/")[3])); self._j({"success": True})
        else: self._j({"error": "Not found"}, 404)

def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]
    except: ip = "127.0.0.1"
    finally: s.close(); return ip

if DB_PATH.exists():
    db = get_db()
    if db:
        try: c = db.execute("SELECT COUNT(*) as c FROM song").fetchone()["c"]; print(f"\U0001f4da 曲库: {c} 首")
        except: print("\U0001f4da 曲库: 已连接")
        db.close()
ip = get_ip(); s = Th(("0.0.0.0", PORT), H)
print(f"\n\u2554\u2550 TV: http://{ip}:{PORT}/tv\n\u2557\n\u255a\u2550 \U0001f4f1 手机: http://{ip}:{PORT}/m\n\u255d\n按 Ctrl+C 停止")
try: s.serve_forever()
except KeyboardInterrupt: s.server_close()
