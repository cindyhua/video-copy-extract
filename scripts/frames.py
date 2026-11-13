# -*- coding: utf-8 -*-
"""抽帧工具：先侦察「文字会不会变」，再决定抽多少帧。

子命令:
  probe  <视频>  [--fps 1] [--tile 3x3]   侦察：信息 + 帧间差异 + 建议 + 拼图
  sheet  <视频>  [--start 0] [--dur 60] [--fps 0.2] [--tile 4x3]  按范围出拼图
  full   <视频>  --at 4,20,55              导出原分辨率单帧（精读用）

要点:
  · 判断画面用「拼图」（缩略），精读文字必须用 full 的原分辨率单帧
  · 缩略到 288px 宽时，出处/署名一类小字会糊成另一个词
  · 零额外依赖：imageio-ffmpeg 自带 ffmpeg + numpy
"""
import argparse
import json
import os
import re
import subprocess
import sys

def _dep_error(name):
    """依赖不随 skill 分发，缺了就把安装命令原样打给用户，不自动安装。"""
    req = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "requirements.txt"))
    py = sys.executable
    sys.exit(
        "\n环境未就绪：缺少 Python 依赖 %s\n"
        "这些包不随本 skill 分发，换机后要自己装一次。\n"
        "可复制命令：\n"
        '  "%s" -m pip install -r "%s"\n'
        '  "%s" -m playwright install chromium-headless-shell\n'
        "验证：\n"
        '  "%s" -c "import numpy, imageio_ffmpeg, yt_dlp, playwright; print(\'deps ok\')"\n'
        % (name, py, req, py, py))


try:
    import numpy as np
except ImportError:
    _dep_error("numpy")

try:
    import imageio_ffmpeg
except ImportError:
    _dep_error("imageio-ffmpeg")

W, H = 288, 512
FRAME = W * H
TOP_RATIO = 0.45


def ffmpeg():
    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_info(path):
    r = subprocess.run([ffmpeg(), "-i", path], capture_output=True,
                       text=True, errors="replace")
    e = r.stderr or ""
    dur, res, has_audio = None, None, False
    for line in e.splitlines():
        if "Duration:" in line and dur is None:
            dur = line.split("Duration:")[1].split(",")[0].strip()
        if "Stream #" in line:
            if "Video:" in line and res is None:
                m = re.search(r"(\d{2,5})x(\d{2,5})", line)
                if m:
                    res = "%sx%s" % (m.group(1), m.group(2))
            if "Audio:" in line:
                has_audio = True
    return {"duration": dur, "resolution": res, "has_audio": has_audio}


def dur_sec(s):
    try:
        h, m, sec = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(sec)
    except Exception:
        return None


