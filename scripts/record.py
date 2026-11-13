# -*- coding: utf-8 -*-
"""统一采集记录：填空 + 渲染成交付文本。

用法:
  python record.py show   <ID>_meta.json
  python record.py set    <ID>_meta.json> [--transcript 文件] [--form A]
                          [--source AudioTranscribe] [--key-points 文件]
                          [--title 标题] [--author 作者] [--note ...]
  python record.py render <ID>_meta.json> [--out xxx_文案.txt]

为什么要有这个脚本:
  1. 每个平台能给的字段不一样，但交付物必须是同一套顺序、同一套字段名；
  2. 「缺失」和「没有」必须能区分 —— 渲染时会单列「本次未采到」，不留空白格；
  3. transcript / key_points 是 AI 在对话里产出的，脚本只负责写进记录，不负责生成。
"""
import argparse
import difflib
import json
import os
import re
import sys

FIELDS_ORDER = [
    ("platform_name", "平台"),
    ("author", "作者"),
    ("title", "标题"),
    ("url", "链接"),
    ("duration_text", "时长"),
    ("publish_time", "发布日期"),
    ("resolution", "尺寸"),
    ("collected_at", "抓取日期"),
]

STAT_NAMES = [("like", "点赞"), ("comment", "评论"), ("collect", "收藏"),
              ("share", "转发"), ("view", "播放")]

MISSING = "—"


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(p, rec):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)


def is_empty(v):
    return v in (None, "", [], {})


def num(v):
    if v is None or v == "":
        return MISSING
    try:
        n = int(v)
    except Exception:  # noqa
        return str(v)
    if n >= 10000:
        return "%s（%s）" % (format(n, ","), "{:.1f}万".format(n / 10000.0))
    return format(n, ",")


def render(rec):
    L = []
    rid = rec.get("id") or os.path.splitext(os.path.basename(
        (rec.get("media") or {}).get("path") or ""))[0] or "记录"
    plat = rec.get("platform_name") or rec.get("platform") or ""
    L.append("=" * 40)
    L.append("%s · %s · 文案" % (rid, plat))
    L.append("=" * 40)
    L.append("")

    # 一、基本信息
    L.append("-" * 40)
    L.append("一、基本信息")
    L.append("-" * 40)
    L.append("")
    for key, label in FIELDS_ORDER:
        v = rec.get(key)
        line = "%s：%s" % (label, MISSING if is_empty(v) else v)
        if key == "title" and rec.get("title_derived"):
            line += "　（该平台无独立标题字段，取描述首句）"
        L.append(line)
    L.append("")

    # 二、标签
    L.append("-" * 40)
    L.append("二、标签（%d 个）" % len(rec.get("tags") or []))
    L.append("-" * 40)
    L.append("")
    tags = rec.get("tags") or []
    L.append("  ".join("#" + t for t in tags) if tags else "（该平台未提供话题标签）")
    L.append("")

    # 三、发布描述
    L.append("-" * 40)
    L.append("三、发布描述（作者原文）")
    L.append("-" * 40)
    L.append("")
    L.append(rec.get("description") or "（无描述）")
    L.append("")
    if rec.get("description_text") and rec["description_text"] != rec.get("description"):
        L.append("　　［去话题后的纯正文］")
        L.append(rec["description_text"])
        L.append("")

    # 四、口播文案
    src = rec.get("transcript_source") or ""
    corr = rec.get("corrections") or []
    L.append("-" * 40)
    L.append("四、口播文案" + ("（来源：%s）" % src if src else ""))
    L.append("-" * 40)
    L.append("")
    if corr:
        n_hits = sum(c.get("hits", 0) for c in corr)
        L.append("　　［已按 %d 条补丁校对 %d 处；原始转写完整保留在 transcript_raw，可回滚］"
                 % (len(corr), n_hits))
    L.append(rec.get("transcript") or "（未采集：本次没有做语音转写，原因见第八节）")
    L.append("")

    # 五、结构化要点
    L.append("-" * 40)
    L.append("五、结构化要点")
    L.append("-" * 40)
    L.append("")
    kp = rec.get("key_points") or []
    if kp:
        for i, x in enumerate(kp, 1):
            L.append("%d. %s" % (i, x))
    else:
        L.append("（未整理）")
    L.append("")

    # 六、数据
    L.append("-" * 40)
    L.append("六、数据")
    L.append("-" * 40)
    L.append("")
    st = rec.get("stats") or {}
    got = [(label, num(st.get(k))) for k, label in STAT_NAMES if not is_empty(st.get(k))]
    if got:
        L.append("  ｜  ".join("%s %s" % (a, b) for a, b in got))
    else:
        L.append("（该平台未提供公开数据）")
    L.append("")

    # 七、其他
    ex = rec.get("extras") or {}
    L.append("-" * 40)
    L.append("七、其他")
    L.append("-" * 40)
    L.append("")
    med = rec.get("media") or {}
    rows = []
    if med.get("path"):
        rows.append(("视频文件", os.path.basename(med["path"])))
        rows.append(("文件大小", "%s 字节（%.1f MB）" % (format(med.get("bytes") or 0, ","),
                                                    (med.get("bytes") or 0) / 1048576.0)))
    if rec.get("has_audio") is not None:
        rows.append(("音轨", "有" if rec["has_audio"] else "无"))
    if rec.get("subtitle_tracks"):
        rows.append(("平台字幕轨", "、".join(rec["subtitle_tracks"])))
    if ex.get("music"):
        rows.append(("背景音乐", ex["music"]))
    if ex.get("poi"):
        rows.append(("位置", ex["poi"]))
    if rec.get("form"):
        rows.append(("形态判定", rec["form"]))
    if corr:
        rows.append(("专名校对", "%d 条补丁 / %d 处替换（原稿见 transcript_raw）"
                     % (len(corr), sum(c.get("hits", 0) for c in corr))))
    if not rows:
        L.append("（无）")
    for a, b in rows:
        L.append("%s：%s" % (a, b))
    L.append("")

    # 八、缺失与待核对
    missing = [label for key, label in FIELDS_ORDER if is_empty(rec.get(key))]
    if is_empty(rec.get("transcript")):
        missing.append("口播文案")
    if not (rec.get("key_points") or []):
        missing.append("结构化要点")
    notes = rec.get("notes") or []
    if missing or notes:
        L.append("-" * 40)
        L.append("八、本次未采到 / 需核对")
        L.append("-" * 40)
        L.append("")
        if missing:
            L.append("未采到：%s" % "、".join(missing))
            L.append("　（平台未提供，或该步骤尚未执行；不要当成「没有」）")
        for n in notes:
            L.append("· %s" % n)
        L.append("")
    return "\n".join(L)


