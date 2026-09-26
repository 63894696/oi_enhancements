"""能力门面层(F1):把端点白名单升级为「能力注册表」,对外统一 search/execute。

定位(见 prisirwork-foundation-integration-design §3):
- 浏览器/agent 不需知道背后是 wallet 还是 prisiragent 团队——一律 search 发现、execute 调用。
- 权限门槛不新造:能力标注 risk(L0-L3)+ auth,实际执行仍走 endpoints 白名单 + token,
  本层只做「能力抽象 + 发现 + 路由」,不绕过红线③。

能力三层抽象(借 openwork connector/skill/plugin):
- 本文件的「能力」= skill 粒度(可被 agent 发现/调用的最小单元)。
- 每个能力绑定一个 endpoint path(底层 connector 实现);打包成 plugin 是 F6 的事。

能力 entry:
  id          : 点分能力名("wallet.status" / "prisiragent.dispatch" …)
  title       : 一句话人话描述(agent 发现时展示)
  risk        : "L0" 只读免确认 / "L1" 内嵌卡 / "L2" 全回显 / "L3" 安全对话框(口令/A2H)
  auth        : 是否需 X-OI-Token(与端点一致;L3 另由授权门把守,见设计 §4)
  endpoint    : 实际执行的端点 path(必须在 endpoints 注册表内)
  method      : 该端点的 HTTP 方法("GET"/"POST")
  keywords    : 发现检索词(中文/英文/别名),search 据此匹配
  confirm     : 授权门提示语(risk>=L1 时给扩展/shell 渲染确认卡用;L0 为 "")
"""
from __future__ import annotations

from typing import Any

# 能力注册表:id → entry。execute 时按 endpoint 路由回 endpoints 白名单。
_REGISTRY: dict[str, dict[str, Any]] = {}


def register_capability(cid: str, *, title: str, endpoint: str, method: str = "GET",
                        risk: str = "L0", auth: bool = True,
                        keywords: tuple[str, ...] = (), confirm: str = "") -> None:
    """登记一个能力。endpoint 必须是 endpoints 注册表里的白名单端点(否则 execute 仍 404)。"""
    _REGISTRY[cid] = {
        "id": cid, "title": title, "endpoint": endpoint, "method": method.upper(),
        "risk": risk, "auth": auth, "keywords": tuple(keywords), "confirm": confirm,
    }


def get(cid: str) -> dict[str, Any] | None:
    return _REGISTRY.get(cid)


def list_capabilities() -> list[dict[str, Any]]:
    """全量能力目录(不含实现;供 search 与调试)。按 id 排序保证稳定输出。"""
    return [
        {
            "id": e["id"], "title": e["title"], "risk": e["risk"], "auth": e["auth"],
            "endpoint": e["endpoint"], "method": e["method"],
            "keywords": list(e["keywords"]), "confirm": e["confirm"],
        }
        for _, e in sorted(_REGISTRY.items())
    ]


def search(query: str) -> list[dict[str, Any]]:
    """能力发现:按 id/title/keywords 子串匹配(大小写不敏感)。空 query 返回全部。

    返回精简条目(发现阶段只需 id/title/risk/confirm,不暴露 endpoint 细节之外的信息)。
    """
    q = (query or "").strip().lower()
    out = []
    for e in sorted(_REGISTRY.values(), key=lambda x: x["id"]):
        if not q:
            hit = True
        else:
            hay = " ".join([e["id"], e["title"], *e["keywords"]]).lower()
            hit = q in hay
        if hit:
            out.append({
                "id": e["id"], "title": e["title"], "risk": e["risk"],
                "auth": e["auth"], "confirm": e["confirm"],
            })
    return out


# ---------------------------------------------------------------------------
# P2.5+16: web.search / web.fetch 能力注册(无 stub,L0 只读免确认)
# ---------------------------------------------------------------------------

register_capability(
    "web.search",
    title="web 搜索(多源 rank fusion,免 API key 优先,失败降级返空)",
    endpoint="/web/search", method="POST", risk="L0", auth=True,
    keywords=("搜索", "网页", "web", "search", "find", "找", "查", "internet"),
    confirm="",
)
register_capability(
    "web.fetch",
    title="web 抓取(多 fetcher 并发竞速 + 7d 本地缓存,失败降级返空)",
    endpoint="/web/fetch", method="POST", risk="L0", auth=True,
    keywords=("抓取", "下载", "网页内容", "fetch", "crawl", "get", "读", "url"),
    confirm="",
)

