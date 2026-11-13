---
name: video-copy-extract
version: 1.3.3
display_name: 多平台视频文案提取
display_name_en: Multi-platform Video Copy Extraction
description_zh: 给一条视频链接（视频号 / 抖音 / 快手 / 小红书 / B站）或本地音视频文件，取出视频并拿到完整资料：平台、作者、标题、链接、时长、发布日期、尺寸、抓取日期、话题标签、发布描述、口播文案、结构化要点、点赞评论收藏转发等数据。会先判断文案到底在音轨里还是在画面里：口播走语音转写，纯文字卡走抽帧读图，屏幕上有大段文字就两者合并。视频文案提取、视频号文案、抖音文案怎么提取、口播稿提取、字幕卡识别、把视频转成文字、视频信息采集。
description_en: Turn a video link (WeChat Channels / Douyin / Kuaishou / Xiaohongshu / Bilibili) or a local file into a full record — author, title, URL, duration, publish date, resolution, crawl date, hashtags, description, transcript, key points and engagement stats — and decides first whether the copy lives in the audio or on screen.
author: xin.hua
agent_created: true
---

# 多平台视频文案提取

## 零、这个技能替用户做的事

用户只做两件事：**给链接**（或给文件）、**说要文案还是要视频**。
其余全自动：取源 → 采集元信息 → 判断文案位置 → 出逐字稿 → 落一份标准记录。

**不要一上来就转写。** 先判断文案在哪个通道，这是这个技能和「随便调个 ASR」的全部区别。
**也不要只交一段文字。** 用户要的是一份可检索、可比对的完整记录，字段见第二节。

---

## 一、先判断：文案在哪？（这一步决定后面所有动作）

| 形态 | 特征 | 文案所在 | 怎么做 |
|---|---|---|---|
| **A · 纯口播** | 真人／配音在说话 | 音轨 | 调 `AudioTranscribe` |
| **A′ · 口播＋长描述** | 描述区有整段文字 | 音轨＋描述 | **优先直接用描述**，省一次转写 |
| **B · 口播＋屏幕大段字** | 有人说，同时屏幕上挂着提示词/清单/代码 | 音轨 **＋** 画面 | **两样都要**，转写＋读图后合并 |
| **C · 纯文字卡** | 无口播，画面是字 | 画面 | 抽帧读图 |

判断方法（按成本从低到高）：

1. 先看发布描述（`fetch.py` 带回 `title / description / tags`）。**描述常常就是全文**，A′ 到此结束。
   注意：**描述长 ≠ 是口播稿**。实测抖音有一条描述只有一句话＋5 个话题，口播却是完整分步讲解，
   这种必须转写，见 `references/measured-cases.md`。
2. 再跑 `frames.py probe` 看画面是不是「图卡＋大字」。
3. 有音轨就转写一次，把转写内容和画面文字比对：**内容不同 = B，内容重复 = 只是字幕**。

> B 类最容易做错：以为「屏幕上有字就不用转写」。屏幕字是**要复制的原文**，
> 口播是**顺序和逻辑**，缺一个都要重跑。

---

## 二、采集字段（每次必采，缺项显式写「未采到」）

`fetch.py` 已在落地时写成 `<ID>_meta.json`，字段固定为下表。
**「未采到」不等于「没有」**：渲染时单列一段说明，不要留空白格让用户猜。

