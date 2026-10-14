"""配置：所有可调项集中在这里，也可以用同目录的 config.json 覆盖。

config.json 是可选的，不存在就用下面 DEFAULTS 里的值。
它默认被 .gitignore 忽略，方便每个人放自己环境的东西。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = APP_DIR / "config.json"


DEFAULTS: Dict[str, Any] = {
    # ---------------- 客户端 ----------------
    # TBe+ 桌面端的位置。这里只是一些"常见位置"的提示，
    # 找不到时程序会自动查找（进程 / 快捷方式 / 注册表 / 常见目录），
    # 实在找不到会弹窗让你手动指定，并把你选的路径记到这里。
    "client_exe_candidates": [
        r"C:\Program Files (x86)\tbea\2305251534\TBe.exe",
        r"C:\Program Files (x86)\tbea\TBe.exe",
        r"C:\Program Files\tbea\TBe.exe",
    ],
    "debug_port": 9222,
    "restart_wait_seconds": 90,

    # ---------------- 板块 ----------------
    # 门户首页上的新闻卡片，按 DOM 顺序
    "portal_cards": ["新闻资讯", "开讲了"],
    # 会话型板块（不在门户卡片里，在左侧消息列表）
    "session_channel": "进出口公司新闻资讯",
    # false = 默认也处理会话型板块
    "skip_session_by_default": False,

    # ---------------- 页面标识 ----------------
    "portal_url_hint": "miniapp://portal",
    "list_host": "ejia.tbea.com",
    "article_host": "tbeanews.tbea.com",
    "article_path": "pc/show",
    "article_webview_hint": "tbeanews.tbea.com/pc/show",

    # ---------------- 点赞按钮 ----------------
    # 实测：未点赞 class="praise-num"，点赞后类名整体换成 "praise-active"
    "praise_selector": ".oper-area .praise-num",
    "praise_active_selector": ".oper-area .praise-active",
    "article_title_selector": ".article-item .article-title",
    # 门户卡片里的文章标题（用来做登录预热，点它打开的文章会带 SSO 票据）
    "portal_article_selector": ".article-title",

    # ---------------- 时间段筛选 ----------------
    # 只处理这个区间内的新闻；留空表示不限。
    # 格式 YYYY-MM-DD（也接受 2026/9/1 这类写法）
    "filter_since": "",
    "filter_until": "",

    # ---------------- 登录预热 ----------------
    # 文章页需要一个 token，而只有"从门户卡片点出来"的链接才带 SSO 票据。
    # 所以开始干活前先从门户点开一篇文章，让新闻模块完成一次登录；
    # 之后所有直连的文章地址就都正常了。
    "login_warmup": True,
    "login_warmup_timeout": 18.0,
    # 客户端刚启动时门户可能还没就绪，预热失败很正常，多试几次
    "warmup_attempts": 3,
    "warmup_retry_interval": 4.0,
    # 重启客户端后等门户页把新闻卡片渲染出来的最长时间
    "portal_ready_timeout": 60.0,

    # ---------------- 列表翻到底 ----------------
    # 频道列表页是无限滚动的（实测一个频道 70 篇，首屏只有 10 篇），
    # 所以要一直滚到底把所有文章都加载出来再处理。
    "list_scroll_rounds": 60,
    # 只在取不到 isEnd（页面结构变了）时才用到的兜底判断
    "list_scroll_stable": 10,
    "list_load_timeout": 180.0,
    # 按页拉列表时最多拉多少页（每页 10 篇，200 页够覆盖任何频道）
    "list_max_pages": 200,
    # 每次滚动后的等待（原来 2 秒，太保守）
    "list_scroll_interval": 1.2,

    # ---------------- 节奏（秒） ----------------
    "page_timeout": 30.0,
    "click_wait_timeout": 6.0,
    # 走页面内点击后，等文章窗口出现的超时（短一点，不行就退回鼠标点击）
    "js_click_wait": 3.0,
    "verify_interval": 0.3,
    "verify_timeout": 8.0,
    "poll_interval": 0.4,
    "min_delay_between_likes": 0.0,

    # ---------------- 清理累积的页面 ----------------
    # 客户端每打开一篇文章就新建一个 webview 且从不回收，跑多了会拖慢甚至超时。
    # 每处理 N 篇重启一次客户端把它清零；0 = 不重启。
    "restart_every_n_articles": 60,

    # ---------------- 接口批量模式 ----------------
    # 登录之后可以直接调新闻站的接口读写点赞状态，不用逐个打开文章页面。
    # 每批处理多少个（批越大往返越少，但单个请求体越大）。
    "api_mode": True,
    "api_batch_size": 20,
    "api_base": "https://tbeanews.tbea.com/api",
    # Chromium 对同一个域名只开 6 条并发连接，发太多会排队甚至整体挂死
    "api_concurrency": 6,
    # 单个请求的最长时间。实测新闻站慢起来要 8 秒以上，卡太死会误判成失败
    "api_request_timeout": 20.0,
    # 一批的 CDP 调用超时（比页面超时长一些）
    "api_batch_timeout": 180.0,
    # 点赞前是否先查一次文章状态。**默认开启**：
    #   开着 → 能准确区分"新点赞"和"原来就已经赞过"（用户要看的就是这个数）；
    #   关掉 → 每篇不管有没有赞过都直接发一个点赞请求，快，但只能报
    #          "已确认点赞 N 篇"，分不清其中多少是本来就已经赞过的。
    # 代价说明：两种模式每篇都是 1 个请求（一个查状态 / 一个点赞），
    # 所以"重复跑一次"的速度几乎一样；只有"第一次跑、确实有一堆要新点"时，
    # 开着查状态会多出一倍请求（查完再点）。
    "api_read_state": True,
    # 点赞后再查一次，确认服务端状态确实变成已赞（慢，默认关）
    "api_verify": False,
    # 没成功的文章再重试几轮（每轮之间会刷新接口页面）
    "api_like_rounds": 3,
    # 用新闻站自己的列表接口枚举（不再是门户那张卡片）。
    # 门户的 card/getArticleList 最多只给 200 篇，7 月的文章根本翻不到；
    # 新闻站的 /api/article/lists 没有这个限制（实测 news 有 3286 篇）。
    "site_api": True,
    # 一次要多少篇（实测 500 也能返回），以及最多翻多少页兜底
    "site_page_size": 200,
    "site_max_pages": 50,
    # 发请求的最高频率（每秒几个）。
    # 实测服务端大约每 60 秒只放行 120 次点赞请求：第 121 次开始整片失败
    # （返回的业务码不是 1），一直失败到下一个时间窗才恢复。
    # 翻页请求也占同一份额度，所以起步就留出余量：1.4/秒 ≈ 84 次/分钟。
    # 真撞上了程序会自动再降速。0 = 不限速
    "api_rate": 1.4,
    # 一轮没成功，等多久再重试（服务端限流是按时间窗算的，
    # 立刻重试只会撞在同一窗口里，这就是"重试仍失败"的真实原因）
    "api_backoff": 20.0,
}


@dataclass
class Config:
    client_exe_candidates: List[str] = field(default_factory=list)
    debug_port: int = 9222
    restart_wait_seconds: int = 90

    portal_cards: List[str] = field(default_factory=list)
    session_channel: str = ""
    skip_session_by_default: bool = False

    portal_url_hint: str = "miniapp://portal"
    list_host: str = ""
    article_host: str = ""
    article_path: str = "pc/show"
    article_webview_hint: str = ""

    praise_selector: str = ".oper-area .praise-num"
    praise_active_selector: str = ".oper-area .praise-active"
    article_title_selector: str = ".article-item .article-title"
    portal_article_selector: str = ".article-title"

    # 时间段筛选（YYYY-MM-DD，留空 = 不限）
    filter_since: str = ""
    filter_until: str = ""

    login_warmup: bool = True
    login_warmup_timeout: float = 18.0
    warmup_attempts: int = 3
    warmup_retry_interval: float = 4.0
    portal_ready_timeout: float = 60.0
    list_scroll_rounds: int = 60
    list_scroll_stable: int = 10
    list_load_timeout: float = 180.0
    list_max_pages: int = 200
    list_scroll_interval: float = 1.2

    page_timeout: float = 30.0
    click_wait_timeout: float = 6.0
    js_click_wait: float = 3.0
    verify_interval: float = 0.3
    verify_timeout: float = 8.0
    poll_interval: float = 0.4
    min_delay_between_likes: float = 0.0
    restart_every_n_articles: int = 60
    api_mode: bool = True
    api_batch_size: int = 20
    api_base: str = "https://tbeanews.tbea.com/api"
    api_concurrency: int = 6
    api_request_timeout: float = 20.0
    api_batch_timeout: float = 180.0
    api_read_state: bool = True
    api_verify: bool = False
    api_like_rounds: int = 3
    api_rate: float = 1.4
    api_backoff: float = 20.0
    site_api: bool = True
    site_page_size: int = 200
    site_max_pages: int = 50

    # ------------------------------------------------------------------
    @property
    def cdp_url(self) -> str:
        return f"http://127.0.0.1:{self.debug_port}"

    def find_client_exe(self) -> Optional[Path]:
        for candidate in self.client_exe_candidates:
            path = Path(candidate)
            if path.is_file():
                return path
        return None


def load_config(path: Optional[Path] = None) -> Config:
    """读取配置：默认值 + （可选的）config.json 覆盖。"""
    data = dict(DEFAULTS)
    file_path = path or CONFIG_FILE
    if file_path.is_file():
        try:
            override = json.loads(file_path.read_text(encoding="utf-8"))
            if isinstance(override, dict):
                data.update(override)
        except (OSError, json.JSONDecodeError):
            # 配置写坏了就用默认值，不要因为读配置失败就跑不起来
            pass

    known = {f for f in Config.__dataclass_fields__}
    return Config(**{k: v for k, v in data.items() if k in known})