# ---------------------------------------------------------------------------
# P2.5+16d: web.research 多步研究能力(plan→search×N→fetch→LLM 合成,带 [n] 引用)
# ---------------------------------------------------------------------------

register_capability(
    "web.research",
    title="web 多步研究(plan→search→fetch→LLM 合成,带 [n] 编号引用)",
    endpoint="/web/research", method="POST", risk="L0", auth=True,
    keywords=("研究", "调研", "查", "综述", "research", "study", "investigate", "deep"),
    confirm="",
)

# ---------------------------------------------------------------------------
# P2.5+16e: web.extract JSON Schema 结构化抽取
# ---------------------------------------------------------------------------

register_capability(
    "web.extract",
    title="web 结构化抽取(JSON Schema,LLM 可选,失败降级 regex 启发式)",
    endpoint="/web/extract", method="POST", risk="L0", auth=True,
    keywords=("抽取", "提取", "解析", "extract", "parse", "structured", "schema"),
    confirm="",
)

# ---------------------------------------------------------------------------
# P2.5+16f: web.find_similar 相似 URL 发现(基于关键词 + 多源:web_search + 可选 Serper related)
# ---------------------------------------------------------------------------

register_capability(
    "web.find_similar",
    title="web 相似 URL 发现(基于关键词 + 多源:web_search + 可选 Serper related)",
    endpoint="/web/find_similar", method="POST", risk="L0", auth=True,
    keywords=("相似", "类似", "相关", "similar", "related", "find_similar"),
    confirm="",
)


# ---------------------------------------------------------------------------
# P2.5+18b: per-domain fetcher 优先级学习(查 / 调试 / 手动触发)
# ---------------------------------------------------------------------------

register_capability(
    "web.tune.recommend",
    title="查 host 的 learned fetcher 优先级(慢站加速用)",
    endpoint="/web/tune/recommend", method="POST", risk="L0", auth=True,
    keywords=("tune", "learn", "推荐", "优先级", "加速", "host", "slow"),
)
register_capability(
    "web.tune.stats",
    title="查 tune 学习状态(累加器 + tune.json 内容)",
    endpoint="/web/tune/stats", method="POST", risk="L0", auth=True,
    keywords=("tune", "stats", "学习状态", "累加器", "统计"),
)
register_capability(
    "web.tune.flush",
    title="手动触发 tune flush,把已稳定的 learned best 写盘",
    endpoint="/web/tune/flush", method="POST", risk="L1", auth=True,
    keywords=("tune", "flush", "落盘", "手动"),
    confirm="L1 tune flush:把累加器中已稳定(MIN_SAMPLES=3 + ok 率 ≥ 80%)的 fetcher 优先级写入 tune.json。下次同 host 直接按 learned 顺序调用。",
)


# ---------------------------------------------------------------------------
# 2026-09-24: 多平台发布门面(Easel 桥接)
# 失败/未就绪 → 降级返 ok=False + reason;不发时绝不抛栈。
# ---------------------------------------------------------------------------

register_capability(
    "publish.list",
    title="列出所有注册的平台发布器(微信公众号 / 小红书 / B站 / 抖音 / 知乎 / 视频号)+ ready 状态",
    endpoint="/publish/list", method="POST", risk="L0", auth=True,
    keywords=("发布", "平台", "publisher", "publish", "平台列表", "发到"),
)
register_capability(
    "publish.status",
    title="查某平台登录态 / 桥接就绪状态(默认 wechat-oa)",
    endpoint="/publish/status", method="POST", risk="L0", auth=True,
    keywords=("登录态", "扫码", "whoami", "login", "发布器状态"),
)
register_capability(
    "publish.html",
    title="把 HTML 草稿发到指定平台(微信公众号走 Easel mp 会话;其他平台 NullPublisher 占位)",
    endpoint="/publish/html", method="POST", risk="L2", auth=True,
    keywords=("发布", "发文", "推文", "公众号", "wechat", "publish",
              "发到", "草稿", "发草稿", "发到公众号", "微信文章"),
    confirm="L2 真发草稿到公众号后台(Easel 会话模式):需要 mp 已扫码登录 + 配好封面 + 标题已审。",
)
register_capability(
    "publish.stats",
    title="公众号近 N 天数据回收(发表记录 / 阅读 / 分享 / 粉丝)— Easel stats 子命令",
    endpoint="/publish/stats", method="POST", risk="L0", auth=True,
    keywords=("公众号数据", "数据回收", "阅读", "分享", "粉丝", "stats",
              "分析", "公众号统计"),
)