def cmd_set(a):
    rec = load(a.path)
    if a.title:
        rec["title"] = a.title
    if a.author:
        rec["author"] = a.author
    if a.form:
        rec["form"] = a.form
    if a.source:
        rec["transcript_source"] = a.source
    if a.transcript:
        rec["transcript"] = open(a.transcript, encoding="utf-8").read().strip()
    if a.text:
        rec["transcript"] = a.text.strip()
    if a.transcript or a.text:
        # 换了新稿 = 旧补丁的依据失效。同步刷新原始副本，避免拿旧原字校新稿。
        if rec.get("corrections"):
            print("提示：检测到 %d 条旧校对补丁，已随新转写稿清空" % len(rec["corrections"]))
        rec["transcript_raw"] = rec["transcript"]
        rec["corrections"] = []
    if a.key_points:
        raw = open(a.key_points, encoding="utf-8").read()
        rec["key_points"] = [x.strip(" -·\t") for x in raw.splitlines() if x.strip()]
    for n in (a.note or []):
        rec.setdefault("notes", []).append(n)
    save(a.path, rec)
    print("已更新：%s" % a.path)
    return 0


def cmd_render(a):
    rec = load(a.path)
    txt = render(rec)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(txt + "\n")
        print(a.out)
    else:
        sys.stdout.write(txt + "\n")
    return 0


def cmd_show(a):
    rec = load(a.path)
    for k in ["platform_name", "author", "id", "title", "duration_text",
              "publish_time", "resolution", "collected_at", "tags"]:
        print("%-12s %s" % (k, rec.get(k)))
    print("%-12s %s" % ("stats", rec.get("stats")))
    print("%-12s %s" % ("transcript", (rec.get("transcript") or "")[:40]))
    print("%-12s %s" % ("key_points", rec.get("key_points")))
    return 0


def cmd_sim(a):
    """判断「描述 ≈ 口播」还是「描述 ≠ 口播」。

    相似度 > 0.9 → 描述就是逐字稿，A′，直接用描述，省一次转写；
    否则说明口播另有内容（补充说明、口语扩写），必须用转写稿。
    """
    rec = load(a.path)
    d = re.sub(r"\s+", "", rec.get("description_text") or "")
    t = re.sub(r"\s+", "", rec.get("transcript") or "")
    if not d:
        print("描述为空，无从比对")
        return 1
    if not t:
        print("还没有转写稿：先跑 AudioTranscribe，再用 record.py set 写进来")
        return 1
    r = difflib.SequenceMatcher(None, d, t).ratio()
    verdict = ("描述≈口播，可直接用描述（A′）" if r >= 0.9
               else "描述≠口播，必须用转写稿")
    print("描述 %d 字 / 口播 %d 字 / 相似度 %.3f → %s" % (len(d), len(t), r, verdict))
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("set")
    s.add_argument("path")
    s.add_argument("--title")
    s.add_argument("--author")
    s.add_argument("--form")
    s.add_argument("--source")
    s.add_argument("--transcript")
    s.add_argument("--text")
    s.add_argument("--key-points")
    s.add_argument("--note", action="append")
    s.set_defaults(fn=cmd_set)

    r = sub.add_parser("render")
    r.add_argument("path")
    r.add_argument("--out")
    r.set_defaults(fn=cmd_render)

    w = sub.add_parser("show")
    w.add_argument("path")
    w.set_defaults(fn=cmd_show)

    m = sub.add_parser("sim")
    m.add_argument("path")
    m.set_defaults(fn=cmd_sim)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