| 字段 | 说明 | 谁能给 |
|---|---|---|
| `platform` / `platform_name` | 平台 | 全部 |
| `author` / `author_id` | 作者 | 全部（ID 多数平台不给） |
| `title` | 标题。抖音/快手/视频号**没有独立标题字段**，取描述首句，并标 `title_derived` | 全部 |
| `url` | 原始链接（分享短链也保留，便于回溯） | 全部 |
| `duration_sec` / `duration_text` | 时长（秒 / `mm:ss`） | 本地探测，最准 |
| `publish_time` / `publish_ts` | 发布日期 | 抖音✅ 快手✅ B站✅ / **视频号❌** |
| `width` / `height` / `resolution` | 尺寸，如 `1920×1080` | 本地探测 |
| `collected_at` | 抓取日期 | 自动 |
| `tags` | 话题标签（数组） | 抖音✅ 视频号✅ 快手✅ / yt-dlp 视平台 |
| `description` | 发布描述**原文**（含话题、保留换行） | 全部 |
| `description_text` | 去话题后的纯正文 | 自动拆 |
| `transcript` / `transcript_source` | 口播文案及其来源（转写 / 平台字幕 / 读图 / 描述） | AI 填 |
| `transcript_raw` | **未经校对**的原始转写，永不覆盖（`proofread.py` 首次落盘时写入） | 脚本 |
| `corrections` | 已确认的专名补丁数组：`from / to / evidence / confidence / hits` | AI 判定 + 脚本 |
| `key_points` | 结构化要点（数组） | AI 填 |
| `stats` | 点赞 / 评论 / 收藏 / 转发 / 播放 | 抖音✅ 快手✅ / yt-dlp 视平台 |
| `media` | 视频路径与字节数 | 全部 |
| `has_audio` / `subtitle_tracks` | 有无音轨、平台字幕轨 | 本地探测 / 平台 |
| `form` | 形态判定（A / A′ / B / C） | AI 填 |
| `extras` | 音乐、位置、解析层级等零碎 | 视平台 |
| `notes` | 需核对项 | AI 填 |

**扩展约定**：以后要加字段（比如「是否带字幕条」「是否含广告」），
加到 `extras` 里，**不要动上面这些键名**——下游渲染和对比都依赖固定键。

---

## 三、标准流程

### 步骤 1 · 取源 + 采集元信息

```bash
python scripts/fetch.py "<链接或分享文本或本地文件>" [--out <目录>] [--dry-run]
```

一条命令同时完成：解析 → 下载 → 探测尺寸时长 → 拆话题 → 写 `<ID>_meta.json`。

返回 JSON 顶层是执行结果（`ok / platform / video / bytes / engine / elapsed`），
`record` 就是那份标准记录，`record_path` 是它落盘的位置。
**stdout 保证是纯 JSON**（已用 `noprogress` 关掉 yt-dlp 进度条），可直接 `| jq` 解析。
依赖缺失时的人读提示（缺什么、怎么装、可复制命令）走 **stderr**，并作为
`install_hints` 字段一并进 JSON，不污染 stdout。

- `--dry-run` 只解析不下载，用来确认链接对不对、是不是要的那条（此时不写记录）。
- `platform=local` 表示给的是本地文件，直接进入下一步。
- 同一目录重跑同一条时不会重下（文件名含作品 ID 就复用），也**不会覆盖已填的转写和要点**。
- **小红书短链会自动展开**：`xhslink.cn` 先用移动端 UA 跟一次 302，拿到带 `xsec_token`
  的 `/discovery/item/<id>` 规范 URL 再交给 yt-dlp。`url` 字段仍存原始短链，
  展开链接放 `extras.resolved_url`（`xsec_token` 有时效，长期回溯要重新展开）。

### 步骤 2 · 侦察（10 秒内出结论）

```bash
python scripts/frames.py probe "<video>" --out <帧目录>            # ≤ 3 分钟
python scripts/frames.py probe "<video>" --out <帧目录> --fps 0.2  # 长视频必加
```

返回：时长 / 分辨率 / 有无音轨 / **帧间差异 `mad_median`** / 一句 `judgement` / 若干拼图。

> **长视频先降 fps。** 实测 19:18 的片子用默认 `fps=1` 抽了 1158 帧、
> 耗时 **5 分 16 秒**、产出 129 张拼图；而转写那边 19 分钟是一次就能返回的。
> 降 fps 后 `mad_median` 数值会变，**不要拿它跟短视频的基准值跨 fps 比较**。
> 要看某一段就用 `frames.py sheet --start <秒> --dur 60`。