def gray_frames(path, fps, start=None, dur=None):
    vf = "fps=%s,scale=%d:%d" % (fps, W, H)
    cmd = [ffmpeg()]
    if start is not None:
        cmd += ["-ss", str(start)]
    if dur is not None:
        cmd += ["-t", str(dur)]
    cmd += ["-i", path, "-vf", vf, "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    p = subprocess.run(cmd, capture_output=True)
    buf = p.stdout or b""
    n = len(buf) // FRAME
    if n == 0:
        return np.zeros((0, H, W), dtype=np.uint8)
    return np.frombuffer(buf[: n * FRAME], dtype=np.uint8).reshape(n, H, W)


def mad_series(arr):
    if arr.shape[0] < 2:
        return np.zeros(0)
    top = arr[:, : int(H * TOP_RATIO), :].astype(np.int16)
    return np.abs(np.diff(top, axis=0)).mean(axis=(1, 2))


def judge(mad):
    if mad.size == 0:
        return "帧数不足，无法判断"
    med, mx = float(np.median(mad)), float(mad.max())
    if mx < 1.0:
        return "文字几乎全程不变 → 抽 1 帧即可（用 full 取原分辨率）"
    # 真人口播 / 游戏录屏：画面整体持续运动，med 本来就高，但帧差是
    # 均匀的、没有切卡尖峰。拿 med*3 当阈值会算出 0 个变化点，进而误报
    # 「文字基本不变」（实测 S5：med=16.968 / max=39.973 / changes=0，
    # 画面其实是真人＋逐句硬字幕）。此时帧差判不了文字卡切换，如实说判不了。
    # 阈值 5.0 仅适用于默认 fps=1 的侦察；降 fps 后 med 会变，不要跨 fps 比较。
    if med >= 5.0:
        return ("画面持续运动（mad_median=%.1f，真人口播/动态画面常见），"
                "帧差判不了文字卡切换 → 先看 sheets 定形态，读字幕用 full" % med)
    th = max(med * 3.0, 1.5)
    changes = [i for i, v in enumerate(mad) if v > th]
    if len(changes) <= 2:
        return "文字基本不变，仅 %d 处有变化 → 先取 1 帧，再针对变化点补帧" % len(changes)
    ratio = len(changes) / float(mad.size)
    if ratio < 0.5:
        return "文字约每 %.1f 秒变一次 → 建议 1~2s/帧" % (1.0 / max(ratio, 0.01))
    return "文字变化频繁 → 建议 0.5s/帧；若为逐行出现，另需提高 fps 并只截文字区"


def make_tiles(path, fps, cols, rows, outdir, start, dur, tag):
    per = cols * rows
    step = per / float(fps)
    made, t, i = [], float(start or 0), 0
    end = (start or 0) + (dur if dur is not None else 1e9)
    while t < end - 0.01:
        out = os.path.join(outdir, "sheet_%s_%02d_%06.1fs.jpg" % (tag, i, t))
        vf = "fps=%s,scale=%d:%d,tile=%dx%d" % (fps, W, H, cols, rows)
        subprocess.run([ffmpeg(), "-ss", "%.2f" % t, "-t", "%.2f" % step,
                        "-i", path, "-vf", vf, "-frames:v", "1", "-y", out],
                       capture_output=True)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            made.append(out)
        t += step
        i += 1
        if dur is None and i > 40:      # 兜底，别无限翻
            break
    return made


def cmd_probe(a):
    info = probe_info(a.path)
    d = dur_sec(info["duration"])
    arr = gray_frames(a.path, a.fps)
    mad = mad_series(arr)
    cols, rows = [int(x) for x in a.tile.lower().split("x")]
    tiles = make_tiles(a.path, a.fps, cols, rows, a.out, 0, d, "probe") if d else []
    return {"file": a.path, "info": info,
            "frames_analyzed": int(arr.shape[0]),
            "mad_median": round(float(np.median(mad)), 3) if mad.size else None,
            "mad_max": round(float(mad.max()), 3) if mad.size else None,
            "judgement": judge(mad),
            "sheets": tiles,
            "next": "先看 sheets 判断画面类型；要读字用 `full --at <秒>` 取原分辨率帧"}


def cmd_sheet(a):
    info = probe_info(a.path)
    cols, rows = [int(x) for x in a.tile.lower().split("x")]
    made = make_tiles(a.path, a.fps, cols, rows, a.out, a.start, a.dur, "sheet")
    return {"file": a.path, "info": info, "sheets": made,
            "coverage": "%s → +%ss" % (a.start, a.dur if a.dur is not None else "end")}


def cmd_full(a):
    info = probe_info(a.path)
    times = [float(x) for x in str(a.at).replace("，", ",").split(",") if x.strip()]
    made = []
    for t in times:
        out = os.path.join(a.out, "full_%06.1fs.jpg" % t)
        subprocess.run([ffmpeg(), "-ss", "%.2f" % t, "-i", a.path,
                        "-frames:v", "1", "-y", out], capture_output=True)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            made.append(out)
    return {"file": a.path, "info": info, "frames": made,
            "note": "这些是原分辨率帧，用于精读文字；不要用 sheet 里的缩略图读小字"}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("probe")
    p1.add_argument("path")
    p1.add_argument("--fps", default="1")
    p1.add_argument("--tile", default="3x3")
    p1.add_argument("--out", default=None)

    p2 = sub.add_parser("sheet")
    p2.add_argument("path")
    p2.add_argument("--start", type=float, default=0)
    p2.add_argument("--dur", type=float, default=60)
    p2.add_argument("--fps", default="0.2")
    p2.add_argument("--tile", default="4x3")
    p2.add_argument("--out", default=None)

    p3 = sub.add_parser("full")
    p3.add_argument("path")
    p3.add_argument("--at", default="0")
    p3.add_argument("--out", default=None)

    a = ap.parse_args()
    if not os.path.isfile(a.path):
        sys.exit("文件不存在: %s" % a.path)
    a.out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.path)), "_frames")
    os.makedirs(a.out, exist_ok=True)

    fn = {"probe": cmd_probe, "sheet": cmd_sheet, "full": cmd_full}[a.cmd]
    print(json.dumps(fn(a), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
