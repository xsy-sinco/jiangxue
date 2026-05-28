"""集锦视频转码：把上传的非浏览器友好视频（H.265/HEVC 等）后台转成 H.264/AAC MP4。

为什么需要：浏览器（Chrome/Firefox on Windows）可靠支持的是 H.264 视频；
很多手机/录屏默认 H.265(HEVC)，网页上只有声音没画面。这里在上传后用 ffmpeg
统一转成 H.264 + AAC + faststart。

设计：
- 单进程内一个后台 worker 线程 + 队列（配合 gunicorn -w 1 --threads 4）。
- 没装 ffmpeg 时优雅降级：不转码，文件原样提供（status=ready）。
- ffmpeg/ffprobe 路径可用环境变量 FFMPEG_BIN / FFPROBE_BIN 覆盖（便于测试）。
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path

from web import db

FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE_BIN = os.environ.get("FFPROBE_BIN", "ffprobe")

# 浏览器基本都能解的视频编码；容器层面 mp4/webm 最稳
_BROWSER_OK_CODECS = {"h264", "vp8", "vp9", "av1"}
_OK_CONTAINER_EXT = {"mp4", "webm"}

_q: "queue.Queue[int]" = queue.Queue()
_worker_lock = threading.Lock()
_worker_started = False


def has_ffmpeg() -> bool:
    return shutil.which(FFMPEG_BIN) is not None


def _has_ffprobe() -> bool:
    return shutil.which(FFPROBE_BIN) is not None


def _probe_video_codec(path: Path) -> str | None:
    """ffprobe 拿第一条视频流的编码名（小写），失败返回 None。"""
    try:
        out = subprocess.run(
            [FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        name = (out.stdout or "").strip().lower()
        return name or None
    except Exception:
        return None


def _box_scan_is_hevc(path: Path) -> bool:
    """没有 ffprobe 时的兜底：扫 MP4/MOV 盒子里的 codec fourcc。
    发现 hvc1/hev1/av01 认为需要转码；只有 avc1 则不需要。"""
    try:
        head = path.read_bytes()[:2_000_000]  # 前 2MB 足够覆盖 moov 里的 stsd
    except Exception:
        return False
    if b"hvc1" in head or b"hev1" in head:
        return True
    return False


def needs_transcode(path: Path, ext: str) -> bool:
    """判断是否需要转码成 H.264 mp4。"""
    ext = ext.lower()
    if _has_ffprobe():
        codec = _probe_video_codec(path)
        if codec is None:
            return False  # 探测不到就别瞎转
        return not (codec in _BROWSER_OK_CODECS and ext in _OK_CONTAINER_EXT)
    # 兜底：只能粗判 HEVC
    if ext == "webm":
        return False
    return _box_scan_is_hevc(path)


def _run_ffmpeg(src: Path, dst: Path) -> bool:
    """转成 H.264/AAC mp4 + faststart。最长跑 1 小时。成功返回 True。"""
    cmd = [
        FFMPEG_BIN, "-y", "-i", str(src),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        # 超过 1080p 的等比缩到 1080p，控制体积；高度为偶数（libx264 要求）
        "-vf", "scale='min(1920,iw)':-2",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except Exception:
        return False
    return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0


def _process(highlight_id: int) -> None:
    hl = db.get_highlight(highlight_id)
    if not hl:
        return
    src = db.HIGHLIGHT_DIR / Path(hl["filename"]).name
    if not src.exists():
        db.set_highlight_status(highlight_id, "failed")
        return

    stem = src.stem
    out_name = f"{stem}.mp4"
    out_path = db.HIGHLIGHT_DIR / out_name
    # 源本身就是 .mp4 时，先转到临时文件再替换，避免边读边写同一文件
    same = out_path == src
    work = db.HIGHLIGHT_DIR / f"{stem}.transcoding.mp4" if same else out_path

    if _run_ffmpeg(src, work):
        if same:
            os.replace(str(work), str(src))
            final_name, final_path = src.name, src
        else:
            final_name, final_path = out_name, out_path
            if src != final_path:
                src.unlink(missing_ok=True)  # 删掉原始非 mp4 文件
        db.set_highlight_status(
            highlight_id, "ready",
            filename=final_name, size_bytes=final_path.stat().st_size,
        )
    else:
        # 转码失败：清理临时文件，标记失败（原文件保留，至少能下到）
        if work.exists() and work != src:
            work.unlink(missing_ok=True)
        db.set_highlight_status(highlight_id, "failed")


def _worker() -> None:
    while True:
        hid = _q.get()
        try:
            _process(hid)
        except Exception:
            try:
                db.set_highlight_status(hid, "failed")
            except Exception:
                pass
        finally:
            _q.task_done()


def ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        threading.Thread(target=_worker, daemon=True).start()
        _worker_started = True


def submit(highlight_id: int) -> None:
    """把一个集锦丢进转码队列（惰性启动 worker）。"""
    ensure_worker()
    _q.put(highlight_id)


def requeue_unfinished() -> None:
    """服务重启后，把卡在 processing 的集锦重新排队（仅在有 ffmpeg 时）。"""
    if not has_ffmpeg():
        return
    try:
        for hl in db.list_highlights():
            if hl.get("status") == "processing":
                submit(hl["id"])
    except Exception:
        pass
