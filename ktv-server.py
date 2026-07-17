#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎤 家庭KTV点歌系统 - 纯Python，零依赖，双击启动
功能：电视端播放 + 手机端点歌 + 原唱/伴唱切换 + 搜索
依赖：Python 3 + ffmpeg（已加入PATH）
"""

import os, sys, json, sqlite3, subprocess, shutil, threading, time, socket
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path
from datetime import datetime

# ======================== 配置 ========================
PORT = 3456
KTV_DIR = Path(r"H:\KTVSong")
DB_PATH = KTV_DIR / "song_V3.2.3.db"
HLS_CACHE = Path(__file__).parent / "hls-cache"
VOICE_FILE = Path(__file__).parent / "voice-mode.json"
SCRIPT_DIR = Path(__file__).parent

# ======================== 全局状态 ========================
song_queue = []          # 队列: [{id, song_id, title, artist, nickname, status}]
queue_counter = 0        # 队列ID计数器
current_song_id = None   # 当前播放歌曲ID
voice_modes = {}         # {song_id: "original"|"accompaniment"}
play_count = {}          # {song_id: count} 播放统计
play_lock = threading.Lock()
hls_locks = {}           # {song_id: threading.Lock} 防止重复转码

# 加载语音模式
if VOICE_FILE.exists():
    try: voice_modes = json.loads(VOICE_FILE.read_text('utf-8'))
    except: pass

# ======================== 数据库 ========================
def get_db():
    """获取数据库连接（只读）"""
    if not DB_PATH.exists():
        return None
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only = ON")
    return db

def search_songs(query):
    db = get_db()
    if not db: return []
    try:
        if not query or not query.strip():
            rows = db.execute("SELECT * FROM song ORDER BY id LIMIT 50").fetchall()
        else:
            q = f"%{query.strip()}%"
            rows = db.execute("SELECT * FROM song WHERE name LIKE ? OR singer_names LIKE ? OR acronym LIKE ? OR id LIKE ? ORDER BY id LIMIT 100", (q, q, q, q)).fetchall()
        db.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"搜索错误: {e}")
        db.close(); return []

def get_song(song_id):
    """根据ID获取歌曲"""
    db = get_db()
    if not db: return None
    try:
        row = db.execute("SELECT * FROM song WHERE id = ?", (song_id,)).fetchone()
        db.close()
        return dict(row) if row else None
    except:
        db.close()
        return None

# ======================== 文件查找 ========================
# 扫描本地视频文件 {编号: 完整路径}
local_files = {}
def scan_local_files():
    ktv = str(KTV_DIR)
    if not os.path.isdir(ktv): return
    count = 0
    for d in os.listdir(ktv):
        sub = os.path.join(ktv, d)
        if not os.path.isdir(sub) or not d.startswith('song'): continue
        for f in os.listdir(sub):
            fp = os.path.join(sub, f)
            if os.path.isfile(fp) and f.isdigit():
                local_files[f] = fp
                count += 1
    print(f"📹 本地视频: {count} 个文件")

def wait_for_file(fp, timeout=30):
    """Wait for a file to appear (HLS segments are generated asynchronously)"""
    for _ in range(timeout * 10):
        if os.path.exists(fp): return True
        time.sleep(0.1)
    return False

def find_song_file(fn):
    """根据文件编号查找视频文件"""
    fp = local_files.get(str(fn))
    if fp: return fp
    ktv = str(KTV_DIR)
    if os.path.isdir(ktv):
        for d in os.listdir(ktv):
            sub = os.path.join(ktv, d)
            if os.path.isdir(sub) and d.startswith('song'):
                fp2 = os.path.join(sub, str(fn))
                if os.path.exists(fp2):
                    local_files[str(fn)] = fp2
                    return fp2
    return None

# ======================== HLS 转码 ========================
def get_hls_dir(song_id):
    return HLS_CACHE / str(song_id)

def is_hls_ready(song_id):
    d = get_hls_dir(song_id)
    return d.exists() and (d / "master.m3u8").exists()

def generate_hls(sid, fp):
    """Generate HLS with audio track detection"""
    hls_dir = get_hls_dir(sid)
    hls_dir.mkdir(parents=True, exist_ok=True)
    seg_v = str(hls_dir / "video_%04d.ts")
    pl_v = str(hls_dir / "video.m3u8")
    pl_a0 = str(hls_dir / "audio0.m3u8")
    pl_a1 = str(hls_dir / "audio1.m3u8")
    
    # Probe audio tracks
    audio_tracks = 0
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", fp], capture_output=True, text=True, timeout=30)
        audio_tracks = len(r.stdout.strip().split(chr(10))) if r.stdout.strip() else 0
    except: pass
    print(f"  Audio tracks: {audio_tracks}")
    
    # Video track
    cmd_v = ["ffmpeg", "-loglevel", "error", "-y", "-i", fp, "-map", "0:v:0", "-an", "-c:v", "copy", "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "event", "-hls_flags", "independent_segments", "-hls_segment_filename", seg_v, pl_v]
    
    # Audio 0: original track
    seg_a0 = str(hls_dir / "audio0_%04d.ts")
    cmd_a0 = ["ffmpeg", "-loglevel", "error", "-y", "-i", fp, "-map", "0:a:0", "-vn", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "event", "-hls_flags", "independent_segments", "-hls_segment_filename", seg_a0, pl_a0]
    
    # Audio 1: use track 1 if available, else use left channel from track 0
    seg_a1 = str(hls_dir / "audio1_%04d.ts")
    if audio_tracks >= 2:
        cmd_a1 = ["ffmpeg", "-loglevel", "error", "-y", "-i", fp, "-map", "0:a:1", "-vn", "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "event", "-hls_flags", "independent_segments", "-hls_segment_filename", seg_a1, pl_a1]
    else:
        # Extract left channel (accompaniment) from stereo track 0
        cmd_a1 = ["ffmpeg", "-loglevel", "error", "-y", "-i", fp, "-map", "0:a:0", "-vn", "-af", "pan=mono|c0=FL", "-c:a", "aac", "-b:a", "128k", "-ac", "1", "-f", "hls", "-hls_time", "6", "-hls_playlist_type", "event", "-hls_flags", "independent_segments", "-hls_segment_filename", seg_a1, pl_a1]
    
    # Write master.m3u8 immediately
    NL = chr(10)
    prefix = "/api/stream/" + str(sid)
    master = NL.join(["#EXTM3U", "#EXT-X-VERSION:6",
        "#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"aud\",NAME=\"\u539f\u5531\",DEFAULT=YES,AUTOSELECT=YES,URI=\"" + prefix + "/audio0.m3u8\"",
        "#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID=\"aud\",NAME=\"\u4f34\u5531\",DEFAULT=NO,AUTOSELECT=NO,URI=\"" + prefix + "/audio1.m3u8\"",
        "#EXT-X-STREAM-INF:BANDWIDTH=8000000,AUDIO=\"aud\"", prefix + "/video.m3u8", ""])
    (hls_dir / "master.m3u8").write_text(master, 'utf-8')
    
    def transcode():
        procs = [subprocess.Popen(cmd_v, stderr=subprocess.PIPE),
                 subprocess.Popen(cmd_a0, stderr=subprocess.PIPE),
                 subprocess.Popen(cmd_a1, stderr=subprocess.PIPE)]
        for p in procs: p.wait()
        print(f"  Done: song {sid}")
    threading.Thread(target=transcode, daemon=True).start()
    return True

def fix_master(master_path, song_id):
    """修正 master.m3u8 确保 hls.js 能正确识别音频轨道"""
    try:
        content = master_path.read_text('utf-8')
        # 确保有正确的音频分组名称
        if "audio" not in content:
            # 如果格式不对，手动修复
            pass
        master_path.write_text(content, 'utf-8')
    except Exception as e:
        print(f"  ⚠️ 修正master失败: {e}")

def ensure_hls(song_id, filepath):
    """确保HLS缓存已生成（线程安全）"""
    if is_hls_ready(song_id):
        return True
    lock = hls_locks.get(song_id)
    if not lock:
        lock = threading.Lock()
        hls_locks[song_id] = lock
    with lock:
        if is_hls_ready(song_id):
            return True
        try:
            generate_hls(song_id, filepath)
            return True
        except Exception as e:
            print(f"转码失败: {e}")
            return False

# ======================== 队列管理 ========================
def add_to_queue(song_id, nickname="手机点歌"):
    global queue_counter
    song = get_song(song_id)
    with play_lock:
        queue_counter += 1
        entry = {
            "id": queue_counter,
            "song_id": song_id,
            "title": song.get("name", "未知") if song else "未知",
            "artist": song.get("singer_names", "未知歌手") if song else "未知歌手",
            "nickname": nickname,
            "status": "waiting",
            "time": time.time()
        }
        song_queue.append(entry)
        # 如果队列为空，立即开始播放
        if len(song_queue) == 1:
            entry["status"] = "playing"
            global current_song_id
            current_song_id = song_id
            # 统计
            play_count[song_id] = play_count.get(song_id, 0) + 1
        return entry["id"]

def remove_from_queue(queue_id):
    with play_lock:
        for i, e in enumerate(song_queue):
            if e["id"] == queue_id:
                if e["status"] == "playing":
                    global current_song_id
                    current_song_id = None
                song_queue.pop(i)
                return True
    return False

def top_queue(queue_id):
    with play_lock:
        for i, e in enumerate(song_queue):
            if e["id"] == queue_id:
                item = song_queue.pop(i)
                # 插在正在播放的后面
                for j, e2 in enumerate(song_queue):
                    if e2["status"] == "playing":
                        song_queue.insert(j + 1, item)
                        return True
                song_queue.insert(0, item)
                return True
    return False

def skip_current():
    with play_lock:
        for i, e in enumerate(song_queue):
            if e["status"] == "playing":
                e["status"] = "finished"
                global current_song_id
                current_song_id = None
                # 播放下一个
                for j in range(i + 1, len(song_queue)):
                    if song_queue[j]["status"] == "waiting":
                        song_queue[j]["status"] = "playing"
                        current_song_id = song_queue[j]["song_id"]
                        play_count[current_song_id] = play_count.get(current_song_id, 0) + 1
                        break
                return True
    return False

def get_queue():
    with play_lock:
        return [dict(e) for e in song_queue]

def get_now_playing():
    with play_lock:
        for e in song_queue:
            if e["status"] == "playing":
                return {"song_id": e["song_id"], "title": e["title"], "artist": e["artist"]}
    return None

# ======================== 语音模式 ========================
def get_voice_mode(song_id):
    return voice_modes.get(str(song_id), "original")

def set_voice_mode(song_id, mode):
    voice_modes[str(song_id)] = mode
    try:
        VOICE_FILE.write_text(json.dumps(voice_modes, ensure_ascii=False), 'utf-8')
    except:
        pass

# ======================== HTTP 服务器 ========================
MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
}

class KTVHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # 精简日志，不打印静态文件请求
        if "?" in args[0] or "/api/" in args[0]:
            print(f"  [{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _send_file(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        mime = MIME_TYPES.get(ext, "application/octet-stream")
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except FileNotFoundError:
            self._send_json({"error": "File not found"}, 404)

    def _read_body(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        params = parse_qs(parsed.query)

        # ===== API 路由 =====
        if path == "/api/songs":
            q = params.get("q", [""])[0]
            results = search_songs(q)
            data = [{
                "id": r["id"],
                "title": r.get("name", "未知"),
                "artist": r.get("singer_names", "未知歌手"),
                "pinyin": r.get("acronym", ""),
                "edition": r.get("edition", "")
            } for r in results]
            self._send_json(data)

        elif path.startswith("/api/songs/") and len(path.split("/")) == 4:
            song_id = int(path.split("/")[3])
            song = get_song(song_id)
            if song:
                self._send_json({
                    "id": song["id"],
                    "title": song.get("name", "未知"),
                    "artist": song.get("singer_names", "未知歌手"),
                    "pinyin": song.get("acronym", ""),
                    "file_number": song.get("number", song["id"])
                })
            else:
                self._send_json({"error": "歌曲不存在"}, 404)

        elif path == "/api/queue":
            q = get_queue()
            self._send_json([{
                "queue_id": e["id"],
                "song_id": e["song_id"],
                "title": e["title"],
                "artist": e["artist"],
                "nickname": e["nickname"],
                "status": e["status"]
            } for e in q])

        elif path == "/api/now-playing":
            np = get_now_playing()
            self._send_json(np)

        elif path.startswith("/api/voice/status/"):
            song_id = path.split("/")[4]
            mode = get_voice_mode(song_id)
            self._send_json({"song_id": song_id, "mode": mode})

        elif path == "/api/charts":
            top = sorted(play_count.items(), key=lambda x: -x[1])[:10]
            self._send_json([{"song_id": k, "count": v} for k, v in top])

        # ===== HLS 流 =====
        elif path.startswith("/api/stream/"):
            parts = path.split("/")
            # /api/stream/<song_id> 或 /api/stream/<song_id>/<file>
            if len(parts) == 4:
                # 请求 master.m3u8 或 启动转码
                song_id = parts[3]
                song = get_song(song_id)
                if not song:
                    self._send_json({"error": "歌曲不存在"}, 404)
                    return
                fn = song.get("number", song["id"])
                fp = find_song_file(fn)
                if not fp:
                    self._send_json({"error": f"视频文件不存在 (song{fn})"}, 404)
                    return
                # 确保HLS已生成
                if not ensure_hls(song_id, fp):
                    self._send_json({"error": "转码失败"}, 500)
                    return
                # 返回 master.m3u8
                master = get_hls_dir(song_id) / "master.m3u8"
                self._send_file(str(master))
            elif len(parts) == 5:
                song_id = parts[3]
                filename = parts[4]
                filepath = get_hls_dir(song_id) / filename
                # 安全检查
                resolved = filepath.resolve()
                cache_dir = get_hls_dir(song_id).resolve()
                if str(resolved).startswith(str(cache_dir)):
                    self._send_file(str(resolved))
                else:
                    self._send_json({"error": "Forbidden"}, 403)
            else:
                self._send_json({"error": "Invalid path"}, 400)

        # ===== 静态文件 =====
        elif path == "/" or path == "/tv":
            self._send_file(str(SCRIPT_DIR / "public" / "tv.html"))
        elif path == "/m":
            self._send_file(str(SCRIPT_DIR / "public" / "m.html"))
        elif path.startswith("/public/"):
            self._send_file(str(SCRIPT_DIR / path[1:]))
        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/queue":
            body = self._read_body()
            song_id = body.get("song_id")
            nickname = body.get("nickname", "手机点歌")
            if not song_id:
                self._send_json({"error": "缺少 song_id"}, 400)
                return
            qid = add_to_queue(song_id, nickname)
            self._send_json({"success": True, "queue_id": qid})

        elif path.startswith("/api/queue/") and path.endswith("/top"):
            qid = int(path.split("/")[3])
            top_queue(qid)
            self._send_json({"success": True})

        elif path == "/api/playback/ended":
            skip_current()
            self._send_json({"success": True})

        elif path == "/api/voice/switch":
            body = self._read_body()
            song_id = body.get("song_id")
            mode = body.get("mode", "original")
            if mode not in ("original", "accompaniment"):
                self._send_json({"error": "mode 必须是 original 或 accompaniment"}, 400)
                return
            set_voice_mode(song_id, mode)
            self._send_json({"success": True, "mode": mode})

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_DELETE(self):
        path = urlparse(self.path).path
        if path.startswith("/api/queue/"):
            qid = int(path.split("/")[3])
            remove_from_queue(qid)
            self._send_json({"success": True})
        else:
            self._send_json({"error": "Not found"}, 404)

# ======================== 启动 ========================
def get_lan_ip():
    """获取局域网IP"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip

def main():
    # 创建缓存目录
    HLS_CACHE.mkdir(parents=True, exist_ok=True)

    # 检查条件
    if not DB_PATH.exists():
        print(f"⚠️ 数据库不存在: {DB_PATH}")
        print("   请确认歌曲文件在 H:\\KTVSong 目录下")
    else:
        db = get_db()
        if db:
            try:
                count = db.execute("SELECT COUNT(*) as c FROM song").fetchone()["c"]
                print(f"📚 曲库: {count} 首歌曲")
                db.close()
            except:
                print("📚 曲库: 已连接")
            db.close()

    # 启动服务器
    lan_ip = get_lan_ip()
    server = HTTPServer(("0.0.0.0", PORT), KTVHandler)

    print("")
    print("╔══════════════════════════════════════════╗")
    print("║        🎤 家庭KTV点歌系统 v1.0           ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  📺 电视端:  http://{lan_ip}:{PORT}/tv     ║")
    print(f"║  📱 手机点歌: http://{lan_ip}:{PORT}/m      ║")
    print("║                                          ║")
    print(f"║  💻 本机访问: http://localhost:{PORT}/tv    ║")
    print("║                                          ║")
    print("║  🔄 原唱/伴唱: 电视端按钮切换              ║")
    print("║  🐍 纯Python · 零依赖 · 双击启动          ║")
    print("╚══════════════════════════════════════════╝")
    print("")
    print("按 Ctrl+C 停止服务")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()

if __name__ == "__main__":
    main()