| `mad_max` | 含义 | 抽帧密度 |
|---|---|---|
| < 1 | 文字几乎不变 | **1 帧**（用 `full` 取原分辨率） |
| 1 ~ 中等 | 偶尔变化 | 先 1 帧，再针对变化点补 |
| 明显高 | 逐句／逐卡变化 | 1~2s/帧 |
| 很高 | 快速切换 | 0.5s/帧 |

> **真人口播是这张表的例外。** 人脸＋手势持续运动时 `mad_median` 本来就高
> （S5 小红书实测 **16.968**、S7 抖音 **12.353**），但帧差是均匀的、没有「切卡尖峰」，
> 拿 `med×3` 当阈值会算出 0 个变化点。此时 `judgement` 会明确说
> 「画面持续运动…帧差判不了文字卡切换」，**别把它读成「文字不变」**；
> 形态直接看 sheets 定，字幕校对用 `full` 取原分辨率帧。
>
> **注意这是「判不了」，不是「没有卡片」。** S7 明明穿插了十几张全屏资料卡、
> `mad_max` 冲到 120.872，中位数仍被真人段拉平；只看 `judgement` 会漏掉整段屏幕原文。
> 该分支阈值 5.0 只适用于默认 `fps=1` 的侦察。

### 步骤 3 · 取文案（按形态分流）

**A / A′**
- 先判是不是 A′：跑完转写后 `record.py sim <ID>_meta.json`，**相似度 ≥ 0.9 就是 A′**，
  描述可直接当全文；< 0.9 说明口播另有内容，必须用转写稿（实测两条：0.955 与 0.241，区分很清楚）。
- A′：直接用记录里的 `description_text`，**到步骤 4**。
- A：把 `media.path` 交给内置工具 `AudioTranscribe`。
  ⚠️ 这是**客户端内置工具，不是脚本**，必须在对话里直接调用，不能 `subprocess`。
- **有硬字幕条时加一步**：很多口播视频底部烧着作者打的字幕，那是**原字**，比转写准。
  `frames.py full --at <任一句的秒数>` 抽 1 帧读字幕条，专门用来校对英文产品名、人名、术语。
  把读到的原字抄成「一行一个词」的文本，交给步骤 3.5 当锚点。

**C**
```bash
python scripts/frames.py full "<video>" --at 4 --out <帧目录>
```
然后用 Read 工具看图，逐字抄下来。多张卡片就 `--at 4,12,20,...` 分批取。

**B**
1. `AudioTranscribe` 拿口播稿；
2. `frames.py full --at ...` 拿屏幕文字；
3. **合并**：以屏幕文字为原文主体（它才是要复制的），用口播补齐顺序、过渡和口头解释；重复的部分只留一份。

> 2026-11-06 首次跑到 B 类真样本（19 分钟 Skill 教程）：抽 2 张原帧读出了转写里
> **完全没有**的原文（`SKILL.md` 元信息、`references/scripts/assets`、指令条目），
> 同时校出转写把 `SKILL.md` 听成「scale」、`Claude Code` 听成乱码。
> 这验证了「读图有用」，但**未做全片逐段对齐**——长视频先按关键帧取样，别承诺全片合并。
>
> 2026-11-09 S6（抖音 5:48 竖屏教程）补了一条更实用的经验：**屏幕原文有时是作者
> 特意放大贴出来给人复制的**。150s 白框里写着「具体提示词（方便大家截图提取文字）」，
> 整段 prompt 口播里一个字都没念。这类内容必须 `full` 原帧逐字抄下来，
> 单独存一份「屏幕原文」，并写进要点；只交口播稿等于把最值钱的部分漏了。
>
> 2026-11-10 S7（抖音 1:35）补了**滚动长文字**的解法：73s / 74s / 75s 是同一张
> 「结构化提示词」长图在向上滚动，单帧只露一段、底部还被头像和「完整提示词见评论区」条挡住。
> 做法是 `--at 73,74,75`（`--at` 支持小数秒）连续抽原帧，按滚动顺序**拼接去重**得到全文。
> 形态比 S6 更杂：真人口播＋硬字幕＋全屏资料卡＋滚动文字＋模拟 AI 对话窗口。
> 作者标了「完整提示词见评论区」，评论区版本取不到，交付里要写明只保证**画面展示版**。

