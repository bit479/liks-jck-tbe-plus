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
    # TBe+ 桌面端可能装在下面任意一个位置，找到哪个用哪个
    "client_exe_candidates": [
        r"C:\Program Files (x86)\tbea\2305251534\TBe.exe",
        r"C:\Program Files (x86)\tbea\TBe.exe",
        r"C:\Program Files\tbea\TBe.exe",
        r"E:\Program Files (x86)\tbea\2305251534\TBe.exe",
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

    # ---------------- 节奏（秒） ----------------
    "page_timeout": 30.0,
    "wait_after_click": 4.0,
    "confirm_wait": 5.0,
    "poll_interval": 1.5,
    "min_delay_between_likes": 0.0,
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

    page_timeout: float = 30.0
    wait_after_click: float = 4.0
    confirm_wait: float = 5.0
    poll_interval: float = 1.5
    min_delay_between_likes: float = 0.0

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
