# 家庭KTV点歌系统

**推荐使用 junyao-ktv**，功能完整、兼容性好。

## 启动方式

1. 打开 WSL（Ubuntu）
2. 运行：
```bash
cd ~/junyao-ktv/app/docker/server
DATA_DIR=/home/riliang/junyao-ktv-data MV_DIR=/mnt/c/KTV node index.js
```

3. 浏览器打开：
   - 电视端: http://localhost:8080/tv
   - 手机点歌: http://localhost:8080/m
   - 管理后台: http://localhost:8080/admin

## 功能
- 电视端全屏播放
- 手机扫码点歌
- 原唱/伴唱切换
- 歌曲搜索
- 队列管理
- 曲库管理
