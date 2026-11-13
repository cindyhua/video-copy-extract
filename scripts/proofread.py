# -*- coding: utf-8 -*-
"""ASR 专名校对（video-copy-extract）。

分工原则：**脚本只做召回与落盘，判定交给人 / 大模型。**

  scan   从标题、描述、标签、画面原字里取「锚点」，把转写稿中可疑的
         英文词、文件名、专名变体全捞出来（宁可多报，不可漏报）。
  apply  只按「已确认的补丁」做精确子串替换；原始转写永久保存在
         transcript_raw，每次都从它重放全部补丁 —— 幂等、可增补、可回滚。
  show   查看当前校对状态。
  revert 回到原始转写（保留 raw，便于重来）。

为什么不直接把整篇稿子丢给大模型「改错别字」：
  1. ASR 这类错是**近音替换**，"scale到底是啥" 单看文字语法通顺，看不出来；
  2. 模型没有音频、没有锚点时会靠语言先验猜，容易把本来对的专名「改顺」；
  3. 整篇重写不可追溯、不可回滚，还会顺手改掉语气词。
所以脚本先把「可疑位置 + 上下文 + 候选原字」做成清单，模型只逐条判是与不是。

不做：不润色、不整篇重写、不判断中文同音字、不联网、不调模型。
"""
import argparse
import difflib
import json
import os
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa
    pass

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+/'’\-]*")
# 短语只在同一行内合并（[ \t] 而不是 \s）：锚点文件是一行一个词，
# 用 \s 会把整个文件吞成一个巨型「短语」，反而把 Nano Banana 之类拆散。
PHRASE_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.+/'’\-]*(?:[ \t]+[A-Za-z][A-Za-z0-9_.+/'’\-]*)*")
VOWELS = set("aeiouy")
CONS_MAP = {"c": "k", "q": "k", "z": "s", "x": "s", "v": "w", "j": "g"}

# 锚点只取「作者/平台原文」，不取 AI 产物。
# key_points / notes 是 AI 写的，可能已经把转写错字抄进去了 —— 拿错字校错字会越校越歪。
ANCHOR_FIELDS = ("title", "description", "description_text", "tags", "author")


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(p, rec):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)


def plain(s):
    """小写 + 只留字母数字（去掉空格/点/斜杠）。"""
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def skeleton(s):
    """辅音骨架：专抓『发音像、拼写不像』，例如 scale / skill → skl。

    ASR 把英文听错时，错的往往是元音和辅音替换，辅音骨架却大体保留。
    只靠编辑距离抓不到 scale→skill（距离 3），骨架比对可以。
    """
    t = plain(s)
    t = "".join(CONS_MAP.get(ch, ch) for ch in t)
    t = "".join(ch for ch in t if ch not in VOWELS)
    return re.sub(r"([a-z0-9])\1+", r"\1", t)


def ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def read_text(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def collect_anchors(rec, anchor_files=(), glossary=(), include_notes=False):
    """锚点 = 作者亲笔写的原字。返回 [{raw, plain, skeleton}]，按 raw 去重。"""
    chunks = []
    for key in ANCHOR_FIELDS:
        v = rec.get(key)
        if isinstance(v, list):
            chunks.extend(str(x) for x in v)
        elif v:
            chunks.append(str(v))
    if include_notes:
        chunks.extend(str(x) for x in (rec.get("notes") or []))
        chunks.extend(str(x) for x in (rec.get("key_points") or []))
    for p in list(anchor_files) + list(glossary):
        if os.path.exists(p):
            chunks.append(read_text(p))
        else:
            print("警告：锚点文件不存在，已跳过 %s" % p, file=sys.stderr)

    seen, out = set(), []
    for text in chunks:
        for s in PHRASE_RE.findall(text) + TOKEN_RE.findall(text):
            s = s.strip(" .,-")
            if len(s) < 2 or plain(s) in seen:
                continue
            seen.add(plain(s))
            out.append({"raw": s, "plain": plain(s), "skel": skeleton(s)})
    return out


def contexts(text, token, limit=3, span=26):
    out, pos = [], 0
    while len(out) < limit:
        i = text.find(token, pos)
        if i < 0:
            break
        out.append(text[max(0, i - span):min(len(text), i + len(token) + span)].replace("\n", " "))
        pos = i + len(token)
    return out


def scan(transcript, anchors):
    """只做召回。返回可疑清单，不改任何东西。"""
    tokens = TOKEN_RE.findall(transcript)
    freq = {}
    for t in tokens:
        freq[t] = freq.get(t, 0) + 1
    # 高频基准：出现 >= 5 次的 token（通常是被听对了的那个词）
    common = [t for t, n in freq.items() if n >= 5 and len(plain(t)) >= 3]

    suspects = {}
    for tok, cnt in freq.items():
        p = plain(tok)
        if len(p) < 3:
            continue
        sk = skeleton(tok)
        reasons, sug = [], []

        # A 写法不一致：只差大小写不算（中文口播里小写英文很正常）；
        # 差空格 / 点号才算，例如 claudecode → Claude Code
        for anc in anchors:
            if anc["plain"] == p and anc["raw"].lower() != tok.lower():
                reasons.append("写法与作者原字不一致（缺空格 / 点号）")
                sug.append(anc["raw"])
                break

        # B 近音变体：辅音骨架相同、拼写不同（scale ↔ skill）
        # 三重约束防误报：骨架至少 3 字符；字符相似度 >= 0.35
        # （实测 scale/skill=0.40 命中，excel/skill=0.20 排除）；
        # 文件名拿主干比对，这样 scale.md 也能被 skill.md 捞出来。
        stems = [p]
        if re.search(r"[./]", tok):
            st = plain(re.split(r"[./]", tok)[0])
            if st:
                stems.append(st)
        near = []
        for st in stems:
            sk_st = skeleton(st)
            if len(sk_st) < 3:
                continue
            for cand in [x["raw"] for x in anchors] + common:
                cp = plain(cand)
                if cp != st and skeleton(cand) == sk_st and ratio(st, cp) >= 0.35:
                    near.append(cand)
        if near:
            reasons.append("近音变体（辅音骨架 %s 相同）" % sk)
            sug.extend(near[:3])

        # C 乱码特征：超长连写、或连续 3 个以上相同字母
        if len(p) >= 14:
            reasons.append("超长连写，疑似 ASR 拼写错乱")
        if re.search(r"([a-z])\1{2,}", p):
            reasons.append("连续重复字母 >= 3")

        # D 与锚点 / 高频词高度相似但不完全相同。
        # 短词（API↔AI）和纯复数（Agents↔Agent）最容易被误报，先排除；
        # 文件名交给 B 规则，不跟单词比相似度。
        if len(p) >= 5 and not re.search(r"[./]", tok):
            best, best_r = None, 0.0
            for cand in [x["raw"] for x in anchors] + common:
                r = ratio(p, plain(cand))
                if r > best_r:
                    best, best_r = cand, r
            if best and 0.72 <= best_r < 1.0:
                bp = plain(best)
                if not (p == bp + "s" or bp == p + "s"):
                    reasons.append("与「%s」相似度 %.2f" % (best, best_r))
                    sug.append(best)

        if reasons:
            suspects[tok] = {
                "token": tok,
                "count": cnt,
                "reasons": sorted(set(reasons)),
                "suggest": list(dict.fromkeys(sug))[:4],
                "contexts": contexts(transcript, tok),
            }
    return {
        "anchors": [a["raw"] for a in anchors],
        "token_total": len(tokens),
        "unique_total": len(freq),
        "suspects": sorted(suspects.values(),
                           key=lambda x: (-len(x["reasons"]), -x["count"], x["token"])),
        "not_covered": [
            "中文同音字（如「秋芝」→ 秋之 / 求之 / 求职）：脚本无词表，需结合上下文人工判定",
            "纯语气词、断句、标点：不属于专名校对范围",
            "清单不是全集：没被列出的英文词只是「没发现异常特征」，不等于正确；"
            "术语表外的错拼请用 --glossary 补锚点",
        ],
    }


def replay(rec, corrections):
    """从原始转写重放全部补丁 —— 保证幂等，不会二次替换污染。"""
    raw = rec.get("transcript_raw") or rec.get("transcript") or ""
    if not raw:
        raise ValueError("transcript 为空，没有可校对的原始转写")
    out, applied = raw, []
    # from 之间可能互为子串（Sskill / Sskill.md / Sskilll）。长串先替换，
    # 短的才不会把长串切碎、导致后面的补丁在原文里找不到。
    ordered = sorted(corrections, key=lambda c: -len(c.get("from") or ""))
    for c in ordered:
        f, t = c.get("from"), c.get("to")
        if not f or t is None:
            raise ValueError("每条 correction 必须有 from / to：%r" % c)
        hits = out.count(f)
        if hits == 0:
            raise ValueError("补丁在原始转写里找不到：from=%r（整批未落盘）" % f)
        out = out.replace(f, t)
        applied.append({**c, "hits": hits})
    return raw, out, applied


def cmd_scan(a):
    rec = load(a.path)
    anchors = collect_anchors(rec, a.anchor_file, a.glossary, a.include_notes)
    result = scan(rec.get("transcript_raw") or rec.get("transcript") or "", anchors)
    result["id"] = rec.get("id") or ""
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
    if a.json or not a.out:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print("锚点（%d）：%s" % (len(anchors), "、".join(result["anchors"][:18])))
    print("转写 %d 个英文 token / %d 种；可疑 %d 种\n"
          % (result["token_total"], result["unique_total"], len(result["suspects"])))
    for s in result["suspects"]:
        print("· %-26s ×%-3d  %s" % (s["token"], s["count"], "；".join(s["reasons"])))
        if s["suggest"]:
            print("    候选：%s" % " / ".join(s["suggest"]))
        for c in s["contexts"][:1]:
            print("    上下文：…%s…" % c)
    print("\n未覆盖：" + "；".join(result["not_covered"]))
    print("清单：%s" % a.out)
    return 0


def cmd_apply(a):
    rec = load(a.path)
    patch = load(a.patch)
    corrections = patch.get("corrections")
    if not isinstance(corrections, list):
        raise ValueError("补丁文件必须含 corrections 数组")
    raw, out, applied = replay(rec, corrections)          # 先校验，任何一条失败都不落盘
    for c in applied:
        if not c.get("evidence"):
            print("警告：from=%r 没写依据，建议补 evidence" % c["from"], file=sys.stderr)
    print("共 %d 条补丁 / %d 处替换" % (len(applied), sum(c["hits"] for c in applied)))
    for c in applied:
        print("  %r → %r  ×%d" % (c["from"], c["to"], c["hits"]))
    if a.dry_run:
        print("dry-run：未写入")
        return 0
    rec["transcript_raw"] = raw
    rec["transcript"] = out
    rec["corrections"] = applied
    save(a.path, rec)
    print("已写入：%s（original 保留在 transcript_raw）" % a.path)
    return 0


def cmd_show(a):
    rec = load(a.path)
    corr = rec.get("corrections") or []
    raw = rec.get("transcript_raw")
    print("id                 %s" % rec.get("id"))
    print("原始转写           %s（%d 字）" % ("已保留" if raw else "尚无", len(raw or "")))
    print("当前口播           %d 字" % len(rec.get("transcript") or ""))
    print("已确认补丁         %d 条 / %d 处" % (len(corr), sum(c.get("hits", 0) for c in corr)))
    for c in corr:
        print("  %r → %r  ×%s  [%s]" % (c["from"], c["to"], c.get("hits"),
                                        c.get("confidence", "未标")))
        if c.get("evidence"):
            print("      依据：%s" % c["evidence"])
    return 0


def cmd_revert(a):
    rec = load(a.path)
    raw = rec.get("transcript_raw")
    if not raw:
        print("没有 transcript_raw，无法回滚", file=sys.stderr)
        return 1
    if a.dry_run:
        print("dry-run：将回滚到 %d 字原始转写" % len(raw))
        return 0
    rec["transcript"] = raw
    rec["corrections"] = []
    save(a.path, rec)
    print("已回滚到原始转写（transcript_raw 保留，可重新 apply）")
    return 0


def main():
    ap = argparse.ArgumentParser(description="ASR 专名校对：脚本召回 + 精确替换，不猜不润色")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="生成可疑词清单（只读）")
    s.add_argument("path")
    s.add_argument("--anchor-file", action="append", default=[],
                   help="画面原字 / 字幕条抄本，可重复给")
    s.add_argument("--glossary", action="append", default=[],
                   help="术语表文件（每行一个词），可重复给")
    s.add_argument("--include-notes", action="store_true",
                   help="把 notes/key_points 也当锚点（默认不用，它们可能已抄了错字）")
    s.add_argument("--out", help="写 JSON 清单")
    s.add_argument("--json", action="store_true", help="同时打印 JSON")
    s.set_defaults(fn=cmd_scan)

    p = sub.add_parser("apply", help="按确认过的补丁精确替换")
    p.add_argument("path")
    p.add_argument("--patch", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_apply)

    w = sub.add_parser("show", help="查看校对状态")
    w.add_argument("path")
    w.set_defaults(fn=cmd_show)

    v = sub.add_parser("revert", help="回滚到原始转写")
    v.add_argument("path")
    v.add_argument("--dry-run", action="store_true")
    v.set_defaults(fn=cmd_revert)

    a = ap.parse_args()
    try:
        return a.fn(a)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print("错误：%s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