### 步骤 3.5 · 专名校对（稿子里有英文 / 产品名 / 文件名时必做）

ASR 对英文词、缩写、文件名、人名很不稳定。实测同一条 19 分钟稿里
`SKILL.md` 被听成 `scale.md / sskill.md / Sskill.md`，`Claude Code` 被听成
`ClaudeCcloududecodede`，`skill` 被听成 `scale`（用户举的那句「scale到底是啥」）。

**不要把整篇稿子丢给大模型「改错别字」。** 三个理由：

1. 这类错是**近音替换**，「scale到底是啥」单看文字完全通顺，看不出来；
2. 模型没有音频和锚点时会按语言先验猜，容易把本来对的专名「改顺」、顺手润色语气词；
3. 整篇重写不可追溯、不可回滚，下次重跑又要重来。

正确分工：**脚本负责召回和落盘，AI 只逐条判定。**

```bash
# 1) 召回：只用作者原字当锚点，输出可疑清单（只读，不改任何东西）
python scripts/proofread.py scan "<ID>_meta.json" \
    --anchor-file <画面原字.txt> --out <ID>_可疑词.json
#    锚点默认取 title / description / tags / author —— 这些是作者亲笔；
#    key_points / notes 默认不用，它们可能已抄了错字（--include-notes 才开）
#    画面原字 = frames.py full 读图抄本，一行一个词；自定义术语用 --glossary

# 2) 判定：AI 或人工逐条看「上下文 + 候选原字」，写补丁 JSON
#    每条必须带 evidence；低置信度写进 rejected，别塞进 corrections

# 3) 落盘（先 dry-run 看命中数）
python scripts/proofread.py apply  "<ID>_meta.json" --patch <ID>_校对补丁.json --dry-run
python scripts/proofread.py apply  "<ID>_meta.json" --patch <ID>_校对补丁.json
python scripts/proofread.py show   "<ID>_meta.json"     # 看状态与依据
python scripts/proofread.py revert "<ID>_meta.json"     # 一键回滚
```

补丁格式（可另带一个 `rejected` 数组记录「不采纳」的理由）：

```json
{"corrections": [
  {"from": "scale到底是啥", "to": "skill到底是啥",
   "evidence": "标题 Agent Skills + 后文「到底什么是skill」", "confidence": "high"}
]}
```

五条纪律：

- **精确子串替换**，不正则、不润色。`from` 尽量带上下文；互为子串的
  （`Sskill` / `Sskill.md` / `Sskilll`）脚本自动按长度降序重放，不会互相切碎。
- `transcript_raw` 存原始转写且**永不改写**，每次 apply 都从它重放全部补丁 → 幂等、可增补、可回滚。
- 任一补丁在原文里找不到 → **整批不落盘**，不会改出半份稿子。
- **清单不是全集**：没被列出只说明「没发现异常特征」。已确认两类漏报：
  ①中文同音字（秋芝→秋之/求之/求职）脚本无词表判不了；
  ②**连读融合词**——S7 把「GPT、Claude、Gemini」听成 `gptloud的Gemini`，
  加 50 个画面锚点后 `scan` 仍报 **0 可疑**（新词的长度、辅音骨架、相似度全不命中）。
  凡口播出现「比如像 A、B、C」这类**列举句**，一律 `full` 抽帧读画面标签／硬字幕核对。
  低置信项留在 `rejected` 和 `notes` 里，别硬改。
- 内置 `AudioTranscribe` 调用时只提交音频文件，**未暴露热词 / 词汇表参数**
  （依据客户端调用代码），所以上游喂不了术语表，只能走下游校对。这是能力边界，不是流程偷懒。

> 校对完必须回到步骤 5 重新 `render`，交付文件才会带上校对说明。

### 步骤 4 · 写进记录（AI 填的三项）