# ---------------------------------------------------------------------------
# P3j T14: 视频 + YouTube 自然语言能力(代理 companion wechat-publisher 服务)
# 全部 L2 / L3 — 涉及外发/扣费;agent 调前需弹确认卡。
# 端点实现见 endpoints.py:_video_* / _youtube_* / _analytics_*(透明代理)
# ---------------------------------------------------------------------------

register_capability(
    "video.list",
    title="查看视频创作模块已注册的 9 种能力(TTS/ASR/合成/AI 配图/视频生成/编排/视频处理/字幕/数据分析)",
    endpoint="/video/list", method="POST", risk="L0", auth=True,
    keywords=("视频能力", "creator", "video creator", "video list",
              "能做什么视频"),
)

register_capability(
    "video.create",
    title="一句话做视频:主题 + 文案 → 配音 + 字幕 + 终片(走 Easel auto-short-video 编排,可指定画幅 9:16/16:9/1:1)",
    endpoint="/video/orchestrate", method="POST", risk="L2", auth=True,
    keywords=("做视频", "做个视频", "出片", "拍视频", "生成视频", "编排视频",
              "一键视频", "short video", "短视频", "make video", "create video",
              "auto short", "主题文案", "口播视频"),
    confirm=("L2 一键出片:会用 AI 配音 + 字幕 + ffmpeg 合成。"
             "默认竖屏 9:16 60 秒。扣费项已在 Web UI 弹过确认卡,这里再确认一次:"
             "画面是默认占位图(不调 AI 配图,除非显式 with_images=true)"),
)

register_capability(
    "video.tts",
    title="把文字转成语音(中文男/女声,可选自动出字幕 SRT)— edge-tts 走外网代理",
    endpoint="/video/tts", method="POST", risk="L1", auth=True,
    keywords=("配音", "朗读", "语音合成", "文字转语音", "tts",
              "text to speech", "念稿", "念出来", "念"),
    confirm="L1 TTS 文字转语音:首次需联网到 edge-tts(走系统代理)。产出 mp3。",
)

register_capability(
    "video.asr",
    title="给视频/音频自动生成字幕文件(SRT/VTT/ASS)— faster-whisper 本地推理",
    endpoint="/video/asr", method="POST", risk="L1", auth=True,
    keywords=("字幕", "识别", "听写", "转写", "asr",
              "speech to text", "字幕识别", "加字幕", "出字幕"),
    confirm="L1 ASR 自动字幕:首次会下 Whisper 模型(base 约 150MB,选 large 约 3GB)。",
)

register_capability(
    "video.cut",
    title="裁剪视频片段(按起止时间,支持 HH:MM:SS 或秒数)— ffmpeg 精确裁剪",
    endpoint="/video/cut", method="POST", risk="L1", auth=True,
    keywords=("裁剪", "切片段", "剪视频", "截取", "cut", "trim"),
)

register_capability(
    "video.bgm",
    title="给视频加背景音乐(混音,自动调节音量,防盖住人声)",
    endpoint="/video/bgm", method="POST", risk="L1", auth=True,
    keywords=("背景音乐", "配乐", "加音乐", "bgm", "music",
              "加歌", "添加音乐"),
)

register_capability(
    "video.burn",
    title="把字幕文件烧录进视频(硬字幕或软字幕)— ffmpeg + libass",
    endpoint="/video/burn", method="POST", risk="L1", auth=True,
    keywords=("烧字幕", "嵌入字幕", "硬字幕", "软字幕", "字幕合成",
              "burn subtitle", "烧录"),
)

register_capability(
    "video.info",
    title="查视频元数据(时长 / 分辨率 / 帧率 / 码率)— ffprobe",
    endpoint="/video/info", method="POST", risk="L0", auth=True,
    keywords=("视频信息", "视频元数据", "时长", "分辨率", "码率",
              "video info", "ffprobe", "查视频"),
)

register_capability(
    "video.analyze",
    title="分析发布数据(最佳时段 / 标签效果 / 内容类型 / 增长归因)— 确定性计算,读本地缓存",
    endpoint="/video/analyze", method="POST", risk="L0", auth=True,
    keywords=("数据分析", "发布分析", "最佳时段", "标签效果", "增长归因",
              "publish analytics", "什么时间发", "标签分析"),
)

register_capability(
    "youtube.upload",
    title="把视频上传到 YouTube(Data API v3,可选 public/unlisted/private)— 需先授权",
    endpoint="/youtube/upload", method="POST", risk="L3", auth=True,
    keywords=("上传 YouTube", "发 YouTube", "YouTube 投稿", "海外发布",
              "youtube", "youtube upload", "推到 YouTube", "youtube video"),
    confirm=("L3 YouTube 真上传:首次需浏览器授权(OAuth,token 落 ~/.prisIrai/youtube_token.json)。"
             "public 后任何人能搜到,不可撤回 — Web UI 弹过卡,这里再确认一次。"
             "默认 exec_real=False 仅校验参数;勾上才真传。"),
)

