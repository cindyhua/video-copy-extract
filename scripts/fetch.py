# -*- coding: utf-8 -*-
"""多平台视频取源：一条链接 / 一个本地文件 → 本地 mp4（或字幕）。

用法:
  python fetch.py <链接|本地文件> [--out 目录] [--dry-run] [--force-engine X]

平台路由（按链接自动判断）:
  视频号      → shipinhao-batch-save 的 wxvideo.py（已验证）
  B站/抖音/小红书 → yt-dlp（轻，无需浏览器）
  快手        → competitor-teardown 的 extract.py --no-asr --workdir（唯一可行）
  以上失败    → competitor-teardown 兜底（playwright）

输出: 一段 JSON（含 platform/title/author/video/bytes/engine/警告）
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
ENV_PY = os.path.join(HOME, ".workbuddy", "binaries", "python", "envs",
                      "default", "Scripts", "python.exe")
PY = ENV_PY if os.path.exists(ENV_PY) else sys.executable

SHIPINHAO = os.path.join(HOME, ".workbuddy", "skills", "shipinhao-batch-save",
                         "scripts", "wxvideo.py")
CT = os.path.join(HOME, ".workbuddy", "skills", "competitor-teardown",
                  "scripts", "extract.py")
CT_SCRIPTS = os.path.join(HOME, ".workbuddy", "skills", "competitor-teardown",
                          "scripts")

PLATFORMS = [
    ("wechat", r"(weixin\.qq\.com/sph/|channels\.weixin\.qq\.com|finder-preview)"),
    ("bilibili", r"(bilibili\.com|b23\.tv)"),
    ("douyin", r"(douyin\.com|iesdouyin\.com)"),
    ("xiaohongshu", r"(xiaohongshu\.com|xhslink)"),
    ("kuaishou", r"(kuaishou\.com|chenzhongtech)"),
]
LOCAL_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".flv", ".avi", ".webm",
             ".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".aiff")

URL_RE = re.compile(r"https?://[^\s，。、；！？\"'）】\)]+")


def clean_url(text):
    """从分享文案里抠出真正的链接。

    抖音/快手的分享文本是一大段话：
      「8.20 复制打开抖音，看看【xx的作品】… https://v.douyin.com/xxx/ B@g.oq 05/22」
    直接把整段丢给 yt-dlp 会失败，必须先提取 URL。
    """
    m = URL_RE.search(text or "")
    if not m:
        return (text or "").strip()
    return m.group(0).rstrip(".,;，。；")


MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) "
             "Version/17.0 Mobile/15E148 Safari/604.1")


def expand_shortlink(url):
    """把小红书分享短链展开成带 xsec_token 的规范 URL。

    直接把 xhslink.cn 交给 yt-dlp 时，它用桌面 UA 跟 302 会被风控跳到
    /login，然后报 "Unsupported URL: .../login?redirectPath=..."。
    实测短链用移动端 UA 展开一次就是 /discovery/item/<id>?...xsec_token=...，
    这个规范 URL 可以直接被 XiaoHongShuIE 读取，无需登录态。
    只处理 xhslink；其他平台不动，失败时原样返回，不阻断主流程。
    """
    if "xhslink" not in url:
        return url, ""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA})
        with urllib.request.urlopen(req, timeout=20) as resp:
            final = resp.geturl()
        if final and final != url and ("/discovery/item/" in final or "/explore/" in final):
            return final, "短链已展开（移动端 UA），保留 xsec_token"
        return url, ""
    except Exception as e:  # noqa: 展开失败就让 yt-dlp 按原链接试，错误照常上报
        return url, "短链展开失败：%s" % str(e)[:120]


def detect(text):
    for name, pat in PLATFORMS:
        if re.search(pat, text, re.I):
            return name
    return ""


def snap(d):
    if not os.path.isdir(d):
        return {}
    out = {}
    for f in glob.glob(os.path.join(d, "*")):
        if os.path.isfile(f):
            out[f] = os.path.getsize(f)
    return out


def new_media(before, after):
    """找出新增的音视频文件，按体积从大到小。"""
    ext = LOCAL_EXT
    got = [p for p in after if p not in before and p.lower().endswith(ext)]
    return sorted(got, key=lambda p: after[p], reverse=True)


def existing_media(outdir, key):
    """目录里已经下过这条（文件名含作品 ID）时，复用它，别当失败。"""
    if not key or not os.path.isdir(outdir):
        return None
    hits = [p for p in glob.glob(os.path.join(outdir, "*%s*" % key))
            if os.path.isfile(p) and p.lower().endswith(LOCAL_EXT)]
    if not hits:
        return None
    return max(hits, key=os.path.getmtime)


def run(cmd, timeout=600):
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                       timeout=timeout)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


# ---------------------------------------------------------------- 安装提示
# 前提：本 skill 只分发自己的脚本 + 文档。下面这些依赖都不会随 git 走，
# 换机后要用户自己装。原则：缺什么说清楚、给出可直接复制的命令、
# **不自动安装**（装到哪个 Python 环境由用户决定）。
_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
_REQ = os.path.normpath(os.path.join(_SCRIPTS, "..", "requirements.txt"))
_PLAYWRIGHT_BROWSER = '"%s" -m playwright install chromium-headless-shell' % PY

INSTALL_HINTS = {
    "pip": {
        "id": "pip",
        "what": "Python 依赖包（yt-dlp / numpy / imageio-ffmpeg / playwright）",
        "steps": ["在本 skill 根目录一次装齐 requirements.txt。"],
        "commands": ['"%s" -m pip install -r "%s"' % (PY, _REQ)],
        "verify": '"%s" -c "import yt_dlp, numpy, imageio_ffmpeg, playwright; print(\'deps ok\')"' % PY,
    },
    "ytdlp": {
        "id": "ytdlp",
        "what": "Python 包 yt-dlp —— B站 / 小红书取源要用",
        "steps": ["只装这一个包也行；建议按 requirements.txt 一次装齐（命令见下）。"],
        "commands": ['"%s" -m pip install yt-dlp' % PY,
                     '"%s" -m pip install -r "%s"' % (PY, _REQ)],
        "verify": '"%s" -c "import yt_dlp; print(yt_dlp.version.__version__)"' % PY,
    },
    "ffmpeg": {
        "id": "ffmpeg",
        "what": "ffmpeg —— B站分离流合并、本地时长/尺寸探测要用",
        "steps": ["不必装系统 ffmpeg：imageio-ffmpeg 自带二进制，脚本会自动找它。"],
        "commands": ['"%s" -m pip install imageio-ffmpeg' % PY],
        "verify": '"%s" -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())"' % PY,
    },
    "playwright": {
        "id": "playwright",
        "what": "Python 包 playwright + 浏览器 —— 抖音 / 快手解析要用",
        "steps": ["先装 Python 包，再下浏览器；抖音实测需要 chromium-headless-shell。"],
        "commands": ['"%s" -m pip install playwright' % PY,
                     _PLAYWRIGHT_BROWSER,
                     '"%s" -m playwright install chromium' % PY],
        "verify": '"%s" -c "import playwright; print(\'ok\')"' % PY,
    },
    "shipinhao": {
        "id": "shipinhao",
        "what": "另一个 skill：shipinhao-batch-save（视频号批量存）—— 视频号下载唯一路径",
        "steps": [
            "在 WorkBuddy 技能市场搜索 `shipinhao-batch-save` 安装（skillId: skill_2110703652279099392）。",
            "它不进本仓库，换机必须单独装；装完应落在下面这个路径。",
            "首次使用要扫码登录一次（元宝凭证），凭证只保存在本机。",
        ],
        "commands": [
            '"%s" "%s" doctor' % (PY, SHIPINHAO),
            '"%s" "%s" login' % (PY, SHIPINHAO),
        ],
        "verify": '"%s" "%s" doctor' % (PY, SHIPINHAO),
        "expected_path": SHIPINHAO,
        "command_note": "可复制命令（装完该 skill 后执行）：",
    },
    "ct": {
        "id": "ct",
        "what": "另一个 skill：competitor-teardown（短视频文案提取与改写）—— 抖音 / 快手解析",
        "steps": [
            "在 WorkBuddy 技能市场搜索 `competitor-teardown` 安装（skillId: skill_2105881606948155392）。",
            "它不进本仓库，换机必须单独装；只用它的 douyin / kuaishou 解析模块。",
            "装完还要补齐 playwright 与浏览器，下面命令可直接复制。",
        ],
        "commands": [
            '"%s" -m pip install playwright' % PY,
            _PLAYWRIGHT_BROWSER,
        ],
        "verify": '"%s" -c "import playwright; print(\'ok\')"' % PY,
        "expected_path": CT_SCRIPTS,
    },
    "cookie": {
        "id": "cookie",
        "what": "视频号元宝凭证（COOKIE_* / YUANBAO_UNAUTH）",
        "steps": ["凭证不随仓库分发，换机或过期后要重新扫码；不影响其它平台。"],
        "commands": [
            '"%s" "%s" doctor' % (PY, SHIPINHAO),
            '"%s" "%s" login' % (PY, SHIPINHAO),
        ],
        "verify": '"%s" "%s" status' % (PY, SHIPINHAO),
    },
}


def with_hint(error, key):
    """给一条依赖类错误附上结构化安装提示。只返回 dict，不打印、不安装。"""
    return {"error": error, "install_hint": dict(INSTALL_HINTS[key])}


def print_install_hints(hints):
    """本次尝试里出现过的安装提示合并打到 stderr。

    stdout 必须保持纯 JSON（可直接 | jq），所以人读提示走 stderr。
    三级回退会覆盖 r["error"]，这里按 engine 收集，保证首个真实原因不丢。
    """
    if not hints:
        return
    lines = ["", "=" * 68,
             "环境未就绪：以下依赖不随本 skill 分发，请自行安装后重试",
             "=" * 68]
    seen = set()
    for engine, h in hints:
        if h["id"] in seen:
            continue
        seen.add(h["id"])
        lines += ["", "[%s] 缺少：%s" % (engine, h["what"])]
        for i, s in enumerate(h.get("steps", []), 1):
            lines.append("  步骤 %d  %s" % (i, s))
        if h.get("expected_path"):
            lines.append("  期望位置  %s" % h["expected_path"])
        if h.get("commands"):
            lines.append("  %s" % h.get("command_note", "可复制命令："))
            for c in h["commands"]:
                lines.append("    %s" % c)
        if h.get("verify"):
            lines += ["  验证：", "    %s" % h["verify"]]
    lines += ["", "=" * 68]
    sys.stderr.write("\n".join(lines) + "\n")
    sys.stderr.flush()


# ---------------------------------------------------------------- 各执行器

def via_shipinhao(url, outdir, dry_run):
    if not os.path.exists(SHIPINHAO):
        return with_hint("未安装 shipinhao-batch-save（视频号下载依赖它）", "shipinhao")
    os.makedirs(outdir, exist_ok=True)
    m_sid = re.search(r"(?:/sph/|[?&]id=)([A-Za-z0-9_-]{6,})", url or "")
    sid = m_sid.group(1) if m_sid else ""
    before = snap(outdir)
    if dry_run:
        rc, so, se = run([PY, SHIPINHAO, "parse", "--url", url], timeout=180)
        if rc != 0:
            err = (se or so)[-300:]
            if "YUANBAO_UNAUTH" in err or "COOKIE_" in err:
                return with_hint("解析失败（凭证问题）: %s" % err, "cookie")
            return {"error": "解析失败: %s" % err}
        try:
            d = json.loads(so[so.index("{"):])
        except Exception:
            return {"error": "解析输出无法识别: %s" % so[-200:]}
        title = d.get("title", "") or ""
        text, tags = split_tags(title)
        return {"title": title, "text": text, "tags": tags,
                "author": d.get("author", ""),
                "id": d.get("id") or d.get("sph_id") or "",
                "media_url": d.get("media_url") or d.get("url") or "",
                "engine": "shipinhao", "dry_run": True}
    rc, so, se = run([PY, SHIPINHAO, "download", "--url", url, "--out", outdir],
                     timeout=600)
    after = snap(outdir)
    files = new_media(before, after)
    res = {"engine": "shipinhao"}
    try:
        d = json.loads(so[so.index("{"):])
        it = (d.get("items") or [{}])[0]
        title = it.get("title", "") or ""
        text, tags = split_tags(title)
        res["title"] = title
        res["text"] = text
        res["tags"] = tags
        res["author"] = it.get("author", "")
        res["id"] = it.get("id") or it.get("sph_id") or sid
        if it.get("code"):
            code = str(it["code"])
            res["error"] = "%s: %s" % (code, it.get("message"))
            if code.startswith("COOKIE_") or code == "YUANBAO_UNAUTH":
                res["install_hint"] = dict(INSTALL_HINTS["cookie"])
    except Exception:
        pass
    if files:
        v = files[0]
        res.update(video=v, bytes=os.path.getsize(v))
    elif "error" not in res:
        # 同一个目录重跑时 wxvideo 会跳过已存在的文件，此时不算失败
        v = existing_media(outdir, res.get("id") or sid)
        if v:
            res.update(video=v, bytes=os.path.getsize(v),
                       note="本地已有该文件，复用未重新下载")
        else:
            res["error"] = "下载命令返回 0 但没找到新文件: %s" % (se or so)[-300:]
    return res


def via_ytdlp(url, outdir, dry_run):
    try:
        import yt_dlp
    except ImportError:
        return with_hint("yt-dlp 未安装：B站 / 小红书取源需要它", "ytdlp")
    os.makedirs(outdir, exist_ok=True)
    before = snap(outdir)
    opts = {
        "outtmpl": os.path.join(outdir, "%(id)s.%(ext)s"),
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        # 进度条走 stdout，会把本命令承诺的「顶层 JSON」弄脏：
        # 实测复用已有文件时仍打印一行 "[download] 100% of ..."，
        # 且用 \r 刷新，最后和 JSON 的 "{" 挤在同一行，管道解析直接失败。
        "noprogress": True,
        "restrictfilenames": True,
        "retries": 3,
        "socket_timeout": 30,
    }
    # B站等平台给的是分离的视频/音频流，必须 ffmpeg 合并。
    # 系统 PATH 里通常没有 ffmpeg，但 imageio_ffmpeg 自带一个二进制，
    # 把它的完整路径交给 yt-dlp（它支持直接指向可执行文件）。
    try:
        import imageio_ffmpeg
        _ff = imageio_ffmpeg.get_ffmpeg_exe()
        if _ff and os.path.exists(_ff):
            opts["ffmpeg_location"] = _ff
    except Exception:  # noqa: 没装就保持原行为，让 yt-dlp 自己报错
        pass
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=not dry_run)
    except Exception as e:
        msg = str(e)[:400]
        # 分离流合并失败的根因常是缺 ffmpeg，别让兜底把真因盖住
        if "ffmpeg" in msg.lower():
            return with_hint("yt-dlp 失败（缺 ffmpeg）: %s" % msg[:300], "ffmpeg")
        return {"error": "yt-dlp 失败: %s" % msg}
    if info is None:
        return {"error": "yt-dlp 未返回信息"}
    if "entries" in info:
        info = (info.get("entries") or [{}])[0]
    desc = info.get("description") or ""
    text, tags = split_tags(desc, info.get("tags") or [])
    res = {"engine": "yt-dlp",
           "title": info.get("title") or "",
           "author": info.get("uploader") or info.get("creator") or "",
           # 小红书只给 uploader_id，不给昵称；留着 ID 便于回溯作者主页
           "author_id": info.get("uploader_id") or info.get("channel_id") or "",
           "duration": info.get("duration"),
           "id": info.get("id") or "",
           "desc": desc,
           "text": text,
           "tags": tags,
           "upload_date": info.get("upload_date") or "",
           "stats": {"view": info.get("view_count"), "like": info.get("like_count"),
                     "comment": info.get("comment_count"),
                     "repost": info.get("repost_count")}}
    sub_langs = list((info.get("subtitles") or {}).keys())
    if sub_langs:
        res["subtitle_tracks"] = sub_langs
    if dry_run:
        res["dry_run"] = True
        res["has_subtitle"] = bool(sub_langs)
        return res
    after = snap(outdir)
    files = new_media(before, after)
    if not files:
        # yt-dlp 发现目标文件已存在会直接跳过下载，此时没有「新增文件」，
        # 按「新增」判定就会误报失败并触发无谓的兜底（视频号也有过同类问题）。
        # 只认合并后的 <id>.mp4，不要把分离流碎片（.f137.mp4 等）当成成功。
        key = res.get("id") or ""
        cand = os.path.join(outdir, key + ".mp4") if key else ""
        if (cand and os.path.isfile(cand)
                and os.path.getsize(cand) > 4096):
            files = [cand]
            res["note"] = "本地已有该文件，复用未重新下载"
    if files:
        v = files[0]
        res.update(video=v, bytes=os.path.getsize(v))
    else:
        res["error"] = "下载完成但没找到文件（可能被平台风控或需要登录态）"
    return res


def split_tags(desc, tags=None):
    """把描述里的 #话题 拆出来，同时给出「去掉话题的正文」。

    抖音的 desc 是正文和话题连在一起的：
      「…剩下全部交给WorkBuddy#WorkBuddy #AI #工作流」
    text_extra 字段经常少给末尾话题（实测 5 个只回来 4 个），所以用正则补齐。
    """
    got = [t for t in (tags or []) if t]
    for t in re.findall(r"#([^#\s]{1,40})", desc or ""):
        # 小红书的话题写作 #名[话题]#；尾部 [话题] 是平台标记，不是标签内容，
        # 不剥掉会拆出「苏越师姐」和「苏越师姐[话题]」两个重复标签。
        t = re.sub(r"\[话题\]$", "", t).strip()
        if t and t not in got:
            got.append(t)
    text = re.sub(r"#\S+", " ", desc or "")
    text = re.sub(r"\s+", " ", text).strip(" 　.。…、，,")
    return text, got


def via_ct_module(url, outdir, plat, dry_run):
    """直接调 competitor-teardown 的抓取模块，只取媒体文件，不跑它的 ASR。

    为什么要绕过它的 CLI：extract.py 带 --no-asr 时会在下载媒体之前就 return，
    不带 --no-asr 又会白跑一遍本地 ASR（精度不够，我们还要用内置转写重做）。
    这里只借它的 playwright 解析能力，拿到直链自己下。
    """
    if not os.path.isdir(CT_SCRIPTS):
        return with_hint("未安装 competitor-teardown（抖音 / 快手解析依赖它）", "ct")
    if CT_SCRIPTS not in sys.path:
        sys.path.insert(0, CT_SCRIPTS)
    os.makedirs(outdir, exist_ok=True)
    try:
        if plat == "douyin":
            import douyin as mod
            r = mod.fetch(url, timeout=90, quiet=True)
            if not r:
                return {"error": "抖音解析失败（三级降级全部失败）"}
            desc = r.get("desc", "") or ""
            text, tags = split_tags(desc, r.get("tags"))
            meta = {"title": desc, "text": text, "tags": tags,
                    "author": r.get("author", ""), "id": r.get("aweme_id", ""),
                    "duration_ms": r.get("duration_ms"),
                    "create_time": r.get("create_time"),
                    "stats": {"digg": r.get("digg"), "comment": r.get("comment"),
                              "collect": r.get("collect"), "share": r.get("share")},
                    "music": r.get("music") or "",
                    "source_layer": r.get("source") or ""}
            subs = r.get("subtitle_urls") or {}
            if subs:
                # 平台自带字幕轨：真实文字，比 ASR 准，能拿到就别转写
                meta["subtitle_tracks"] = list(subs.keys())
                meta["subtitle_urls"] = subs
            media = r.get("video_url")
            referer = "https://www.douyin.com/"
        elif plat == "kuaishou":
            import kuaishou as mod
            r = mod.fetch_meta(url, timeout=90, quiet=True, workdir=outdir)
            if not r:
                return {"error": "快手解析失败（多半是登录墙或链接失效）"}
            caption = r.get("caption", "") or ""
            text, tags = split_tags(caption, r.get("tags"))
            meta = {"title": caption, "text": text, "tags": tags,
                    "author": r.get("author", ""), "id": r.get("photo_id", ""),
                    "duration_ms": r.get("duration"),
                    "create_time": r.get("timestamp"),
                    "stats": {"view": r.get("view_count"), "digg": r.get("like_count"),
                              "comment": r.get("comment_count"),
                              "share": r.get("forward_count")},
                    "poi": r.get("poi") or "",
                    "source_layer": r.get("source") or ""}
            media = r.get("video_url") or r.get("audio_url")
            referer = None
        else:
            return {"error": "该平台不适用此执行器"}

        if dry_run:
            meta.update({"engine": "ct-module", "dry_run": True,
                         "has_media": bool(media)})
            if not media:
                meta["warning"] = "接口没给媒体直链，只能拿到标题文案"
            return meta
        if not media:
            meta.update({"error": "接口没给媒体直链，只能拿到标题文案",
                         "engine": "ct-module"})
            return meta

        dst = os.path.join(outdir, "%s.mp4" % (meta.get("id") or "media"))
        try:
            ok = mod.download(media, dst, referer=referer)
        except TypeError:
            ok = mod.download(media, dst)
        meta["engine"] = "ct-module"
        if ok and os.path.exists(dst) and os.path.getsize(dst) > 4096:
            meta.update({"video": dst, "bytes": os.path.getsize(dst)})
        else:
            meta["error"] = "媒体直链下载失败（可能已过期，重试一次）"
        return meta
    except Exception as e:
        msg = str(e)[:300]
        if isinstance(e, ImportError) or "playwright" in msg.lower():
            return with_hint("调用 competitor-teardown 模块失败（多半缺 playwright）: %s"
                             % msg, "playwright")
        return {"error": "调用 competitor-teardown 模块失败: %s" % msg}


def via_ct(url, outdir, dry_run, keep_work=None):
    """借 competitor-teardown 的 playwright 实现（快手唯一可行）。"""
    if not os.path.exists(CT):
        return with_hint("未安装 competitor-teardown（抖音 / 快手解析依赖它）", "ct")
    if dry_run:
        return {"error": "competitor-teardown 不支持 dry-run", "engine": "competitor-teardown"}
    work = keep_work or os.path.join(outdir, "_work_%d" % int(time.time()))
    os.makedirs(work, exist_ok=True)
    rc, so, se = run([PY, CT, url, "--no-asr", "--workdir", work,
                      "--json", "--quiet"], timeout=600)
    files = new_media({}, snap(work))
    res = {"engine": "competitor-teardown", "workdir": work}
    try:
        d = json.loads(so[so.index("{"):])
        res["title"] = d.get("caption") or d.get("title") or ""
        res["author"] = d.get("author") or ""
        if d.get("transcript"):
            res["subtitle"] = d["transcript"]
        if d.get("error"):
            res["error"] = d["error"]
    except Exception:
        if rc != 0:
            res["error"] = "执行失败: %s" % (se or so)[-400:]
    if files:
        v = files[0]
        # 搬到 outdir 根目录，避免和 workdir 混在一起
        dst = os.path.join(outdir, os.path.basename(v))
        try:
            if os.path.abspath(v) != os.path.abspath(dst):
                os.replace(v, dst)
            res.update(video=dst, bytes=os.path.getsize(dst))
        except Exception:
            res.update(video=v, bytes=os.path.getsize(v))
    elif "error" not in res:
        res["error"] = "没下到媒体文件（可能该条作品是图文，或需要登录态）"
    return res


# ---------------------------------------------------------------- 统一字段
# 每个平台能给的东西不一样，但落盘必须是同一套字段（见 SKILL.md「采集字段表」）。
# 缺的字段留空，不要用别的东西冒充，否则下游分不清「没有」和「没采到」。

PLATFORM_NAMES = {
    "wechat": "视频号", "douyin": "抖音", "kuaishou": "快手",
    "xiaohongshu": "小红书", "bilibili": "B站", "local": "本地文件",
}

RECORD_FIELDS = [
    "platform", "platform_name", "id", "url", "author", "author_id",
    "title", "description", "description_text", "tags",
    "duration_sec", "duration_text", "publish_time", "publish_ts",
    "width", "height", "resolution", "collected_at",
    "stats", "media", "has_audio", "subtitle_tracks",
    "transcript", "transcript_raw", "corrections",
    "transcript_source", "form", "key_points",
    "extras", "notes",
]


def fmt_dur(sec):
    try:
        sec = float(sec or 0)
    except Exception:  # noqa
        return ""
    if sec <= 0:
        return ""
    m, s = divmod(int(round(sec)), 60)
    h, m = divmod(m, 60)
    return ("%d:%02d:%02d" % (h, m, s)) if h else ("%02d:%02d" % (m, s))


def fmt_ts(ts):
    """秒级时间戳 → 可读时间。有些平台给毫秒，先归一。"""
    try:
        ts = int(float(ts))
    except Exception:  # noqa
        return ""
    if ts > 1e11:
        ts //= 1000
    if ts <= 0:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:  # noqa
        return ""


def fmt_upload_date(s):
    """yt-dlp 的 upload_date 是 YYYYMMDD，没有时间部分。"""
    s = str(s or "")
    if len(s) == 8 and s.isdigit():
        return "%s-%s-%s" % (s[:4], s[4:6], s[6:])
    return s


def probe_media(path):
    """取尺寸 / 时长 / 有无音轨。优先用 imageio-ffmpeg 自带的 ffmpeg。"""
    out = {"duration_sec": None, "width": None, "height": None,
           "resolution": "", "has_audio": None}
    if not path or not os.path.exists(path):
        return out
    exe = None
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa
        exe = "ffmpeg"
    try:
        r = subprocess.run([exe, "-i", path], capture_output=True,
                           text=True, errors="replace", timeout=60)
        e = r.stderr or ""
    except Exception:  # noqa
        return out
    for line in e.splitlines():
        if "Duration:" in line and out["duration_sec"] is None:
            d = line.split("Duration:")[1].split(",")[0].strip()
            try:
                h, m, s = d.split(":")
                out["duration_sec"] = int(h) * 3600 + int(m) * 60 + float(s)
            except Exception:  # noqa
                pass
        if "Stream #" in line and "Video:" in line and not out["resolution"]:
            m = re.search(r"(\d{2,5})x(\d{2,5})", line)
            if m:
                out["width"], out["height"] = int(m.group(1)), int(m.group(2))
                out["resolution"] = "%s×%s" % (m.group(1), m.group(2))
        if "Stream #" in line and "Audio:" in line:
            out["has_audio"] = True
    if out["has_audio"] is None:
        out["has_audio"] = False
    return out


def build_record(plat, url, m, media_path=""):
    """把各引擎的原始返回压成统一字段。"""
    desc = m.get("desc") or m.get("description") or ""
    title = m.get("title") or ""
    # 抖音/快手/视频号的 title 就是描述；yt-dlp 的 title 是标题、desc 是简介
    if not desc:
        desc = title
    text = m.get("text") or ""
    tags = m.get("tags") or []
    if not text:
        text, tags = split_tags(desc, tags)

    dur = m.get("duration") or (m.get("duration_ms") or 0) / 1000.0 or 0
    info = probe_media(media_path)
    if info["duration_sec"]:
        dur = info["duration_sec"]

    st = m.get("stats") or {}
    stats = {"like": st.get("like", st.get("digg")),
             "comment": st.get("comment"),
             "collect": st.get("collect"),
             "share": st.get("share", st.get("repost")),
             "view": st.get("view")}

    # 抖音/快手/视频号没有独立标题字段，title 就是整段描述。
    # 直接照抄会让「标题」和「发布描述」一模一样、还拖着话题，
    # 所以取描述首句做一个短标题，并标 derived，渲染时注明来源。
    title_derived = False
    if plat in ("douyin", "kuaishou", "wechat") and title:
        head = re.split(r"[。！？!?\n]", text or title)[0].strip()
        if head and len(head) < len(title.strip()):
            title = head if len(head) <= 60 else head[:60] + "…"
            title_derived = True

    pub_ts = m.get("create_time") or m.get("timestamp")
    pub_time = fmt_ts(pub_ts) or fmt_upload_date(m.get("upload_date"))

    rec = {
        "schema": "video-copy-extract/1.0",
        "platform": plat,
        "platform_name": PLATFORM_NAMES.get(plat, plat),
        "id": m.get("id") or "",
        "url": url,
        "author": m.get("author") or "",
        "author_id": m.get("author_id") or "",
        "title": title,
        "title_derived": title_derived,
        "description": desc,
        "description_text": text,
        "tags": tags,
        "duration_sec": round(float(dur), 2) if dur else None,
        "duration_text": fmt_dur(dur),
        "publish_time": pub_time,
        "publish_ts": pub_ts,
        "width": info["width"], "height": info["height"],
        "resolution": info["resolution"],
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stats": stats,
        "media": {"path": media_path or "", "bytes": os.path.getsize(media_path)
                  if media_path and os.path.exists(media_path) else 0},
        "has_audio": info["has_audio"],
        "subtitle_tracks": m.get("subtitle_tracks") or [],
        # 下面几项由 AI 在对话里填（脚本拿不到），见 record.py set / proofread.py
        "transcript": "",
        "transcript_raw": "",
        "corrections": [],
        "transcript_source": "",
        "form": "",
        "key_points": [],
        "extras": {k: v for k, v in {
            "music": m.get("music"), "poi": m.get("poi"),
            "source_layer": m.get("source_layer"),
            "engine": m.get("engine"),
            # 短链展开后的规范 URL（含 xsec_token，有时效），原始短链仍在 url 字段
            "resolved_url": m.get("resolved_url"),
            "shortlink_note": m.get("shortlink_note"),
        }.items() if v},
        "notes": [],
    }
    return rec


def write_record(outdir, rec):
    """落标准记录。重爬同一条时要保住 AI 已填的字段，以及校对结果，不然转写白做。"""
    rid = rec.get("id") or os.path.splitext(os.path.basename(rec["media"]["path"]))[0] or "record"
    try:
        os.makedirs(outdir, exist_ok=True)
        p = os.path.join(outdir, "%s_meta.json" % rid)
        KEEP = ("transcript", "transcript_raw", "corrections",
                "transcript_source", "form", "key_points", "notes")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    old = json.load(f)
                if old.get("id") == rec.get("id"):
                    for k in KEEP:
                        if old.get(k):
                            rec[k] = old[k]
            except Exception:  # noqa
                pass
        with open(p, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
        return p
    except Exception:  # noqa
        return None


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-engine", choices=["shipinhao", "yt-dlp", "competitor-teardown"])
    a = ap.parse_args()

    raw = a.target.strip()
    # 先看是不是本地文件；不是的话从分享文案里抠链接
    if os.path.isfile(raw):
        t = raw
    else:
        t = clean_url(raw)

    outdir = a.out or os.path.join(HOME, "Downloads", "视频取源",
                                   time.strftime("%Y-%m-%d"))
    os.makedirs(outdir, exist_ok=True)

    # 本地文件：直接返回，不下载
    if os.path.isfile(t):
        rec = build_record("local", "", {"title": os.path.splitext(os.path.basename(t))[0],
                                         "id": ""}, os.path.abspath(t))
        out = {"ok": True, "platform": "local", "engine": "none",
               "video": os.path.abspath(t), "bytes": os.path.getsize(t),
               "title": rec["title"], "author": "", "outdir": outdir,
               "record": rec}
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    plat = detect(t)
    if not plat:
        print(json.dumps({"ok": False, "platform": "", "outdir": outdir,
                          "error": "无法识别平台。支持：视频号 / B站 / 抖音 / 小红书 / 快手 / 本地文件"},
                         ensure_ascii=False, indent=2))
        return 1

    t0 = time.time()
    eng = a.force_engine
    r = None
    tried = []
    hints = []          # [(engine, install_hint)]，三级回退也要留住首个真实原因

    # 小红书分享短链先展开成带 xsec_token 的规范 URL，再交给 yt-dlp。
    # 记录里的 url 仍存原始短链（便于回溯），展开链接放 extras.resolved_url。
    origin_url = t
    shortlink_note = ""
    if plat == "xiaohongshu":
        t, shortlink_note = expand_shortlink(t)

    def attempt(name, fn):
        nonlocal r
        tried.append(name)
        r = fn()
        if r and r.get("install_hint"):
            hints.append((name, r["install_hint"]))
        return r and not r.get("error")

    if eng:
        table = {"shipinhao": lambda: via_shipinhao(t, outdir, a.dry_run),
                 "yt-dlp": lambda: via_ytdlp(t, outdir, a.dry_run),
                 "competitor-teardown": lambda: via_ct_module(t, outdir, plat, a.dry_run)}
        attempt(eng, table[eng])
        # 直连失败 → 退回它的 CLI（会白跑一次本地 ASR，慢，但成功率高）
        if eng == "competitor-teardown" and r and r.get("error") and not a.dry_run:
            attempt("ct-cli", lambda: via_ct(t, outdir, a.dry_run))
    else:
        # 视频号只有一条路
        if plat == "wechat":
            attempt("shipinhao", lambda: via_shipinhao(t, outdir, a.dry_run))
        # 抖音 / 快手：yt-dlp 不可用（抖音要 fresh cookies、快手无 extractor）
        elif plat in ("douyin", "kuaishou"):
            if not attempt("ct-module",
                           lambda: via_ct_module(t, outdir, plat, a.dry_run)) \
                    and not a.dry_run:
                attempt("ct-cli", lambda: via_ct(t, outdir, a.dry_run))
        # B站 / 小红书：yt-dlp 优先，失败逐级回退
        else:
            if not attempt("yt-dlp", lambda: via_ytdlp(t, outdir, a.dry_run)) \
                    and not a.dry_run:
                if not attempt("ct-module",
                               lambda: via_ct_module(t, outdir, plat, a.dry_run)):
                    attempt("ct-cli", lambda: via_ct(t, outdir, a.dry_run))

    r = r or {"error": "没有可用的执行器"}
    r["ok"] = not r.get("error")
    r["platform"] = plat
    if t != origin_url:
        r["resolved_url"] = t
    if shortlink_note:
        r["shortlink_note"] = shortlink_note
    r["outdir"] = outdir
    r["elapsed"] = round(time.time() - t0, 2)
    r["engines_tried"] = tried

    # 统一字段：成功了就落一份标准记录（采集表见 SKILL.md）
    if r["ok"] and not a.dry_run:
        rec = build_record(plat, origin_url, r, r.get("video") or "")
        p = write_record(outdir, rec)
        r["record"] = rec
        if p:
            r["record_path"] = p

    # 失败时把「缺什么 / 怎么装 / 命令」附进结果，并打到 stderr。
    # stdout 仍是纯 JSON（可直接 | jq），安装提示不污染它。
    if not r["ok"] and hints:
        r["install_hints"] = [{"engine": e, **h} for e, h in hints]
        print_install_hints(hints)

    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
