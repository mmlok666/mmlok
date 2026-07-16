#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎤 家庭KTV点歌系统
纯 Python 实现，零依赖，双击启动
功能：电视端播放 + 手机点歌 + 原唱/伴唱切换 + 搜索
"""

import os, sys, json, sqlite3, subprocess, threading, time, socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path
from datetime import datetime

PORT = 3456
KTV_DIR = Path(r"H:\KTVSong")
DB_PATH = KTV_DIR / "song_V3.2.3.db"
HLS_CACHE = Path(__file__).parent / "hls-cache"
VOICE_FILE = Path(__file__).parent / "voice-mode.json"
SCRIPT_DIR = Path(__file__).parent

song_queue = []
queue_counter = 0
current_song_id = None
voice_modes = {}
play_count = {}
play_lock = threading.Lock()
hls_locks = {}

if VOICE_FILE.exists():
    try: voice_modes = json.loads(VOICE_FILE.read_text('utf-8'))
    except: pass

def get_db():
    if not DB_PATH.exists(): return None
    db = sqlite3.connect(str(DB_PATH)); db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON"); return db

def search_songs(query):
    db = get_db()
    if not db: return []
    try:
        if not query or not query.strip():
            rows = db.execute("SELECT * FROM song ORDER BY id LIMIT 50").fetchall()
        else:
            q = f"%{query.strip()}%"
            rows = db.execute("SELECT * FROM song WHERE song_name LIKE ? OR singer LIKE ? OR pinyin LIKE ? OR id LIKE ? ORDER BY id LIMIT 100", (q, q, q, q)).fetchall()
        db.close(); return [dict(r) for r in rows]
    except: db.close(); return []

def get_song(song_id):
    db = get_db()
    if not db: return None
    try:
        row = db.execute("SELECT * FROM song WHERE id = ?", (song_id,)).fetchone()
        db.close(); return dict(row) if row else None
    except: db.close(); return None

def find_song_file(fn):
    ktv = str(KTV_DIR)
    names = [f"song{fn}.mkv", f"song{fn}.mp4", f"song{str(fn).zfill(4)}.mkv", f"song{str(fn).zfill(4)}.mp4"]
    for n in names:
        p = os.path.join(ktv, n)
        if os.path.exists(p): return p
    for base in range(0, 4500, 500):
        sub = f"song{base}";
        for n in names:
            p = os.path.join(ktv, sub, n)
            if os.path.exists(p): return p
    return None

def get_hls_dir(sid): return HLS_CACHE / str(sid)

def is_hls_ready(sid):
    d = get_hls_dir(sid); return d.exists() and (d / "master.m3u8").exists() and (d / "stream.m3u8").exists()

def generate_hls(sid, fp):
    d = get_hls_dir(sid); d.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-i", fp, "-map", "0:v:0", "-c:v", "copy", "-map", "0:a:0", "-c:a:0", "aac", "-b:a:0", "128k", "-ac:a:0", "2", "-map", "0:a:1", "-c:a:1", "aac", "-b:a:1", "128k", "-ac:a:1", "2", "-f", "hls", "-hls_time", "10", "-hls_list_size", "0", "-hls_segment_filename", str(d / "seg_%03d.ts"), "-master_pl_name", "master.m3u8", "-var_stream_map", "v:0,a:0 a:1", "-loglevel", "error", str(d / "stream.m3u8")]
    print(f"  🔄 转码: 歌曲ID={sid}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0: raise RuntimeError(f"ffmpeg失败: {r.stderr[:200]}")
    print(f"  ✅ 转码完成: 歌曲ID={sid}")

def ensure_hls(sid, fp):
    if is_hls_ready(sid): return True
    lock = hls_locks.get(sid)
    if not lock: lock = threading.Lock(); hls_locks[sid] = lock
    with lock:
        if is_hls_ready(sid): return True
        try: generate_hls(sid, fp); return True
        except: return False

def add_to_queue(sid, nk="手机点歌"):
    global queue_counter; s = get_song(sid)
    with play_lock:
        queue_counter += 1
        e = {"id": queue_counter, "song_id": sid, "title": s.get("song_name","未知") if s else "未知", "artist": s.get("singer","未知歌手") if s else "未知歌手", "nickname": nk, "status": "waiting", "time": time.time()}
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

MIME = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".m3u8": "application/vnd.apple.mpegurl", ".ts": "video/mp2t"}

class H(BaseHTTPRequestHandler):
    def log_message(self, f, *a):
        if "/api/" in a[0]: print(f"  [{datetime.now().strftime('%H:%M:%S')}] {a[0]}")
    def _j(self, d, s=200):
        self.send_response(s); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers(); self.wfile.write(json.dumps(d, ensure_ascii=False).encode())
    def _f(self, p):
        e = os.path.splitext(p)[1].lower(); m = MIME.get(e, "application/octet-stream")
        try:
            with open(p, 'rb') as f: c = f.read()
            self.send_response(200); self.send_header("Content-Type", m); self.send_header("Content-Length", str(len(c))); self.send_header("Access-Control-Allow-Origin", "*"); self.end_headers(); self.wfile.write(c)
        except: self._j({"error": "Not found"}, 404)
    def _b(self):
        l = int(self.headers.get('Content-Length', 0)); return json.loads(self.rfile.read(l).decode()) if l > 0 else {}
    def do_OPTIONS(self):
        self.send_response(200); self.send_header("Access-Control-Allow-Origin", "*"); self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS"); self.send_header("Access-Control-Allow-Headers", "Content-Type"); self.end_headers()
    def do_GET(self):
        p = urlparse(self.path).path; q = parse_qs(urlparse(self.path).query)
        if p == "/api/songs":
            r = search_songs(q.get("q", [""])[0]); self._j([{"id": x["id"], "title": x.get("song_name","未知"), "artist": x.get("singer","未知歌手")} for x in r])
        elif p.startswith("/api/songs/") and len(p.split("/")) == 4:
            s = get_song(int(p.split("/")[3]))
            if s: self._j({"id": s["id"], "title": s.get("song_name","未知"), "artist": s.get("singer","未知歌手"), "file_number": s.get("number", s["id"])})
            else: self._j({"error": "不存在"}, 404)
        elif p == "/api/queue":
            self._j([{"queue_id": e["id"], "song_id": e["song_id"], "title": e["title"], "artist": e["artist"], "nickname": e["nickname"], "status": e["status"]} for e in get_queue()])
        elif p == "/api/now-playing": self._j(get_now())
        elif p.startswith("/api/voice/status/"): self._j({"song_id": p.split("/")[4], "mode": voice_modes.get(p.split("/")[4], "original")})
        elif p.startswith("/api/stream/"):
            parts = p.split("/")
            if len(parts) == 4:
                sid = parts[3]; s = get_song(sid)
                if not s: self._j({"error": "不存在"}, 404); return
                fn = s.get("number", s["id"]); fp = find_song_file(fn)
                if not fp: self._j({"error": f"文件不存在 (song{fn})"}, 404); return
                if not ensure_hls(sid, fp): self._j({"error": "转码失败"}, 500); return
                self._f(str(get_hls_dir(sid) / "master.m3u8"))
            elif len(parts) == 5:
                f = get_hls_dir(parts[3]) / parts[4]
                if str(f.resolve()).startswith(str(get_hls_dir(parts[3]).resolve())): self._f(str(f))
                else: self._j({"error": "Forbidden"}, 403)
            else: self._j({"error": "路径错误"}, 400)
        elif p in ("/", "/tv"): self._f(str(SCRIPT_DIR / "public" / "tv.html"))
        elif p == "/m": self._f(str(SCRIPT_DIR / "public" / "m.html"))
        else: self._j({"error": "Not found"}, 404)
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
            voice_modes[str(b.get("song_id"))] = m
            try: VOICE_FILE.write_text(json.dumps(voice_modes, ensure_ascii=False), 'utf-8')
            except: pass
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

HLS_CACHE.mkdir(parents=True, exist_ok=True)
if DB_PATH.exists():
    db = get_db()
    if db:
        try:
            c = db.execute("SELECT COUNT(*) as c FROM song").fetchone()["c"]; print(f"📚 曲库: {c} 首")
        except: print("📚 曲库: 已连接")
        db.close()
ip = get_ip(); s = HTTPServer(("0.0.0.0", PORT), H)
print("\n╔══════════════════════════════════════╗\n║        🎤 家庭KTV点歌系统 v1.0       ║\n╠══════════════════════════════════════╣")
print(f"║  📺 TV:  http://{ip}:{PORT}/tv           ║")
print(f"║  📱 手机: http://{ip}:{PORT}/m            ║")
print(f"║  💻 本机: http://localhost:{PORT}/tv       ║")
print("║  🔄 原唱/伴唱: 按钮切换                   ║")
print("║  🐍 纯Python · 零依赖 · 双击启动          ║")
print("╚══════════════════════════════════════╝\n按 Ctrl+C 停止")
try: s.serve_forever()
except KeyboardInterrupt: s.server_close()