register_capability(
    "youtube.list",
    title="列出我自己 YouTube 频道的视频(标题 / 隐私状态 / 链接)— 需 OAuth 已授权",
    endpoint="/youtube/list", method="POST", risk="L0", auth=True,
    keywords=("我 YouTube 视频", "我的 YouTube", "我的频道",
              "list my youtube", "youtube videos"),
)

register_capability(
    "youtube.status",
    title="查 YouTube 桥接层状态(是否已装依赖 / 有无 OAuth token / 有无 client_secrets)",
    endpoint="/youtube/status", method="POST", risk="L0", auth=True,
    keywords=("YouTube 状态", "youtube ready", "youtube 配置",
              "youtube 装没装", "youtube 授权没"),
)


# ---------------------------------------------------------------------------
# P3j T20-B: Agent-Reach 信息源(14 平台:小红书 / B站字幕 / GitHub / V2EX / RSS …)
# 全部 L0 只读,失败/未安装 fail-soft 返 ok=False + hint,绝不抛栈。
# 端点实现见 endpoints.py:_web_reach_*(子进程桥调 agent-reach CLI)
# ---------------------------------------------------------------------------

register_capability(
    "web.reach.doctor",
    title="查 agent-reach 安装 + 14 平台健康(L0 探活,无需 key)",
    endpoint="/web/reach/doctor", method="POST", risk="L0", auth=True,
    keywords=("agent-reach", "reach", "平台健康", "reach doctor",
              "信息源健康", "14 平台", "reach 安装"),
)
register_capability(
    "web.reach.read",
    title="读某平台 URL(B站字幕 / 小红书 / GitHub / V2EX / RSS)— 调 agent-reach 子进程",
    endpoint="/web/reach/read", method="POST", risk="L0", auth=True,
    keywords=("读小红书", "读 B站", "看视频字幕", "看帖子", "看 GitHub",
              "看 V2EX", "看 RSS", "fetch post", "read post",
              "bilibili 字幕", "xhs", "v2ex post", "读 reach"),
)
register_capability(
    "web.reach.search",
    title="搜某平台关键词(小红书 / B站 / V2EX / RSS)— 调 agent-reach 子进程",
    endpoint="/web/reach/search", method="POST", risk="L0", auth=True,
    keywords=("搜小红书", "搜 B站", "搜 V2EX", "搜 RSS",
              "xhs search", "v2ex search", "bilibili search",
              "reach 搜", "reach search"),
)
register_capability(
    "web.reach.platforms",
    title="列 14 平台目录 + P0 默认开标记",
    endpoint="/web/reach/platforms", method="POST", risk="L0", auth=True,
    keywords=("reach 平台", "agent-reach platforms", "信息源列表",
              "14 平台列表"),
)


# ---------------------------------------------------------------------------
# P3j T20-I: jina-ai/reader 复现 — URL → LLM-friendly markdown + 全文搜索
# 端点实现见 endpoints.py:_web_jina_* + 底层 web_fetch_jina.py
# 无 API key 也可用 hosted(r.jina.ai 返 cached snapshot),
# 自部署 Docker = 零外部依赖(JINA_READER_URL=http://localhost:8081)
# ---------------------------------------------------------------------------

register_capability(
    "web.jina.health",
    title="查 jina reader/search 部署状态(hosted / 自部署 / api_key 是否有)",
    endpoint="/web/jina/health", method="POST", risk="L0", auth=True,
    keywords=("jina 状态", "jina health", "jina reader 健康",
              "jina search 健康", "r.jina.ai 健康"),
)
register_capability(
    "web.jina.fetch",
    title="URL → markdown(jina reader;显式调,不依赖 web_fetch 路由)",
    endpoint="/web/jina/fetch", method="POST", risk="L0", auth=True,
    keywords=("jina 抓", "jina fetch", "jina reader",
              "url 转 markdown", "url 转 md",
              "r.jina.ai", "jina 读"),
)
register_capability(
    "web.jina.search",
    title="关键词 → top N URL + 全文 markdown(jina search;全文搜索 provider)",
    endpoint="/web/jina/search", method="POST", risk="L0", auth=True,
    keywords=("jina 搜", "jina search", "jina 全文搜索",
              "s.jina.ai", "jina 搜索"),
)
