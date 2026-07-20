# 🎤 家庭KTV点歌系统

纯 Python，零依赖，双击启动。

## 启动方式

1. 双击 `start.bat`
2. 浏览器自动打开电视端

或手动访问：
- 📺 电视端: `http://localhost:3456/tv`
- 📱 手机点歌: `http://localhost:3456/m`

## 功能

- ✅ 电视端全屏播放（HLS 边转边播）
- ✅ 手机扫码/浏览器点歌
- ✅ 原唱/伴唱秒切（独立音轨 HLS + 静默预加载）
- ✅ 歌曲搜索（歌名/歌手/拼音）
- ✅ 搜歌只显示本地有的歌
- ✅ 队列管理（点歌/删除/跳过）
- ✅ 播完自动下一首
- ✅ 取消静音（localStorage 记住）
- ✅ 进度条可拖拽
- ✅ 键盘快捷键（V切换 S搜索 空格暂停）

## 技术栈

- Python 3（`py` 命令启动）
- ffmpeg（HLS 转码，需加入 PATH）
- hls.js（前端播放）
- SQLite（song_V3.2.3.db 曲库）

## 文件结构

```
KTV/
├── ktv-server.py      # 主服务（端口 3456）
├── start.bat          # 一键启动
├── public/
│   ├── tv.html        # 电视端
│   └── m.html         # 手机点歌端
└── hls-cache/         # HLS 转码缓存（自动生成）
```