```bash
python scripts/record.py set "<ID>_meta.json" \
    --transcript <转写稿.txt> --source "内置 AudioTranscribe（云端）" \
    --form "A · 纯口播（软件界面录屏 + 作者硬字幕条）" \
    --key-points <要点.txt> \
    --note "「装载」与字幕条一致，判定为作者原话"
```

`--text` 可直接传字符串（短稿时用），`--note` 可重复多次（写进「需核对」段）。
若用 `--transcript / --text` 换了新稿，会同步刷新 `transcript_raw` 并清空旧补丁
（依据已失效），不会拿旧原字校新稿。

### 步骤 5 · 渲染交付文件

```bash
python scripts/record.py render "<ID>_meta.json" --out "<ID>_文案.txt"
```

固定八段：基本信息 → 标签 → 发布描述 → 口播文案 → 结构化要点 → 数据 → 其他 → 本次未采到/需核对。

**必须走 `record.py render`，不要手写这份 txt**——手写会漏字段、字段顺序会飘，
以后批量对比就看不出规律了。

### 步骤 6 · 回话

只说三件事：拿到了什么（形态、几个字段、多少字）、存哪了、哪些字段没采到或需核对。
不要播报技术细节，除非用户问。

---

## 四、平台矩阵

| 平台 | 引擎 | 前置条件 | 已知风险 |
|---|---|---|---|
| **视频号** | `shipinhao-batch-save` 的 `wxvideo.py` | **一次扫码**（凭证过期报 `YUANBAO_UNAUTH`） | 走网页端内部行为，接口会变；**不给发布时间** |
| B站 | `yt-dlp` | **本地有 ffmpeg**（`fetch.py` 会自动用 `imageio_ffmpeg` 自带的那个） | 分离流必须合并；缺 ffmpeg 时会被兜底报错盖住真因（见回归 R-14）。短链指向多 P 合集时 `id` 带 `_p1`，只取第 1 个分 P；发布时间只有日期、无时分秒；不返回收藏/转发 |
| 抖音 | **competitor-teardown 的 `douyin` 模块**（playwright） | `playwright install chromium-headless-shell` | **yt-dlp 不可用**：Douyin extractor 要求 fresh cookies，本机浏览器 cookie 库被锁（App-Bound 加密）→ 别浪费时间去调 |
| **小红书** | `yt-dlp`（`XiaoHongShuIE`）+ **短链预展开** | 无 | 短链 `xhslink.cn` 直接喂 yt-dlp 会被桌面 UA 风控跳 `/login`；展开后**无需登录态**。话题格式是 `#名[话题]#`，要剥掉 `[话题]` 后缀否则标签重复。平台**不返回**作者昵称 / 发布时间 / 任何互动数据，只给 `uploader_id` |
| 快手 | **competitor-teardown 的 `kuaishou` 模块**（playwright） | 同上 | **yt-dlp 没有快手 extractor** |
| 以上全部失败 | `competitor-teardown` 的 CLI | playwright | 慢（会白跑一次本地 ASR），只作最后兜底 |

**为什么不用 CT 的 CLI 取视频**：`extract.py --no-asr` 会在**下载媒体之前**就 return；
不带 `--no-asr` 又会白跑一遍它自己的本地 ASR。所以本技能直接调它的 `douyin` / `kuaishou`
模块，只借浏览器解析能力，拿到直链自己下载。

### 换机 / 新环境安装

**本仓库只分发自己的脚本和文档。**下面这些不随 git 走，换一台 WorkBuddy 机器要自己装。
`fetch.py` 缺哪个就明确报哪个，并把**缺什么 / 怎么装 / 可复制命令**打到 stderr；

| 缺什么 | 怎么装 | 什么时候会报 |
|---|---|---|
| Python 包 | `"<python>" -m pip install -r requirements.txt` | `yt-dlp 未安装`、跑 `frames.py` 缺 numpy |
| playwright 浏览器 | `"<python>" -m playwright install chromium-headless-shell` | 抖音 / 快手解析 |
| `shipinhao-batch-save` | WorkBuddy **技能市场**搜索安装，skillId `skill_2110703652279099392` | 视频号 |
| `competitor-teardown` | WorkBuddy **技能市场**搜索安装，skillId `skill_2105881606948155392` | 抖音 / 快手 / 兜底 |
| 视频号凭证 | `"<python>" "<…>/scripts/wxvideo.py" login` 扫码一次 | `YUANBAO_UNAUTH` / `COOKIE_*` |

- 两个 skill 是**独立发布的市场 skill，没有 `git clone` 地址**，所以提示里给的是
  市场搜索名 + skillId + 装完后的 `doctor` / `login` 验证命令，不编造安装命令。
- **脚本不自动安装、不静默重试**。报错里的 `"<python>"` 已替换成当前真实解释器路径，可直接复制。
- 三级回退会覆盖 `error`，但 `install_hints` 按引擎保留全部提示，首个真实原因不会丢。
- 可选的手工自检（没做 `doctor` 子命令）：
  `"<python>" -c "import yt_dlp, numpy, imageio_ffmpeg, playwright; print('deps ok')"`

依赖齐全时上面这些都不用做；装完不用改任何代码或配置。

---

## 五、三条铁律（都是踩过的坑）

1. **转写不会返回空，它会幻觉。**
   纯 BGM 音轨喂进去，拿到的是 `「嗯。」`。所以判定成功不能只看「有没有返回」，
   要看内容是否成句、是否与画面/描述吻合。极短结果一律当失败处理。

2. **缩略图会毁小字。**
   288px 宽的拼图里，「摘自唐 · 王维《终南别业》」被读成「摘自在《唐诗三百首》」。
   **判断画面用拼图，精读文字必须 `full` 取原分辨率帧。**

3. **不要用画面哈希去重来提速。**
   静态背景＋底部字幕这类视频，8×8 均值哈希会把 90% 的卡片判成同一张。
   要提速只能降 fps，或者复用已抽的帧。

4. **别让大模型「通读改错别字」。**
   ASR 错是近音替换，单看文字通顺；模型无锚点会猜、会润色、会把对的专名改顺。
   交给大模型的应该是**可疑位置清单**（`proofread.py scan` 的产物），不是整篇稿子；
   每条改动要有依据，且必须能一键回滚。

---

## 六、边界

- 只处理用户自己发布的、或已获授权的内容。不做批量搬运。
- 转写是**云端**（`/agenttool/v1/asr`），音频会上行；要求「音频不出本机」时改走本地 whisper，并明确告知精度会降。
- 链接会失效、登录态会过期、页面会改版。遇到失败**如实报错误码**，不要静默重试。
- **ASR 后端优先级：方向已认可，本次不实现。** 将来引入多后端时按
  ①平台自带字幕轨 → ②客户端内置工具 → ③本地 whisper → ④第三方 ASR API 降级，
  配 `VCE_ASR=auto|builtin|local|api` 加能力探测，并把**实际用到的后端**写进
  `transcript_source`（枚举化，别再手填自由文本）。当前仍只走内置 `AudioTranscribe`，
  不要提前改代码。

---

## 七、改完必须回归

改任何脚本或判定逻辑后，按 `references/regression-cases.md` 跑一遍。
里面 19 条用例都带**精确期望值**（如 `mad_median=0.248`、相似度 `0.955`/`0.241`、校对 `16 条 / 36 处`、`scan` 漏报 `0 可疑`），
不是「能跑通」，是「跑出来是不是这个数」。L0 离线段不到 1 分钟即可跑完。

> **回归基线 = S1～S7 的真实 mp4 + `<ID>_meta.json`**（本机 `Downloads/` 下，**不进仓库**）。
> 那些精确数字都是在这几个视频上跑出来的，换机拿不到样本就跑不了 L0，
> 别误判成脚本坏了。已定方向：将来要带基线就做**最小 fixture**，不放整段视频；
> 本次不落地。R-19 是唯一不依赖任何样本文件的用例（临时 home 即可跑）。
