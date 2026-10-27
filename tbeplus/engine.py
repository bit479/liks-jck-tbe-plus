"""点赞引擎：遍历板块 → 找出未点赞 → 批量点击。

实测出来的真实结构（2026-10 在 TBe+ 桌面端验证）
------------------------------------------------
* 板块入口：门户页卡片 -> "更多" -> 频道文章列表页
      https://<list_host>/pubacc-front/article/list?contentSource=[...]
* 列表页本身没有点赞按钮；点文章标题会开文章窗口
      https://<article_host>/pc/show?id=...&f=...&yzj_id=...
* 点赞按钮在文章详情页：
      未点赞  <span class="praise-num">10476</span>
      已点赞  <span class="praise-active">10477</span>
* 会话型板块（消息列表里的订阅号）里的文章卡片点不开，
  但消息组件的数据里直接带着每篇文章的 URL，所以改成读 URL 再导航。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from .cdp import CDPSession, CDPError, list_targets
from .config import Config

LogFn = Callable[[str], None]

# 这些状态说明"这一篇没处理成"，值得重试一次
_RETRYABLE = {
    "no-button",
    "no-window",
    "click-failed",
    "no-list",
    "no-change",
    "no-webview",
}

# 接口模式里这几种原始状态都算"这一篇没落下"
_API_OK = {
    "liked",              # 查过状态、确认是新点上的
    "like-unconfirmed",   # 点赞已受理，复查没看到状态变化
    "digg-sent",          # 没查状态（或读不到），点赞请求已被服务端接受
    "digg-unconfirmed",
    "already-liked",
    "dry-run",
}


# ---------------------------------------------------------------------------
# 日期解析
# ---------------------------------------------------------------------------
# 缩略图地址里带着发布日期：.../public/data/26/09/30/xxxx.jpg  ->  2026-09-30
_IMAGE_DATE_RE = re.compile(r"/data/(\d{2})/(\d{1,2})/(\d{1,2})/")
# 列表里的日期文案：可能是 2026/09/30，也可能只有 09/30
_FULL_DATE_RE = re.compile(r"(\d{4})[/\-年](\d{1,2})[/\-月](\d{1,2})")
_SHORT_DATE_RE = re.compile(r"(\d{1,2})[/\-](\d{1,2})")


def _safe_date(year: int, month: int, day: int) -> Optional[date]:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_article_date(image: str = "", date_text: str = "") -> Optional[date]:
    """从缩略图地址或列表里的日期文案推断文章发布日期。"""
    match = _IMAGE_DATE_RE.search(image or "")
    if match:
        year, month, day = (int(x) for x in match.groups())
        parsed = _safe_date(2000 + year, month, day)
        if parsed is not None:
            return parsed

    text = (date_text or "").strip()
    match = _FULL_DATE_RE.search(text)
    if match:
        parsed = _safe_date(*(int(x) for x in match.groups()))
        if parsed is not None:
            return parsed

    match = _SHORT_DATE_RE.search(text)
    if match:
        month, day = (int(x) for x in match.groups())
        today = date.today()
        parsed = _safe_date(today.year, month, day)
        if parsed is None:
            return None
        # 只有月日时，如果比今天晚了一个月以上，多半是去年的
        if (parsed - today).days > 30:
            parsed = _safe_date(today.year - 1, month, day) or parsed
        return parsed
    return None


def parse_user_date(text: str) -> Optional[date]:
    """解析用户输入的时间：支持 2026-09-01 / 2026/9/1 / 20260901 / 2026.9.1。"""
    text = (text or "").strip()
    if not text:
        return None
    match = re.match(r"^(\d{4})[-/.年]?(\d{1,2})[-/.月]?(\d{1,2})日?$", text)
    if not match:
        return None
    return _safe_date(*(int(x) for x in match.groups()))


@dataclass
class SectionResult:
    name: str
    total: int = 0
    stats: Dict[str, int] = field(default_factory=dict)
    error: str = ""
    # 接口模式没拿下、改交页面方式处理的篇数。
    # 只用于日志说明，不进 stats——否则同一批文章会被统计两次
    # （先记一次接口失败，再记一次页面成功），汇总看着像"失败了一堆"，
    # 其实一篇都没落下。
    api_failed: int = 0

    @property
    def liked(self) -> int:
        return sum(v for k, v in self.stats.items() if k.startswith("liked"))

    @property
    def already(self) -> int:
        return self.stats.get("already-liked", 0)


class LikeEngine:
    def __init__(
        self,
        config: Config,
        dry_run: bool = False,
        log: Optional[LogFn] = None,
        stop_flag: Optional[Callable[[], bool]] = None,
    ):
        self.cfg = config
        self.dry_run = dry_run
        self._log = log or (lambda _: None)
        self._stop = stop_flag or (lambda: False)
        # 新闻站栏目名 → section_code 的缓存（一次运行里查一次就够）
        self._codes: Optional[Dict[str, str]] = None

    # ------------------------------------------------------------------
    # 小工具
    # ------------------------------------------------------------------
    def log(self, message: str) -> None:
        self._log(message)

    def targets(self) -> List[Dict[str, Any]]:
        try:
            return list_targets(self.cfg.debug_port)
        except Exception:
            return []

    def find_target(self, hint: str) -> Optional[Dict[str, Any]]:
        return next((t for t in self.targets() if hint in (t.get("url") or "")), None)

    def find_webview(self, hint: str) -> Optional[Dict[str, Any]]:
        return next(
            (
                t
                for t in self.targets()
                if t.get("type") == "webview" and hint in (t.get("url") or "")
            ),
            None,
        )

    async def _eval(
        self, target: Dict[str, Any], js: str, timeout: Optional[float] = None
    ) -> Any:
        async with CDPSession(
            target["webSocketDebuggerUrl"], timeout=timeout or self.cfg.page_timeout
        ) as s:
            await s.send("Runtime.enable")
            return await s.evaluate(js)

    async def _click(self, target: Dict[str, Any], x: float, y: float) -> None:
        async with CDPSession(target["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout) as s:
            await s.send("Runtime.enable")
            await s.click_point(x, y)

    # ------------------------------------------------------------------
    # 按文案点击（会先滚动到可视区，等布局稳定再重新量坐标）
    # ------------------------------------------------------------------
    async def click_text(self, target: Dict[str, Any], text: str) -> bool:
        """按文案点击（真实鼠标事件，慢但通用——门户那种场景只能用它）。"""
        return await self._click_text_mouse(target, text)

    async def click_text_js(self, target: Dict[str, Any], text: str) -> bool:
        """按文案点击（页面内派发事件）。

        快得多：实测列表页 0.02 秒就能把文章窗口点出来，
        而真实鼠标事件要等 5 秒——因为点完上一篇之后列表页变成了后台页，
        得等操作系统把它重新切到前台、布局刷新完才能量到正确坐标。
        列表页的点击处理器认这种合成事件，所以这里走快路径。
        """
        js = (
            r"""((text) => {
              let best = null, bd = -1;
              document.querySelectorAll('*').forEach((el) => {
                const t = (el.innerText || '').trim();
                if (!t || t.length > 60 || !t.includes(text)) return;
                let d = 0, p = el;
                while (p) { d += 1; p = p.parentElement; }
                if (d > bd) { bd = d; best = el; }
              });
              if (!best) return false;
              const node = best.closest('a, li, div') || best;
              const opt = { bubbles: true, cancelable: true, view: window };
              node.dispatchEvent(new MouseEvent('mousedown', opt));
              node.dispatchEvent(new MouseEvent('mouseup', opt));
              node.click();
              return true;
            })("""
            + json.dumps(text, ensure_ascii=False)
            + ")"
        )
        try:
            async with CDPSession(
                target["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout
            ) as s:
                await s.send("Runtime.enable")
                return bool(await s.evaluate(js))
        except CDPError:
            return False

    async def _click_text_mouse(self, target: Dict[str, Any], text: str) -> bool:
        """按文案点击目标元素。

        踩过的坑：打开一篇文章后，列表页会变成后台标签，Chromium 会对它限流，
        这时 scrollIntoView 之后量到的坐标是旧的，点击就会落空。
        所以每次点击前先 Page.bringToFront 把它拉回前台，并且失败会重试几轮。
        """
        mark_js = (
            r"""((text) => {
              const hits = [];
              document.querySelectorAll('*').forEach((el) => {
                const t = (el.innerText || '').trim();
                if (!t || t.length > 60 || !t.includes(text)) return;
                let d = 0, p = el;
                while (p) { d += 1; p = p.parentElement; }
                hits.push([d, el]);
              });
              if (!hits.length) return false;
              hits.sort((a, b) => b[0] - a[0]);
              const el = hits[0][1];
              document.querySelectorAll('[data-tbe-click]').forEach((n) =>
                n.removeAttribute('data-tbe-click'));
              el.setAttribute('data-tbe-click', '1');
              el.scrollIntoView({ block: 'center' });
              return true;
            })("""
            + json.dumps(text, ensure_ascii=False)
            + ")"
        )
        rect_js = (
            r"""(() => {
              const el = document.querySelector('[data-tbe-click]');
              if (!el) return { found: false };
              el.scrollIntoView({ block: 'center' });
              const r = el.getBoundingClientRect();
              return { found: true, x: r.left + r.width / 2, y: r.top + r.height / 2,
                       vw: window.innerWidth, vh: window.innerHeight,
                       visible: document.visibilityState === 'visible' };
            })()"""
        )
        loop = asyncio.get_event_loop()
        try:
            async with CDPSession(
                target["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout
            ) as s:
                await s.send("Runtime.enable")
                try:
                    # 把它拉回前台，否则后台标签会被限流、scrollIntoView 之后
                    # 量到的还是旧坐标，点击就落空了
                    await s.send("Page.bringToFront")
                except CDPError:
                    pass
                if not await s.evaluate(mark_js):
                    return False

                # 拉回前台是异步生效的，这里等"页面可见 + 坐标落在视口内"，
                # 而不是盲等固定时间再重试
                deadline = loop.time() + self.cfg.click_wait_timeout
                while loop.time() < deadline and not self._stop():
                    info = await s.evaluate(rect_js)
                    if (
                        info
                        and info.get("found")
                        and info.get("visible")
                        and 0 <= info["x"] <= info["vw"]
                        and 0 <= info["y"] <= info["vh"]
                    ):
                        await s.click_point(info["x"], info["y"])
                        return True
                    await asyncio.sleep(0.2)
        except CDPError:
            return False
        return False

    # ------------------------------------------------------------------
    # 点赞状态
    # ------------------------------------------------------------------
    def _read_praise_js(self) -> str:
        return (
            "(() => {\n"
            f"  const active = document.querySelector({json.dumps(self.cfg.praise_active_selector)});\n"
            f"  const el = active || document.querySelector({json.dumps(self.cfg.praise_selector)});\n"
            "  const body = document.body ? document.body.innerText.slice(0, 80) : '';\n"
            "  if (!el) return { found: false, body: body };\n"
            "  el.scrollIntoView({ block: 'center' });\n"
            "  const r = el.getBoundingClientRect();\n"
            "  return { found: true, liked: !!active, count: el.innerText.trim(),\n"
            "           x: r.left + r.width / 2, y: r.top + r.height / 2 };\n"
            "})()"
        )

    @staticmethod
    def _looks_logged_out(state: Optional[Dict[str, Any]]) -> bool:
        """客户端没登录 / 会话过期时，文章页会显示"登录超时"之类的提示。"""
        if not state:
            return False
        body = state.get("body") or ""
        return ("登录超时" in body) or ("重新登录" in body) or ("请先登录" in body)

    async def _close_tab(self, marker: str) -> bool:
        """内容页是开在一个窗口里的标签页，关标签而不是关窗口。

        只在"这个标签是文章页"时才关，绝不误伤频道列表页。
        有的标签 title 属性是空的（页面还没加载完就关），
        所以匹配不到具体文章时，退回关最后一个 **文章** 标签。
        """
        host = next(
            (t for t in self.targets() if "browser.html" in (t.get("url") or "")), None
        )
        if host is None:
            return False
        js = (
            r"""((marker) => {
              const bar = document.querySelector('.tab-bar');
              if (!bar) return false;
              const tabs = Array.from(bar.querySelectorAll('.tab-header'));
              const titleOf = (tab) => {
                const span = tab.querySelector('span.title');
                return span ? (span.getAttribute('title') || '') : '';
              };
              let target = tabs.find((tab) => titleOf(tab).includes(marker));
              if (!target) {
                // 只考虑文章页，避免把频道列表页关掉
                const articles = tabs.filter((tab) => titleOf(tab).includes('pc/show'));
                target = articles.length ? articles[articles.length - 1] : null;
              }
              const closer = target && target.querySelector('em.tab-close');
              if (!closer) return false;
              closer.click();
              return true;
            })("""
            + json.dumps(marker, ensure_ascii=False)
            + ")"
        )
        try:
            async with CDPSession(host["webSocketDebuggerUrl"], timeout=20) as s:
                await s.send("Runtime.enable")
                return bool(await s.evaluate(js))
        except CDPError:
            return False

    async def close_article_tabs(self) -> int:
        """把文章窗口里遗留的文章标签页都关掉，避免干扰后续判断。

        上一次运行如果被打断，会留下一些已经失效的文章页；它们和新的文章页
        URL 相同，按 URL 查找时可能命中旧的，从而误判成"登录超时"。
        这里只关 URL 里带 pc/show 的标签，不动用户自己开的东西。
        """
        host = next(
            (t for t in self.targets() if "browser.html" in (t.get("url") or "")), None
        )
        if host is None:
            return 0
        js = (
            "(() => { const bar = document.querySelector('.tab-bar');"
            " if (!bar) return 0;"
            " const tabs = Array.from(bar.querySelectorAll('.tab-header'));"
            " let n = 0;"
            " for (const tab of tabs) {"
            "   const span = tab.querySelector('span.title');"
            "   const t = span ? (span.getAttribute('title') || '') : '';"
            "   if (!t.includes('pc/show')) continue;"
            "   const c = tab.querySelector('em.tab-close');"
            "   if (c) { try { c.click(); n++; } catch (e) {} }"
            " }"
            " return n; })()"
        )
        try:
            async with CDPSession(host["webSocketDebuggerUrl"], timeout=20) as s:
                await s.send("Runtime.enable")
                return int(await s.evaluate(js) or 0)
        except (CDPError, TypeError):
            return 0

    async def _wait_for_new_target(
        self, existing_ids: set, pattern: str, timeout: float
    ) -> Optional[Dict[str, Any]]:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._stop():
                return None
            for t in self.targets():
                if t.get("id") not in existing_ids and pattern in (t.get("url") or ""):
                    return t
            await asyncio.sleep(1.0)
        return None

    # ------------------------------------------------------------------
    # 门户卡片型板块
    # ------------------------------------------------------------------
    async def warm_up_login(self) -> bool:
        """登录预热，失败会重试几次。

        客户端刚启动时门户往往还没就绪，第一次很容易失败，
        所以这里多试几次，而不是一次失败就带着未登录状态硬跑。
        """
        if not self.cfg.login_warmup:
            return True

        # 客户端刚重启时门户页要一会儿才把内容加载出来，
        # 不等它就直接预热会连试几次都扑空（实测重启后就是这样卡住的）
        if not await self._wait_portal_ready(self.cfg.portal_ready_timeout):
            self.log("  门户页一直没准备好，跳过登录预热")
            return False

        attempts = max(1, self.cfg.warmup_attempts)
        for attempt in range(1, attempts + 1):
            if await self._warm_up_login_once():
                return True
            if attempt < attempts:
                self.log(
                    f"  登录预热第 {attempt} 次没成功，"
                    f"{self.cfg.warmup_retry_interval:.0f} 秒后重试 …"
                )
                await asyncio.sleep(self.cfg.warmup_retry_interval)
        return False

    async def _wait_portal_ready(self, timeout: float = 60.0) -> bool:
        """等门户页把新闻卡片渲染出来（重启客户端之后尤其需要）。"""
        js = (
            "(() => { const el = document.querySelector("
            + json.dumps(self.cfg.portal_article_selector)
            + "); return !!(el && (el.innerText || '').trim()); })()"
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        waited = 0
        while loop.time() < deadline and not self._stop():
            portal = self.find_webview(self.cfg.portal_url_hint)
            if portal is not None:
                try:
                    if await self._eval(portal, js):
                        return True
                except CDPError:
                    pass
            if waited and waited % 10 == 0:
                self.log(f"  等门户页加载…{waited}s")
            await asyncio.sleep(2)
            waited += 2
        return False

    async def _warm_up_login_once(self) -> bool:
        """从门户卡片点开一篇文章，让新闻模块完成一次 SSO 登录。

        这是唯一可靠的登录入口：实测只有 **门户卡片** 生成的链接会带 SSO
        票据（`ticket=APPURLWITHTICKET...`），频道列表页点出来的链接不带。
        文章页拿到票据后就会换来登录态，之后所有直连的文章地址（包括
        消息里那些不带票据的 URL）都能正常加载。

        不做这一步，文章页会一直显示"文章获取错误：登录超时，请重新登录"。
        """
        if not self.cfg.login_warmup:
            return True
        portal = self.find_webview(self.cfg.portal_url_hint)
        if portal is None:
            self.log("  门户页不在，跳过登录预热")
            return False

        js = (
            "(() => { const el = document.querySelector("
            + json.dumps(self.cfg.portal_article_selector)
            + "); if (!el) return { found: false };"
            " el.scrollIntoView({ block: 'center' });"
            " const r = el.getBoundingClientRect();"
            " return { found: true, title: el.innerText.trim().slice(0, 30),"
            " x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()"
        )
        before = {t.get("id") for t in self.targets()}
        try:
            async with CDPSession(
                portal["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout
            ) as s:
                await s.send("Runtime.enable")
                info = await s.evaluate(js)
                if not info or not info.get("found"):
                    self.log("  门户上没找到可点的文章，跳过登录预热")
                    return False
                self.log(f"  点开门户文章做登录预热：{info.get('title', '')}")
                await s.click_point(info["x"], info["y"])
        except CDPError as exc:
            self.log(f"  登录预热点击失败：{exc}")
            return False

        article = await self._wait_for_new_target(
            before, self.cfg.article_webview_hint, timeout=self.cfg.login_warmup_timeout
        )
        if article is None:
            self.log("  登录预热：没能打开文章窗口")
            return False

        url = article.get("url") or ""
        if "id=" not in url:
            return False
        article_id = url.split("id=", 1)[1].split("&", 1)[0]
        marker = f"{self.cfg.article_path}?id={article_id}"
        try:
            _, state = await self._wait_praise(
                marker, self.cfg.login_warmup_timeout, target_id=article.get("id")
            )
        finally:
            await self._close_tab("id=" + article_id)

        if state and state.get("found"):
            self.log("  登录预热成功，新闻模块已登录")
            return True
        if self._looks_logged_out(state):
            self.log("  登录预热失败：文章页仍提示需要重新登录")
            return False
        self.log("  登录预热没读到点赞区域，继续按原流程尝试")
        return False

    async def open_channel(self, card_name: str, timeout: float = 20.0) -> Optional[Dict[str, Any]]:
        portal = self.find_webview(self.cfg.portal_url_hint)
        if portal is None:
            return None
        before = {t.get("id") for t in self.targets()}
        js = (
            r"""((name) => {
              const titles = Array.from(document.querySelectorAll('.card-title'));
              const hit = titles.find((t) => t.innerText.trim() === name);
              if (!hit) return { found: false, reason: 'no-card' };
              let card = hit;
              for (let i = 0; i < 6 && card.parentElement; i += 1) card = card.parentElement;
              const btn = card.querySelector('.card-header-button');
              if (!btn) return { found: false, reason: 'no-more-button' };
              btn.scrollIntoView({ block: 'center' });
              const r = btn.getBoundingClientRect();
              return { found: true, x: r.left + r.width / 2, y: r.top + r.height / 2 };
            })("""
            + json.dumps(card_name, ensure_ascii=False)
            + ")"
        )
        try:
            async with CDPSession(
                portal["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout
            ) as s:
                await s.send("Runtime.enable")
                info = await s.evaluate(js)
                if not info or not info.get("found"):
                    return None
                await s.click_point(info["x"], info["y"])
        except CDPError:
            return None
        return await self._wait_for_new_target(before, self.cfg.list_host, timeout)

    # ------------------------------------------------------------------
    # 新闻站自己的列表接口（推荐路径）
    #
    # 门户那张卡片的接口 card/getArticleList **最多只给 200 篇**：
    # 放大 pageSize（100、200 都试过）、翻到第 21 页以上，全都试了，
    # 它就是不再给了 —— 所以 7 月的文章在门户里翻不到，只能拿到最近的 200 篇。
    #
    # 新闻站自己的接口没有这个限制：
    #     GET /api/article/lists?section_code=news&page_no=1&page_size=100
    # 实测 section_code=news 的 count 是 **3286**（门户只给 200），
    # page_size 给 500 也照收。section_code 从 /api/pc/portal 的栏目名反查：
    # 新闻资讯=news、开讲了=lecture、要闻=headline。
    # ------------------------------------------------------------------
    async def _ensure_site_runner(self) -> Optional[Dict[str, Any]]:
        """找一个 tbeanews 域下的页面（同域才能发这些请求）。"""
        runner = self.find_webview(self.cfg.article_host)
        if runner is not None and await self._looks_alive(runner):
            return runner

        view = self.find_webview(self.cfg.list_host) or self.find_webview(
            self.cfg.article_host
        )
        if view is None:
            return None
        try:
            async with CDPSession(view["webSocketDebuggerUrl"], timeout=25) as s:
                await s.navigate(f"https://{self.cfg.article_host}/pc/list")
        except CDPError:
            pass
        for _ in range(30):
            await asyncio.sleep(1.0)
            runner = self.find_webview(self.cfg.article_host)
            if runner is None:
                continue
            try:
                if await self._eval(runner, "1 + 1", timeout=10) == 2:
                    return runner
            except CDPError:
                continue
        return None

    async def _section_codes(self, runner: Dict[str, Any]) -> Dict[str, str]:
        """栏目名 → section_code，例如 "新闻资讯" → "news"。"""
        if self._codes is not None:
            return self._codes
        js = (
            "fetch('/api/pc/portal',{credentials:'include'})"
            ".then((r)=>r.json()).then((b)=>{const o={};"
            "const d=(b&&b.data)||{};"
            "for(const k of Object.keys(d)){const s=d[k]||{};"
            "if(s.name)o[String(s.name).trim()]=k;}return o;})"
        )
        try:
            got = await self._eval(runner, js, timeout=30)
        except CDPError:
            got = None
        self._codes = got if isinstance(got, dict) else {}
        return self._codes

    def _site_list_js(
        self, code: str, stop_before: Optional[date], page_size: int, max_pages: int
    ) -> str:
        payload = json.dumps(
            {
                "base": f"https://{self.cfg.article_host}/api",
                "code": code,
                "stopBefore": stop_before.isoformat() if stop_before else "",
                "pageSize": max(1, page_size),
                "maxPages": max(1, max_pages),
            },
            ensure_ascii=False,
        )
        return (
            r"""((async (cfg) => {
  const out = { ok: true, items: [], pages: [], total: null };
  for (let page = 1; page <= cfg.maxPages; page++) {
    // code 为空 = 全部资讯（钉钉版左侧"新闻资讯"栏就是它）
    const url = cfg.base + '/article/lists'
      + (cfg.code ? '?section_code=' + encodeURIComponent(cfg.code) + '&' : '?')
      + 'page_no=' + page + '&page_size=' + cfg.pageSize;
    let body = null;
    try {
      const r = await fetch(url, { credentials: 'include' });
      body = await r.json();
    } catch (e) {
      out.ok = false; out.reason = 'fetch: ' + String(e); break;
    }
    if (!body || (body.code !== 1 && body.code !== 0)) {
      out.ok = false;
      out.reason = 'code=' + (body ? body.code : 'null') + ' msg=' + (body ? body.msg : '');
      break;
    }
    const d = body.data || {};
    const items = d.lists || [];
    if (out.total === null && typeof d.count !== 'undefined') out.total = d.count;
    if (!items.length) { out.pages.push({ page: page, got: 0 }); break; }
    let older = false;
    for (const it of items) {
      out.items.push(it);
      const day = String(it.publish_time || it.create_time || '').slice(0, 10);
      if (cfg.stopBefore && day && day < cfg.stopBefore) older = true;
    }
    const last = items[items.length - 1];
    out.pages.push({
      page: page, got: items.length,
      oldest: String(last.publish_time || last.create_time || '').slice(0, 10)
    });
    // 文章是从新到旧排的，翻过起始日期就不用再往下翻了
    if (older) break;
    if (out.total !== null && out.items.length >= out.total) break;
  }
  return out;
})("""
            + payload
            + "))"
        )

    async def collect_site_articles(
        self,
        card_name: str,
        stop_before: Optional[date] = None,
        code: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """用新闻站接口把某个栏目拉全。拿不到就返回 None（调用方退回老办法）。

        code 传空字符串表示"全部资讯"（钉钉版左侧新闻资讯栏的口径）。
        """
        runner = await self._ensure_site_runner()
        if runner is None:
            self.log("  找不到新闻站页面，改用门户卡片列表")
            return None
        if code is None:
            codes = await self._section_codes(runner)
            code = codes.get(card_name.strip())
            if not code:
                self.log(f"  新闻站栏目里没有「{card_name}」，改用门户卡片列表")
                return None

        try:
            info = await self._eval(
                runner,
                self._site_list_js(
                    code,
                    stop_before,
                    self.cfg.site_page_size,
                    self.cfg.site_max_pages,
                ),
                timeout=max(120.0, self.cfg.list_load_timeout),
            )
        except CDPError as exc:
            self.log(f"  新闻站接口没成功（{exc}），改用门户卡片列表")
            return None
        if not isinstance(info, dict) or not info.get("ok"):
            reason = (info or {}).get("reason") if isinstance(info, dict) else None
            self.log(f"  新闻站接口返回异常（{reason}），改用门户卡片列表")
            return None

        items = info.get("items") or []
        where = f"section_code={code}" if code else "全部资讯"
        self.log(
            f"  新闻站栏目「{card_name}」({where})："
            f"共 {info.get('total')} 篇，本次翻 {len(info.get('pages') or [])} 页取到 {len(items)} 篇"
        )
        articles: List[Dict[str, Any]] = []
        seen: set = set()
        for it in items:
            aid = it.get("article_id") or it.get("id")
            if aid is None:
                continue
            aid = str(aid)
            if aid in seen:
                continue
            seen.add(aid)
            day = parse_user_date(
                (str(it.get("publish_time") or it.get("create_time") or ""))[:10]
            )
            if day is None:
                day = parse_article_date(str(it.get("image") or ""), "")
            articles.append(
                {
                    "title": it.get("title") or "",
                    "url": f"https://{self.cfg.article_host}/{self.cfg.article_path}?id={aid}",
                    "date": day,
                }
            )
        return articles

    async def count_site_articles(
        self, card_name: str, code: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """只问总数和首页信息，不把整个列表翻完（给 --list 用，快）。"""
        runner = await self._ensure_site_runner()
        if runner is None:
            return None
        if code is None:
            codes = await self._section_codes(runner)
            code = codes.get(card_name.strip())
            if not code:
                return None
        try:
            info = await self._eval(
                runner, self._site_list_js(code, None, 200, 1), timeout=90
            )
        except CDPError:
            return None
        if not isinstance(info, dict) or not info.get("ok"):
            return None
        items = info.get("items") or []
        latest = items[0] if items else None
        oldest = items[-1] if items else None
        return {
            "total": info.get("total"),
            "latest": latest,
            "oldest": oldest,
        }

    def _list_js(self, stop_before: Optional[date], max_pages: int) -> str:
        """按页把列表拉全的脚本。

        为什么不用滚动？看列表页自己的 `_getArticleList`：

            function(){var n=this;!n.isLoading&&!n.isEnd
              && document.scrollingElement.scrollTop+window.innerHeight+n.scrollThreshold
                 >= document.scrollingElement.scrollHeight
              && (n.isLoading=!0, n.getArticle({... pageIndex:n.cardArticleList[...].pageIndex}))
                .then(function(e){n.isLoading=!1, 0===e&&(n.isEnd=!0)})}

        两个要命的点：
          1. 只有"文档滚动条到底"它才加载，滚别的容器没用；
          2. `pageIndex` 是异步回来的响应里自增的。之前为了治"列表卡死"，
             代码会硬把 `isLoading` 改成 false 再调一次——于是同一个 pageIndex
             会发两个请求，两个响应各把 pageIndex 加一次，
             **中间那一页 10 篇就永远跳过了**。这就是"漏识别"的真正原因。

        所以这里改成直接调 store 的 action，自己指定 pageIndex 一页页拉：
        不依赖滚动、不依赖 isLoading。

        还有个坑：列表页渲染出第一屏之后会偷懒，**大约 5 秒后才自己去加载下一页**
        （实测开讲了 15 篇：第 0~4.5 秒一直是 10 篇，第 5 秒才开始加载、5.5 秒变成 15 篇）。
        所以判断"这页没有了"之前必须先确认列表真的安静了，否则会漏掉整整一页。
        """
        payload = json.dumps(
            {
                "stopBefore": stop_before.isoformat() if stop_before else "",
                "maxPages": max_pages,
            },
            ensure_ascii=False,
        )
        return (
            r"""((async (args) => {
  const findVm = () => {
    let node = document.querySelector('.article-item');
    for (let i = 0; i < 25 && node; i += 1, node = node.parentElement) {
      const v = node.__vue__;
      if (v && v.$options && v.$options.name === 'List') return v;
    }
    return null;
  };
  const vm = findVm();
  if (!vm) return { ok: false, reason: 'no-vm' };
  const store = vm.$store;
  const tabs = vm.cardArticleList || (store.state && store.state.cardArticleList) || [];
  const idx = (typeof vm.currentTabIndex === 'number') ? vm.currentTabIndex : 0;
  const tab = tabs[idx];
  if (!tab) return { ok: false, reason: 'no-tab' };

  const pages = [];
  let strikes = 0;
  let guard = 0;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  // 等页面自己的请求落地：它和我们可能同时在拉同一页
  const settle = async (maxMs) => {
    const t0 = Date.now();
    while (vm.isLoading && Date.now() - t0 < maxMs) await sleep(200);
    await sleep(250);
  };
  // 判断"这一页真的没有了"之前，必须确认列表是真的不动了。
  // 实测列表页渲染出第一屏之后，会拖到 5 秒左右才自己去加载下一页
  // （开讲了 15 篇，第 5 秒才从 10 变 15）。早收工就会漏掉那一页。
  // 返回 true = 这 quietMs 毫秒内列表一动不动。
  const waitQuiet = async (quietMs, maxMs) => {
    const t0 = Date.now();
    let lastLen = (tab.list || []).length;
    let quietSince = Date.now();
    while (Date.now() - t0 < maxMs) {
      await sleep(300);
      if ((tab.list || []).length !== lastLen || vm.isLoading) {
        lastLen = (tab.list || []).length;
        quietSince = Date.now();
      }
      if (Date.now() - quietSince >= quietMs) return true;
    }
    return false;
  };
  while (guard < args.maxPages) {
    guard += 1;
    await settle(6000);
    const pageIndex = tab.pageIndex;
    const before = (tab.list || []).length;
    let res = null, err = null;
    try {
      res = await vm.getArticle(Object.assign(
        {}, { ticket: vm.ticket, buId: vm.buId },
        vm.tabSetting[idx], { pageIndex: pageIndex, pageSize: vm.pageSize, source: 1 }
      ));
    } catch (e) { err = String(e); }
    await settle(6000);
    const after = (tab.list || []).length;
    const moved = after > before || tab.pageIndex !== pageIndex;
    pages.push({ pageIndex: pageIndex, grew: after - before, res: res,
                 pageNow: tab.pageIndex });
    if (err) return { ok: false, reason: err, pages: pages };
    if (moved) {
      strikes = 0;
    } else {
      // 返回 0 而且列表一点没动：大概率是到底了，但也可能页面自己正要补下一页。
      // 先等它安静下来再说；这期间又长出来了就接着往下拉。
      if (await waitQuiet(3000, 12000)) {
        // 连续两次"说没有、而且真的半天没动"才算到底
        strikes += 1;
        if (strikes >= 2) break;
      } else {
        strikes = 0;
      }
    }
    if (args.stopBefore) {
      const days = (tab.list || [])
        .map((it) => String(it.publishTime || '').slice(0, 10))
        .filter(Boolean);
      if (days.some((d) => d < args.stopBefore)) break;
    }
  }

  await settle(10000);
  const items = [];
  for (const t of tabs) {
    for (const it of (t.list || [])) {
      if (it && it.url) items.push({
        url: it.url, title: it.title || '',
        time: it.publishTime || '', thumb: it.thumburl || ''
      });
    }
  }
  return { ok: true, items: items, pages: pages, pageIndex: tab.pageIndex };
})("""
            + payload
            + "))"
        )

    @staticmethod
    def _to_articles(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        collected: Dict[str, Dict[str, Any]] = {}
        for item in items:
            url = item.get("url")
            if url and url not in collected:
                collected[url] = item
        articles: List[Dict[str, Any]] = []
        for item in collected.values():
            day = parse_user_date((item.get("time") or "")[:10])
            if day is None:
                # 没有 publishTime 就退回从缩略图地址里推断
                day = parse_article_date(item.get("thumb", ""), "")
            articles.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "date": day,
                }
            )
        return articles

    async def collect_list_articles(
        self, timeout: Optional[float] = None, stop_before=None
    ) -> List[Dict[str, Any]]:
        """把频道列表拉全（标题 + 链接 + 发布日期）。

        优先用 `_list_js` 按页确定性拉取（不会漏、还快很多）；
        万一页面结构变了导致它拿不到东西，再退回老的滚动加载。
        """
        timeout = timeout or self.cfg.list_load_timeout
        deadline = time.monotonic() + timeout
        complained = False
        # 等页面渲染出来的时间单独设上限：真渲染不出来就得赶紧换老办法，
        # 不能把整个 list_load_timeout 都耗在等待上
        ready_deadline = time.monotonic() + min(60.0, timeout)

        while time.monotonic() < deadline and not self._stop():
            if time.monotonic() > ready_deadline:
                self.log("  列表页一直没就绪，改用滚动加载")
                break
            target = self.find_webview(self.cfg.list_host)
            if target is None:
                await asyncio.sleep(1.0)
                continue
            try:
                info = await self._eval(
                    target,
                    self._list_js(stop_before, self.cfg.list_max_pages),
                    timeout=min(180.0, max(30.0, timeout)),
                )
            except CDPError:
                await asyncio.sleep(1.0)
                continue
            if isinstance(info, dict) and info.get("ok"):
                articles = self._to_articles(info.get("items") or [])
                pages = info.get("pages") or []
                self.log(
                    f"  列表按页拉取：{len(pages)} 页 → {len(articles)} 篇"
                )
                # pages 非空说明真的跟服务端对上话了：就算 0 篇也是"这个频道就是空的"，
                # 不能再傻等；pages 为空才是页面还没起来，需要重试
                if articles or pages:
                    return articles
            reason = info.get("reason") if isinstance(info, dict) else None
            # no-vm / no-tab 只是"页面还没渲染出来"，等一下再来；
            # 其它原因（比如取列表的请求直接抛错）才说明这条路走不通
            if reason and reason not in ("no-vm", "no-tab"):
                self.log(f"  按页拉取没成功（{reason}），改用滚动加载")
                break
            if not complained:
                self.log("  列表页还在渲染，等它就绪…")
                complained = True
            await asyncio.sleep(2.0)

        return await self._collect_list_by_scroll(timeout, stop_before)

    async def _collect_list_by_scroll(
        self, timeout: Optional[float] = None, stop_before=None
    ) -> List[Dict[str, Any]]:
        """兜底用的滚动加载（按页拉取失败时才走这里）。

        列表页是无限滚动的，一屏只有 10 篇。数据从 Vuex store 的
        `cardArticleList[].list` 里读，每篇都带 url / title / publishTime。

        这里的 `isLoading` 只在连续多轮毫无增长时才手动解开：
        它一卡住页面就不加载了，但在请求飞行途中解开会让同一个 pageIndex
        发两次、跳过一页（见 `_list_js` 的注释），所以不能一发现 isLoading
        就动手。
        """
        read_js = (
            "(() => {\n"
            "  let node = document.querySelector('.article-item'), vm = null, store = null;\n"
            "  for (let i = 0; i < 25 && node; i += 1, node = node.parentElement) {\n"
            "    const v = node.__vue__;\n"
            "    if (v && v.$store) {\n"
            "      store = v.$store;\n"
            "      if (v.$options && v.$options.name === 'List') vm = v;\n"
            "      if (vm) break;\n"
            "    }\n"
            "  }\n"
            "  if (!store) return { ok: false };\n"
            "  const items = [];\n"
            "  for (const tab of (store.state.cardArticleList || [])) {\n"
            "    for (const it of (tab.list || [])) {\n"
            "      if (it && it.url) items.push({ url: it.url, title: it.title || '',\n"
            "        time: it.publishTime || '', thumb: it.thumburl || '' });\n"
            "    }\n"
            "  }\n"
            "  return { ok: true, items: items,\n"
            "           isLoading: vm ? !!vm.isLoading : null,\n"
            "           isEnd: vm ? !!vm.isEnd : null };\n"
            "})()"
        )
        drive_js = (
            "(() => {\n"
            "  let node = document.querySelector('.article-item'), vm = null;\n"
            "  for (let i = 0; i < 25 && node; i += 1, node = node.parentElement) {\n"
            "    const v = node.__vue__;\n"
            "    if (v && v.$options && v.$options.name === 'List') { vm = v; break; }\n"
            "  }\n"
            "  if (!vm) return 'no-vm';\n"
            "  const stuck = !!vm.isLoading;\n"
            # 只在真的卡住（连续几轮毫无增长）时才解开，
            # 避免请求还在飞的时候重复发同一个 pageIndex
            "  if (stuck && __UNSTICK__) vm.isLoading = false;\n"
            "  try { vm._getArticleList(); } catch (e) {}\n"
            "  for (const el of document.querySelectorAll('*')) {\n"
            "    if (el.scrollHeight > el.clientHeight + 40 && el.clientHeight > 120 &&\n"
            "        el.querySelector && el.querySelector('.article-item')) {\n"
            "      el.scrollTop = el.scrollHeight;\n"
            "    }\n"
            "  }\n"
            "  return stuck ? 'unstuck' : 'ok';\n"
            "})()"
        )

        timeout = timeout or self.cfg.list_load_timeout
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        collected: Dict[str, Dict[str, Any]] = {}
        stale = 0

        while loop.time() < deadline and not self._stop():
            target = self.find_webview(self.cfg.list_host)
            if target is None:
                # 处理阶段会把列表页导航走，那时就没有它了
                break
            try:
                info = await self._eval(target, read_js) or {}
            except CDPError:
                await asyncio.sleep(self.cfg.list_scroll_interval)
                continue
            if not info.get("ok"):
                await asyncio.sleep(self.cfg.list_scroll_interval)
                continue

            before = len(collected)
            for item in info.get("items") or []:
                url = item.get("url")
                if url and url not in collected:
                    collected[url] = item
            stale = 0 if len(collected) > before else stale + 1

            if info.get("isEnd"):
                break
            if stop_before is not None:
                days = [
                    d
                    for d in (
                        parse_user_date((v.get("time") or "")[:10])
                        for v in collected.values()
                    )
                    if d is not None
                ]
                if days and min(days) < stop_before:
                    break
            if len(collected) >= 5000 or stale >= self.cfg.list_scroll_stable:
                break

            try:
                await self._eval(
                    target,
                    drive_js.replace("__UNSTICK__", "true" if stale >= 3 else "false"),
                )
            except CDPError:
                pass
            await asyncio.sleep(self.cfg.list_scroll_interval)

        return self._to_articles(list(collected.values()))

    async def collect_list_titles(self, timeout: Optional[float] = None) -> List[str]:
        return [a["title"] for a in await self.collect_list_articles(timeout)]

    async def like_from_list(self, title: str) -> str:
        """从频道列表页点开文章并点赞。"""
        list_target = self.find_webview(self.cfg.list_host)
        if list_target is None:
            return "no-list"
        before = {t.get("id") for t in self.targets()}

        # 先走快路径：页面内派发点击，通常 0.5 秒内就把文章窗口点出来
        article = None
        if await self.click_text_js(list_target, title):
            article = await self._wait_for_new_target(
                before, self.cfg.article_webview_hint, timeout=self.cfg.js_click_wait
            )

        # 快路径没点开就退回真实鼠标点击（慢，但更接近手工操作）
        if article is None:
            if not await self._click_text_mouse(list_target, title):
                return "click-failed"
            article = await self._wait_for_new_target(
                before, self.cfg.article_webview_hint, timeout=25
            )
        if article is None:
            return "no-window"

        url = article.get("url") or ""
        marker = self.cfg.article_path + "?id=" + url.split("id=", 1)[1].split("&", 1)[0]
        article_id = marker.split("id=", 1)[1]
        try:
            return await self._like_article_target(marker, target_id=article.get("id"))
        finally:
            await self._close_tab("id=" + article_id)

    # ------------------------------------------------------------------
    # 会话型板块
    # ------------------------------------------------------------------
    async def switch_to_messages(self) -> bool:
        """切到消息视图。切换是异步的，所以要点完再确认真的切过去了。"""
        main = self.find_target("dashboard.html")
        if main is None:
            return False
        js = (
            "(() => { const el = document.querySelector('.navi-left a[title=\"消息\"]')"
            " || document.querySelector('a[title=\"消息\"]');"
            " if (!el) return false; el.click(); return true; })()"
        )
        # 有些状态下会话列表已经在 DOM 里但视图没显示，所以按可见性判断
        visible_js = (
            "(() => { const el = document.querySelector('.session.layout-im');"
            " if (!el) return false; const r = el.getBoundingClientRect();"
            " return r.height > 200 && r.top > -20 && r.left >= 0; })()"
        )
        for _ in range(3):
            main = self.find_target("dashboard.html")
            if main is None:
                return False
            try:
                if await self._eval(main, visible_js):
                    return True
                await self._eval(main, js)
            except CDPError:
                pass
            await asyncio.sleep(3)
        try:
            return bool(await self._eval(main, visible_js))
        except CDPError:
            return False

    async def open_session(self, name: str) -> bool:
        """点开订阅号会话，并等它的消息渲染出来。"""
        main = self.find_target("dashboard.html")
        if main is None:
            return False
        js = (
            r"""((name) => {
              for (const li of document.querySelectorAll('li.session-item')) {
                const n = li.querySelector('.name .ellipsis');
                if (n && n.innerText.trim() === name) {
                  const a = li.querySelector('a[data-id]') || li;
                  const r = a.getBoundingClientRect();
                  return { found: true, x: r.left + r.width / 2,
                           y: r.top + r.height / 2 };
                }
              }
              return { found: false };
            })("""
            + json.dumps(name, ensure_ascii=False)
            + ")"
        )
        for attempt in range(3):
            main = self.find_target("dashboard.html")
            if main is None:
                return False
            try:
                async with CDPSession(
                    main["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout
                ) as s:
                    await s.send("Runtime.enable")
                    info = await s.evaluate(js)
                    if not info or not info.get("found"):
                        await asyncio.sleep(2)
                        continue
                    await s.click_point(info["x"], info["y"])
            except CDPError:
                await asyncio.sleep(2)
                continue
            # 等消息渲染出来（这是判断"会话真的打开了"最可靠的信号）
            for _ in range(12):
                await asyncio.sleep(2)
                try:
                    n = await self._eval(
                        main, "document.querySelectorAll('div.news-item.msg-item').length"
                    )
                except CDPError:
                    n = 0
                if n:
                    return True
            await asyncio.sleep(2)
        return False

    async def collect_session_articles(self, timeout: float = 90.0) -> List[Dict[str, str]]:
        """滚动加载会话历史，从消息组件数据里抽出所有文章 {title, url}。

        会话消息卡片背后的 Vue 组件（name=NewsItem）里，msg.param.list
        直接带着每篇文章的完整 URL —— 因为有它，就不用去点卡片了。
        """
        scroll_js = (
            "(() => { const b = document.querySelector('.msg-list-wrap')"
            " || document.querySelector('.msg-area');"
            " if (!b) return -1; b.scrollTop = 0; return b.scrollTop; })()"
        )
        extract_js = (
            r"""(() => {
              const out = [];
              document.querySelectorAll('div.news-item.msg-item').forEach((el) => {
                const vm = el.__vue__;
                const msg = vm && vm.$props ? vm.$props.msg : (vm ? vm.msg : null);
                const list = msg && msg.param ? msg.param.list : null;
                const sendTime = msg && msg.sendTime ? msg.sendTime : '';
                if (Array.isArray(list)) {
                  list.forEach((a) => {
                    if (a && a.url) out.push({
                      title: a.title || a.text || '',
                      url: a.url,
                      image: a.thumbUrl || a.name || '',
                      dateText: sendTime
                    });
                  });
                }
              });
              return out;
            })()"""
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        found: Dict[str, Dict[str, Any]] = {}
        stable = 0
        while loop.time() < deadline and not self._stop():
            main = self.find_target("dashboard.html")
            if main is None:
                await asyncio.sleep(self.cfg.poll_interval)
                continue
            try:
                await self._eval(main, scroll_js)
                items = await self._eval(main, extract_js) or []
            except CDPError:
                await asyncio.sleep(self.cfg.poll_interval)
                continue
            before = len(found)
            for item in items:
                url = item.get("url")
                if url and url not in found:
                        found[url] = {
                            "title": item.get("title") or "",
                            "url": url,
                            "date": parse_article_date(
                                item.get("image", ""), item.get("dateText", "")
                            ),
                        }
            if len(found) > before:
                stable = 0
            else:
                stable += 1
                if stable >= 3:
                    break
            await asyncio.sleep(self.cfg.poll_interval)
        return list(found.values())

    async def ensure_article_webview(self) -> Optional[Dict[str, Any]]:
        """找一个能随便导航的 webview 当"浏览器"用。

        门户那个 webview 不能动（会把门户页毁掉），所以优先用文章页，
        其次是临时打开的频道列表页；都没有就从门户点一篇文章把窗口建出来。
        """
        view = self.find_webview(self.cfg.article_webview_hint)
        if view is not None:
            return view
        view = self.find_webview(self.cfg.list_host)
        if view is not None:
            return view

        portal = self.find_webview(self.cfg.portal_url_hint)
        if portal is None:
            return None
        before = {t.get("id") for t in self.targets()}
        js = (
            "(() => { const el = document.querySelector("
            + json.dumps(self.cfg.article_title_selector)
            + "); if (!el) return { found: false };"
            " el.scrollIntoView({ block: 'center' });"
            " const r = el.getBoundingClientRect();"
            " return { found: true, x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()"
        )
        try:
            async with CDPSession(
                portal["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout
            ) as s:
                await s.send("Runtime.enable")
                info = await s.evaluate(js)
                if not info or not info.get("found"):
                    return None
                await s.click_point(info["x"], info["y"])
        except CDPError:
            return None
        await self._wait_for_new_target(before, self.cfg.article_webview_hint, timeout=25)
        return self.find_webview(self.cfg.article_webview_hint)

    async def process_article_url(self, url: str, max_wait: Optional[float] = None) -> str:
        """把 webview 导航到文章地址，读状态并点赞。

        每次导航都会重建 webview 目标（连接被掐断），所以用 URL 里的 id 认它。
        """
        view = await self.ensure_article_webview()
        if view is None:
            return "no-webview"
        try:
            async with CDPSession(view["webSocketDebuggerUrl"], timeout=25) as s:
                await s.navigate(url)
        except CDPError:
            pass

        marker = self.cfg.article_path + "?id=" + url.split("id=", 1)[1].split("&", 1)[0]
        return await self._like_article_target(marker, max_wait)

    async def _like_article_target(
        self,
        marker: str,
        max_wait: Optional[float] = None,
        allow_rewarm: bool = True,
        target_id: Optional[str] = None,
    ) -> str:
        timeout = max_wait or self.cfg.page_timeout
        found_id, state = await self._wait_praise(marker, timeout, target_id=target_id)
        if state is None:
            return "no-button"

        if self._looks_logged_out(state):
            # 登录态掉了就再走一次门户预热（只重试一次，避免死循环）
            if allow_rewarm and await self.warm_up_login():
                return await self._like_article_target(
                    marker, max_wait, allow_rewarm=False, target_id=target_id
                )
            return "need-login"

        if state.get("liked"):
            return "already-liked"
        if self.dry_run:
            return f"dry-run(count={state['count']})"

        current = None
        if found_id:
            current = next((t for t in self.targets() if t.get("id") == found_id), None)
        if current is None and target_id:
            current = next((t for t in self.targets() if t.get("id") == target_id), None)
        if current is None:
            current = self.find_webview(marker)
        if current is None:
            return "no-button"
        try:
            async with CDPSession(current["webSocketDebuggerUrl"], timeout=timeout) as s:
                await s.send("Runtime.enable")
                try:
                    await s.send("Page.bringToFront")
                except CDPError:
                    pass
                await s.click_point(state["x"], state["y"])
                # 轮询等状态变化，一变就返回；不再盲等固定的 4 秒 + 5 秒。
                # 原来那样既慢，又会在状态刷新稍慢时误判成没点上。
                before = state.get("count")
                deadline = asyncio.get_event_loop().time() + self.cfg.verify_timeout
                while asyncio.get_event_loop().time() < deadline:
                    await asyncio.sleep(self.cfg.verify_interval)
                    after = await s.evaluate(self._read_praise_js())
                    if after and (
                        after.get("liked") or after.get("count") != before
                    ):
                        return f"liked({before}->{after.get('count')})"
                return "no-change"
        except CDPError:
            return "no-change"

    async def _wait_praise(
        self, marker: str, timeout: float, target_id: Optional[str] = None
    ) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
        """等文章页把点赞区域渲染出来，返回 (目标id, 状态)。

        实测同一篇文章会有好几个同名 webview：刚新建的那个可能是空的，
        真正渲染好的是另一个，另外还可能有上次遗留的失效页面。
        所以这里把候选全试一遍，谁先渲染出点赞区域就用谁。

        性能上：**复用一条 CDP 会话**。原来每轮轮询都重新建一次连接，
        建连 + Runtime.enable + bringToFront 的开销比页面本身渲染还大，
        导致等待时间在 1.6~17 秒之间乱跳。现在只在会话失效时才重连。
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        logged_out_state: Optional[Dict[str, Any]] = None
        session: Optional[CDPSession] = None
        session_target_id: Optional[str] = None
        last_tried: Optional[str] = None
        polls_on_session = 0

        async def drop_session() -> None:
            nonlocal session, session_target_id
            if session is not None:
                try:
                    await session.close()
                except Exception:
                    pass
            session = None
            session_target_id = None

        try:
            while loop.time() < deadline and not self._stop():
                candidates: List[Dict[str, Any]] = []
                if target_id:
                    preferred = next(
                        (t for t in self.targets() if t.get("id") == target_id), None
                    )
                    if preferred is not None:
                        candidates.append(preferred)
                for t in self.targets():
                    if t.get("type") != "webview" or marker not in (t.get("url") or ""):
                        continue
                    if any(c.get("id") == t.get("id") for c in candidates):
                        continue
                    candidates.append(t)

                if not candidates:
                    await asyncio.sleep(self.cfg.poll_interval)
                    continue

                # 常驻会话的目标如果已经不在了（页面重载会换 id），就重连；
                # 同一个目标试了 2 秒多还没渲染出来，就换下一个候选试试
                # （刚新建的那个常常是空壳，真正渲染好的是另一个）
                live_ids = {c.get("id") for c in candidates}
                if session is not None and (
                    session_target_id not in live_ids or polls_on_session >= 6
                ):
                    await drop_session()

                if session is None:
                    current = next(
                        (c for c in candidates if c.get("id") != last_tried), candidates[0]
                    )
                    session = CDPSession(
                        current["webSocketDebuggerUrl"], timeout=timeout
                    )
                    try:
                        await session.connect()
                        await session.send("Runtime.enable")
                        try:
                            # 文章页也可能是后台标签，拉回前台再读，避免被限流
                            await session.send("Page.bringToFront")
                        except CDPError:
                            pass
                    except CDPError:
                        await drop_session()
                        await asyncio.sleep(self.cfg.poll_interval)
                        continue
                    session_target_id = current.get("id")
                    last_tried = session_target_id
                    polls_on_session = 0

                try:
                    state = await session.evaluate(self._read_praise_js())
                except CDPError:
                    await drop_session()
                    await asyncio.sleep(self.cfg.poll_interval)
                    continue
                polls_on_session += 1

                if state and state.get("found"):
                    found_id = session_target_id
                    await drop_session()
                    return found_id, state
                if self._looks_logged_out(state) and logged_out_state is None:
                    logged_out_state = state
                await asyncio.sleep(self.cfg.poll_interval)
        finally:
            await drop_session()
        # 所有候选都没渲染出点赞区域；如果期间看到过"登录超时"就报它
        return None, logged_out_state

    # ------------------------------------------------------------------
    # 总入口
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # 接口批量模式
    #
    # 登录之后，新闻站的接口是可以直接用的：
    #   GET  /article/detail?id=     读状态（is_digg / digg_count）
    #   POST /article/addDigg        点赞
    #   POST /article/cancelDigg     取消赞
    # 所以根本不用逐个打开文章页面——既快（每篇几百毫秒），也不会产生僵尸页面。
    # 前提是拿得到文章的数字 id，这个从列表页 Vuex store 里的 url 就能取到。
    # ------------------------------------------------------------------
    def _article_id(self, url: str) -> Optional[str]:
        if not url or "id=" not in url:
            return None
        return url.split("id=", 1)[1].split("&", 1)[0]

    async def ensure_api_runner(
        self, articles: List[Dict[str, Any]], force: bool = False
    ):
        """找一个 tbeanews 域下的页面，用来在它的上下文里发接口请求。

        必须是同域的页面，否则 fetch 会被 CORS 拦掉。
        force=True 时即使已经有了也把它重新导航一次（连接池堵住时用来复位）。
        """
        target_url = ""
        for item in articles:
            if item.get("url"):
                target_url = item["url"]
                break

        if not force:
            runner = self.find_webview("tbeanews.tbea.com")
            if runner is not None and await self._looks_alive(runner):
                return runner

        # 列表页是 ejia.tbea.com，跨域用不了；把它（或已经卡住的接口页）
        # 导航到一篇新闻上，就得到一个同域可用的页面
        view = self.find_webview(self.cfg.list_host) or self.find_webview(
            "tbeanews.tbea.com"
        )
        if view is None or not target_url:
            return None
        try:
            async with CDPSession(view["webSocketDebuggerUrl"], timeout=25) as s:
                await s.navigate(target_url)
        except CDPError:
            pass
        # 导航完目标会重建，而且要等页面真的能跑 JS 才算就绪
        for _ in range(30):
            await asyncio.sleep(1.0)
            runner = self.find_webview("tbeanews.tbea.com")
            if runner is None:
                continue
            try:
                if await self._eval(runner, "1 + 1") == 2:
                    return runner
            except CDPError:
                continue
        return None

    async def _looks_alive(self, target: Dict[str, Any]) -> bool:
        """这个页面的 JS 还能不能跑。"""
        try:
            return await self._eval(target, "1 + 1", timeout=10) == 2
        except CDPError:
            return False

    def _api_batch_js(self, ids: List[str], do_like: bool) -> str:
        payload = json.dumps(
            {
                "base": self.cfg.api_base,
                "ids": ids,
                "like": do_like,
                "limit": max(1, self.cfg.api_concurrency),
                "timeout": int(self.cfg.api_request_timeout * 1000),
                "read": bool(self.cfg.api_read_state),
                "verify": bool(self.cfg.api_verify),
            },
            ensure_ascii=False,
        )
        return (
            r"""((async (cfg) => {
  const base = cfg.base, ids = cfg.ids, doLike = cfg.like;
  const limit = cfg.limit, reqTimeout = cfg.timeout;
  const out = [];

  const withTimeout = (work) => new Promise((resolve) => {
    const ctl = new AbortController();
    const timer = setTimeout(() => { try { ctl.abort(); } catch (e) {} }, reqTimeout);
    Promise.resolve()
      .then(() => work(ctl.signal))
      .then((v) => { clearTimeout(timer); resolve(v); })
      .catch(() => { clearTimeout(timer); resolve(null); });
  });

  // 只放 limit 条并发。Chromium 对同一个域名本来就只有 6 条连接，
  // 一次发二十个只会排队，严重时把连接池堵死（实测整批 30 秒不返回）。
  const runAll = async (items, worker) => {
    const res = new Array(items.length);
    let cursor = 0;
    const runners = [];
    const n = Math.max(1, Math.min(limit, items.length));
    for (let k = 0; k < n; k++) {
      runners.push((async () => {
        while (true) {
          const i = cursor++;
          if (i >= items.length) return;
          res[i] = await withTimeout((signal) => worker(items[i], signal));
        }
      })());
    }
    await Promise.all(runners);
    return res;
  };

  const readOne = (id, signal) =>
    fetch(base + '/article/detail?id=' + encodeURIComponent(id),
          { credentials: 'include', signal: signal })
      .then((r) => r.json())
      .then((j) => (j && j.data)
        ? { id: id, liked: !!j.data.is_digg, count: j.data.digg_count }
        : { id: id, bad: true })
      .catch(() => ({ id: id, bad: true }));

  const likeOne = (id, signal) =>
    fetch(base + '/article/addDigg', {
      method: 'POST', credentials: 'include', signal: signal,
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ id: id }).toString()
    }).then((r) => r.json()).then((j) => !!(j && j.code === 1)).catch(() => false);

  // 实测：详情接口平均 2.5 秒，慢的时候 8 秒以上，是最慢的一步；
  // 点赞接口只要 0.6 秒左右，而且对已经点过的文章再点一次同样返回 code=1，
  // 不会重复计数（实测 8 篇里 7 篇计数完全不变，is_digg 一直是 true）。
  // 所以默认不查状态，直接点赞：每篇 1 次请求，既快又不容易超时。
  let todo = ids.map((id) => ({ id: id, count: null }));
  // 状态没读到的：照样把赞发出去（接口幂等），但状态未知，
  // 不能算进"新点赞"，只能记"已确认点赞"。
  const blind = [];

  // 可选：点赞前先查状态，能区分"本来就没赞"和"早就赞过"，
  // 代价是每篇多一次慢查询。
  if (cfg.read) {
    const states = await runAll(todo, (s, signal) => readOne(s.id, signal));
    // 详情接口偶尔会超时（实测 400 篇里有 5 篇）。这些先补读一次，
    // 能读出来就能准确归类；补读还是失败才走"盲点赞"，并如实标成"已确认点赞"。
    const missed = [];
    for (let i = 0; i < todo.length; i++) {
      const s = states[i];
      if (!s || s.bad) missed.push(i);
    }
    if (missed.length) {
      const again = await runAll(
        missed.map((i) => todo[i]), (s, signal) => readOne(s.id, signal)
      );
      for (let k = 0; k < missed.length; k++) states[missed[k]] = again[k];
    }
    const kept = [];
    for (let i = 0; i < todo.length; i++) {
      const s = states[i];
      if (!s || s.bad) { blind.push(todo[i]); continue; }
      if (s.liked) { out.push({ id: s.id, status: 'already-liked' }); continue; }
      kept.push({ id: s.id, count: s.count });
    }
    todo = kept;
  }

  if (todo.length) {
    if (!doLike) {
      for (const s of todo) out.push({ id: s.id, status: 'dry-run', count: s.count });
    } else {
      const acted = await runAll(todo, (s, signal) => likeOne(s.id, signal));
      const done = [];
      for (let i = 0; i < todo.length; i++) {
        if (acted[i] === true) done.push(todo[i]);
        else out.push({ id: todo[i].id, status: 'like-failed' });
      }
      // 可选：点赞后再查一次，作为"服务端状态确实变成已赞"的佐证。
      // 默认关掉——点赞接口返回 code=1 本身就是服务端确认。
      if (cfg.verify && done.length) {
        const fresh = await runAll(done, (s, signal) => readOne(s.id, signal));
        for (let i = 0; i < done.length; i++) {
          const f = fresh[i];
          const ok = f && !f.bad && f.liked;
          const after = (f && !f.bad) ? f.count : null;
          if (cfg.read) {
            out.push({ id: done[i].id, status: ok ? 'liked' : 'like-unconfirmed',
                       after: after });
          } else {
            out.push({ id: done[i].id,
                       status: ok ? 'digg-sent' : 'digg-unconfirmed', after: after });
          }
        }
      } else {
        // 查过状态的才敢说"新点赞"；没查状态只能报"点赞请求已确认"
        for (const s of done) {
          out.push({ id: s.id, status: cfg.read ? 'liked' : 'digg-sent' });
        }
      }
    }
  }

  if (blind.length) {
    if (!doLike) {
      for (const s of blind) out.push({ id: s.id, status: 'read-failed' });
    } else {
      const acted = await runAll(blind, (s, signal) => likeOne(s.id, signal));
      for (let i = 0; i < blind.length; i++) {
        out.push({ id: blind[i].id,
                   status: acted[i] === true ? 'digg-sent' : 'like-failed' });
      }
    }
  }
  return out;
})("""
            + payload
            + "))"
        )


    def _api_status_text(self, row: Dict[str, Any]) -> str:
        """给人看的标签（不带括号里的数字）。

        统计用的 key 一律是**接口返回的原始状态名**（见 done_ids），
        这里只负责显示——两者千万别混用一个字符串，
        之前就是拿显示文字当 key，结果 `liked(count=123)` 被算成"未成功"。
        """
        status = row.get("status")
        if status == "already-liked":
            return "原本已点赞"
        if status == "dry-run":
            return "待点赞"
        if status == "liked":
            return "新点赞"
        if status in ("like-unconfirmed", "digg-unconfirmed"):
            return "点赞已受理待复查"
        if status == "digg-sent":
            return "已确认点赞"
        if status == "no-change":
            return "没看到状态变化"
        if status == "like-failed":
            return "点赞失败"
        if status == "read-failed":
            return "读状态失败"
        return status or "unknown"

    @staticmethod
    def _api_log_text(row: Dict[str, Any], label: str) -> str:
        """日志行里带上计数，方便肉眼核对。"""
        after = row.get("after")
        count = row.get("count")
        if row.get("status") in ("liked", "digg-sent") and after is not None:
            return f"{label}(赞数 {after})"
        if row.get("status") == "dry-run" and count is not None:
            return f"{label}(赞数 {count})"
        return label

    async def process_articles_via_api(
        self, articles: List[Dict[str, Any]], result: "SectionResult"
    ) -> Optional[List[Dict[str, Any]]]:
        """走接口把这批文章处理完。

        返回**没能处理掉的文章**（可能为空）；接口完全用不了时返回 None，
        两种情况都表示调用方要用打开文章的方式来兜底。

        注意不要返回"处理成功的篇数"：失败是零散分布的，用篇数当游标
        会让调用方从头重跑一遍（早期版本的性能事故就是这么来的）。
        """
        plan: List[tuple] = []
        for item in articles:
            aid = self._article_id(item.get("url", ""))
            if aid:
                plan.append((aid, item))
        if not plan:
            return None

        runner = await self.ensure_api_runner(articles)
        if runner is None:
            self.log("  找不到可用的新闻站页面，改用打开文章的方式")
            return None

        mode = "查状态+点赞" if self.cfg.api_read_state else "直接点赞"
        if self.dry_run:
            mode = "只检查"
        self.log(
            f"  接口模式：{len(plan)} 篇，每批 {self.cfg.api_batch_size} 个、"
            f"并发 {self.cfg.api_concurrency}，{mode}"
        )
        try:
            async with CDPSession(runner["webSocketDebuggerUrl"], timeout=20) as s:
                await s.send("Page.bringToFront")
        except CDPError:
            pass

        done_ids: Dict[str, str] = {}
        order = [aid for aid, _ in plan]
        index_of = {aid: i for i, aid in enumerate(order)}
        item_of: Dict[str, Dict[str, Any]] = {aid: item for aid, item in plan}
        size = max(1, self.cfg.api_batch_size)
        pending = list(range(len(plan)))
        rounds = 1 + max(0, self.cfg.api_like_rounds)
        stopped = False

        # 起始速度用配置里的 api_rate；一旦撞上服务端配额就自动降速，
        # 免得后面每一批都白撞一次（翻页请求也占同一份额度，所以不能按
        # "每分钟刚好 120 次"去卡）
        rate = max(0.0, self.cfg.api_rate)

        for round_no in range(1, rounds + 1):
            if not pending or stopped:
                break
            if round_no > 1:
                # 服务端限流是按时间窗算的，立刻重试只会撞回同一个窗口
                wait = max(0.0, self.cfg.api_backoff) * (2 ** (round_no - 2))
                if wait:
                    self.log(f"  等 {wait:.0f} 秒让服务端缓一缓，再重试…")
                    await asyncio.sleep(wait)
                self.log(f"  第 {round_no} 轮：重试上一轮没成功的 {len(pending)} 篇")
            this_round = pending
            pending = []
            for start in range(0, len(this_round), size):
                if self._stop():
                    self.log("  已手动停止")
                    stopped = True
                    pending.extend(this_round[start:])
                    break
                chunk = [plan[i] for i in this_round[start : start + size]]
                ids = [aid for aid, _ in chunk]
                chunk_started = time.monotonic()
                rows = None
                for attempt in range(3):
                    try:
                        rows = await self._eval(
                            runner,
                            self._api_batch_js(ids, not self.dry_run),
                            timeout=self.cfg.api_batch_timeout,
                        )
                        if isinstance(rows, list) and len(rows) == len(ids):
                            break
                        rows = None
                    except CDPError:
                        rows = None
                    # 失败多半是页面卡住了：重新导航一次把它复位，再试同一批
                    self.log(f"  第 {attempt + 1} 次没成功，刷新接口页面后重试…")
                    runner = await self.ensure_api_runner(
                        [item for _, item in chunk], force=True
                    )
                    if runner is None:
                        break
                    await asyncio.sleep(1.0)

                if rows is None:
                    # 整批拿不到结果，留到下一轮
                    self.log(f"  这一批 {len(ids)} 篇没拿到结果，留到下一轮")
                    pending.extend(this_round[start : start + size])
                    continue

                by_id = {r.get("id"): r for r in rows if isinstance(r, dict)}
                failed_here: Dict[str, int] = {}
                for aid, item in chunk:
                    row = by_id.get(aid)
                    # 判断成败要看接口回来的**原始状态**，不能拿展示用的文字去比：
                    # dry-run 的展示文字是 "dry-run(count=12)"，拿它跟 "dry-run" 比
                    # 永远不相等，于是试运行会被当成整批失败（踩过这个坑）。
                    raw = row.get("status") if isinstance(row, dict) else None
                    status = self._api_status_text(row) if row else "read-failed"
                    if raw in _API_OK:
                        # 统计用原始状态名，显示用中文标签，别混用
                        done_ids[aid] = raw
                        day = item.get("date")
                        stamp = f"{day:%m-%d} " if day else "      "
                        shown = (
                            self._api_log_text(row, status) if row else status
                        )
                        self.log(
                            f"  [{len(done_ids)}/{len(plan)}] {stamp}{shown:<24} "
                            f"{item.get('title', '')[:30]}"
                        )
                    else:
                        pending.append(index_of[aid])
                        failed_here[status] = failed_here.get(status, 0) + 1
                if failed_here:
                    self.log(f"  这一批有 {sum(failed_here.values())} 篇没成功：{failed_here}")
                    if rate > 0:
                        rate = max(0.6, rate * 0.7)
                        self.log(f"  像是撞到服务端配额了，把发请求的速度降到 {rate:.2f}/秒")

                # 限速：把这一批的请求摊到 api_rate 允许的时间长度里
                if rate and rate > 0 and start + size < len(this_round):
                    budget = len(ids) / rate
                    spent = time.monotonic() - chunk_started
                    if spent < budget:
                        await asyncio.sleep(budget - spent)

        # 收尾统计：只把成功的记进 stats；没成功的交给调用方用页面方式处理，
        # 那一步的结果才是这些文章真正的结果
        for aid, status in done_ids.items():
            result.stats[status] = result.stats.get(status, 0) + 1

        failed = [item_of[plan[i][0]] for i in pending]
        if stopped:
            # 手动停止：剩下的交给调用方不合适，直接报剩余
            return [item_of[order[j]] for j in range(len(order)) if order[j] not in done_ids]
        if failed:
            result.api_failed = len(failed)
            self.log(
                f"  接口模式：成功 {len(order) - len(failed)}/{len(order)} 篇，"
                f"剩 {len(failed)} 篇改用打开文章的方式"
            )
        return failed

    def date_range(self):
        """返回配置里的 (起, 止)，没配就是 None。"""
        return (
            parse_user_date(self.cfg.filter_since),
            parse_user_date(self.cfg.filter_until),
        )

    def filter_by_date(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按配置的时间段筛掉不在范围内的文章。"""
        since, until = self.date_range()
        if since is None and until is None:
            return articles

        kept: List[Dict[str, Any]] = []
        skipped = 0
        unknown = 0
        for item in articles:
            day = item.get("date")
            if day is None:
                # 认不出日期的宁可留着，也不要漏掉
                unknown += 1
                kept.append(item)
                continue
            if since is not None and day < since:
                skipped += 1
                continue
            if until is not None and day > until:
                skipped += 1
                continue
            kept.append(item)

        span = []
        if since is not None:
            span.append(f"{since:%Y-%m-%d} 起")
        if until is not None:
            span.append(f"{until:%Y-%m-%d} 止")
        self.log(f"  时间段筛选：{'、'.join(span)} → 保留 {len(kept)} 篇，跳过 {skipped} 篇")
        if unknown:
            self.log(f"  （其中 {unknown} 篇日期没认出来，为保证不漏已照常处理）")
        return kept

    async def _recycle_client(self, card_name: str) -> bool:
        """重启客户端，清掉它累积的页面，然后重新打开频道。

        客户端每打开一篇文章都会新建一个 webview 且从不回收，跑几百篇会
        明显拖慢甚至超时。重启是最安全的清理方式（手动删它的 DOM 会把
        窗口的内部记账弄坏，试过，不行）。
        """
        from . import client as client_mod

        exe = client_mod.resolve_client_exe(self.cfg.client_exe_candidates, self.log)
        if exe is None:
            self.log("  找不到 TBe.exe，没法重启")
            return False

        self.log("  重启客户端清理累积的页面（大约 1 分钟）…")
        preferred = self.cfg.debug_port
        # 先真的关掉，否则 restart_client 会认为"已经在跑"直接返回
        client_mod.stop_client(self.log)
        used = client_mod.restart_client(
            exe, preferred, self.cfg.restart_wait_seconds, self.log
        )
        if not used:
            return False
        self.cfg.debug_port = used
        if used != preferred:
            client_mod.save_debug_port(used)

        if not await self.warm_up_login():
            self.log("  重启后登录预热没成功，继续尝试打开频道")
        if await self.open_channel(card_name) is None:
            self.log("  重启后没能打开频道列表页")
            return False
        await asyncio.sleep(5)
        self.log("  客户端已重启，继续处理")
        return True

    async def run_section(
        self, card_name: str, limit: int = 0, code: Optional[str] = None
    ) -> SectionResult:
        result = SectionResult(name=card_name)
        self.log("")
        self.log("=" * 60)
        self.log(f"板块：{card_name}")
        since, _until = self.date_range()
        if code == "" and since is None:
            # 钉钉版"新闻资讯"是各公司订阅号的全部资讯（一万多篇），
            # 不设起始日期就全量跑要几个小时，这里给个保守默认值
            days = max(1, self.cfg.dingtalk_default_days)
            since = date.today() - timedelta(days=days)
            self.log(
                f"  没设置起始日期：钉钉版新闻有上万篇，先只跑最近 {days} 天"
                f"（从 {since} 起）。要更多请在时间段里选起始日期。"
            )

        # 首选：新闻站自己的栏目接口（能翻到很早的文章，门户那张卡片只给 200 篇）
        articles: List[Dict[str, Any]] = []
        if self.cfg.site_api:
            articles = (
                await self.collect_site_articles(
                    card_name, stop_before=since, code=code
                )
                or []
            )
        if not articles:
            if code is not None:
                # 这个板块只存在于新闻站接口里（比如钉钉版新闻资讯），
                # 门户上没有对应卡片，没法退回点击方式
                self.log("  新闻站接口没取到这个板块的文章")
                result.error = "site-empty"
                return result
            opened = await self.open_channel(card_name)
            if opened is None:
                self.log("  打不开板块列表页，跳过")
                result.error = "open-failed"
                return result
            await asyncio.sleep(5)
            articles = await self.collect_list_articles(stop_before=since)
        self.log(f"  列表里共 {len(articles)} 篇")
        articles = self.filter_by_date(articles)
        result.total = len(articles)
        if limit:
            articles = articles[:limit]

        # 接口模式：登录之后直接调新闻站接口，不开文章页面、不产生僵尸页面
        if self.cfg.api_mode:
            left = await self.process_articles_via_api(articles, result)
            if left is not None and not left:
                self.log(f"  本板块小结：{_fmt(result.stats)}")
                return result
            if left:
                self.log(f"  （剩下 {len(left)} 篇改用打开文章的方式）")
                articles = left

        total = len(articles)
        done: set = set()
        processed = 0
        idx = 0
        while idx < len(articles):
            if self._stop():
                self.log("  已手动停止")
                break
            item = articles[idx]
            idx += 1
            if item["title"] in done:
                continue

            # 有链接就直接用一个 webview 导航过去（不开窗口、不堆僵尸页）；
            # 拿不到链接才退回"点开文章窗口"的老办法
            has_url = bool(item.get("url"))
            status = (
                await self.process_article_url(item["url"])
                if has_url
                else await self.like_from_list(item["title"])
            )
            if status in _RETRYABLE:
                # 偶尔会遇到文章窗口开得慢 / 页面还没渲染好，重试一次
                await asyncio.sleep(2)
                if has_url:
                    retry = await self.process_article_url(item["url"])
                elif status == "no-list":
                    # 频道列表页被关掉了，重新打开再试
                    if await self.open_channel(card_name) is not None:
                        await asyncio.sleep(5)
                    retry = await self.like_from_list(item["title"])
                else:
                    retry = await self.like_from_list(item["title"])
                if retry not in _RETRYABLE:
                    status = retry
                else:
                    status = f"{retry}(重试仍失败)"
            result.stats[status] = result.stats.get(status, 0) + 1
            done.add(item["title"])
            processed += 1
            day = item.get("date")
            stamp = f"{day:%m-%d} " if day else "      "
            self.log(f"  [{processed}/{total}] {stamp}{status:<24} {item['title'][:30]}")

            # 客户端每开一篇文章就新建一个 webview 且不回收，跑多了会拖慢甚至超时。
            # 每处理 N 篇重启一次客户端把它清零，然后重新取列表接着往下做。
            n = self.cfg.restart_every_n_articles
            if n and processed % n == 0 and idx < len(articles) and not self._stop():
                if await self._recycle_client(card_name):
                    refreshed = self.filter_by_date(
                        await self.collect_list_articles(stop_before=since)
                    )
                    article_pool = [a for a in refreshed if a["title"] not in done]
                    if limit:
                        # 重新取列表后也要守住 --max 的上限
                        article_pool = article_pool[: max(0, limit - processed)]
                    articles = article_pool
                    total = processed + len(articles)
                    idx = 0
                    self.log(f"  重启后剩余 {len(articles)} 篇待处理")
                else:
                    self.log("  重启客户端没成功，继续用当前会话往下做")

        current = self.find_webview(self.cfg.list_host)
        if current is not None:
            await self._close_tab(self.cfg.list_host)
        self.log(f"  本板块小结：{_fmt(result.stats)}")
        return result

    async def run_session_section(self, name: str, limit: int = 0) -> SectionResult:
        result = SectionResult(name=name)
        self.log("")
        self.log("=" * 60)
        self.log(f"板块（会话）：{name}")
        articles: List[Dict[str, str]] = []
        for attempt in range(1, 4):
            if not await self.switch_to_messages():
                self.log(f"  第 {attempt} 次：切不到消息视图")
            elif not await self.open_session(name):
                self.log(f"  第 {attempt} 次：打不开会话")
            else:
                articles = await self.collect_session_articles()
                if articles:
                    break
                self.log(f"  第 {attempt} 次：没取到文章，重试 …")
            await asyncio.sleep(3)
        if not articles:
            self.log("  会话板块没取到文章，跳过")
            result.error = "session-empty"
            return result
        self.log(f"  会话里共找到 {len(articles)} 篇（消息里直接带 URL，不用点卡片）")
        articles = self.filter_by_date(articles)
        result.total = len(articles)
        if limit:
            articles = articles[:limit]

        if self.cfg.api_mode:
            left = await self.process_articles_via_api(articles, result)
            if left is not None and not left:
                self.log(f"  本板块小结：{_fmt(result.stats)}")
                return result
            if left:
                self.log(f"  （剩下 {len(left)} 篇改用打开文章的方式）")
                articles = left

        for i, item in enumerate(articles, 1):
            if self._stop():
                self.log("  已手动停止")
                break
            status = await self.process_article_url(item["url"])
            if status in _RETRYABLE:
                await asyncio.sleep(2)
                retry = await self.process_article_url(item["url"])
                status = retry if retry not in _RETRYABLE else f"{retry}(重试仍失败)"
            result.stats[status] = result.stats.get(status, 0) + 1
            day = item.get("date")
            stamp = f"{day:%m-%d} " if day else "      "
            self.log(f"  [{i}/{len(articles)}] {stamp}{status:<24} {item['title'][:30]}")
        self.log(f"  本板块小结：{_fmt(result.stats)}")
        return result

    async def run(self, channels: Optional[List[str]] = None, limit: int = 0) -> List[SectionResult]:
        """跑完选定的板块。channels=None 表示三个板块全跑。"""
        results: List[SectionResult] = []
        selected = set(channels) if channels else None

        if self.cfg.login_warmup:
            self.log("=" * 60)
            self.log("准备：登录预热（先从门户打开一篇文章，建立新闻模块登录态）")
            closed = await self.close_article_tabs()
            if closed:
                self.log(f"  先清理了 {closed} 个上次遗留的文章标签页")
            await self.warm_up_login()

        for card in self.cfg.portal_cards:
            if self._stop():
                break
            if selected and card not in selected:
                continue
            results.append(await self.run_section(card, limit))

        # 新版 TBe+（钉钉套壳）左侧"新闻资讯"栏 = 新闻站全部资讯
        dt_name = self.cfg.dingtalk_news_name
        if (
            self.cfg.dingtalk_news
            and not self._stop()
            and (not selected or dt_name in selected)
        ):
            results.append(await self.run_section(dt_name, limit, code=""))

        if self.cfg.session_channel and not self.cfg.skip_session_by_default:
            if not self._stop() and (not selected or self.cfg.session_channel in selected):
                results.append(await self.run_session_section(self.cfg.session_channel, limit))
        return results


# 状态名 → 中文标签（只用于显示）
_STATUS_LABEL = {
    "liked": "新点赞",
    "already-liked": "原本已点赞",
    "digg-sent": "已确认点赞",
    "like-unconfirmed": "点赞已受理待复查",
    "digg-unconfirmed": "点赞已受理待复查",
    "dry-run": "待点赞",
    "like-failed": "点赞失败",
    "read-failed": "读状态失败",
    "no-button": "找不到点赞按钮",
    "no-window": "文章窗口没开出来",
    "click-failed": "点击失败",
    "no-list": "列表页不在了",
    "no-change": "没看到状态变化",
    "no-webview": "没有可用页面",
    "need-login": "登录超时",
    "open-failed": "打不开列表页",
    "session-empty": "会话里没取到文章",
    "unknown": "未知结果",
}


def _bucket(key: str) -> str:
    """把一个状态名归到统计口径里。

    这里只认**原始状态名**，而且必须精确匹配：
    以前拿"以 liked 开头"当前缀去套，`liked(count=123)` 也会被算成新点赞，
    后来换成显示文字当 key，又反过来被算成"未成功"——口径自己先乱了两次。
    """
    base = key.split("(")[0].strip()
    if base == "liked":
        return "liked"          # 查过状态、确认是新点上的
    if base == "already-liked":
        return "already"        # 本来就赞过
    if base == "digg-sent":
        return "sent"           # 没查状态，只看点赞请求被服务端接受
    if base in ("like-unconfirmed", "digg-unconfirmed"):
        return "unclear"        # 点赞受理了，复查没看到
    if base == "dry-run":
        return "dry"
    return "failed"             # 没成 / 状态不明，都归到这里，不能藏


def _label(key: str) -> str:
    base = key.split("(")[0].strip()
    return _STATUS_LABEL.get(base, base)


def _totals(stats: Dict[str, int]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "liked": 0, "already": 0, "sent": 0, "unclear": 0, "dry": 0
    }
    failed: Dict[str, int] = {}
    for key, value in stats.items():
        where = _bucket(key)
        if where == "failed":
            # 按中文标签合并，不然 "no-change" 和 "no-change(重试仍失败)" 会各占一行
            label = _label(key)
            failed[label] = failed.get(label, 0) + value
        else:
            out[where] += value
    out["failed"] = sum(failed.values())
    out["failed_detail"] = failed
    return out


def _fmt(stats: Dict[str, int]) -> str:
    if not stats:
        return "（没有可处理的文章）"
    t = _totals(stats)
    parts = []
    if t["liked"]:
        parts.append(f"新点赞 {t['liked']}")
    if t["already"]:
        parts.append(f"原本已点赞 {t['already']}")
    if t["sent"]:
        parts.append(f"已确认点赞 {t['sent']}（没查历史状态，不区分是否本来已赞）")
    if t["unclear"]:
        parts.append(f"点赞已受理待复查 {t['unclear']}")
    if t["dry"]:
        parts.append(f"待点赞 {t['dry']}")
    if t["failed"]:
        detail = "，".join(f"{_label(k)} {v}" for k, v in t["failed_detail"].items())
        parts.append(f"未成功 {t['failed']}（{detail}）")
    return "，".join(parts) if parts else "（没有可处理的文章）"


def summarize(results: List[SectionResult]) -> str:
    merged: Dict[str, int] = {}
    for r in results:
        for key, value in r.stats.items():
            merged[key] = merged.get(key, 0) + value
    t = _totals(merged)

    head = []
    if t["liked"]:
        head.append(f"新点赞 {t['liked']} 篇")
    if t["already"]:
        head.append(f"原本已点赞 {t['already']} 篇")
    if t["sent"]:
        head.append(f"已确认点赞 {t['sent']} 篇（没查历史状态，分不清是否本来已赞）")
    if t["unclear"]:
        head.append(f"点赞已受理待复查 {t['unclear']} 篇")
    if t["dry"]:
        head.append(f"待点赞 {t['dry']} 篇")
    if t["failed"]:
        head.append(f"未成功 {t['failed']} 篇")

    lines = ["", "=" * 60, "全部板块合计"]
    lines.append("  " + ("，".join(head) if head else "没有可处理的文章"))
    lines.append("-" * 60)
    for r in results:
        if r.error:
            lines.append(f"  · {r.name}：处理失败（{r.error}）")
        else:
            processed = sum(r.stats.values())
            if processed and r.total and processed != r.total:
                # 用了 --max 之类只处理了一部分时，把"总共多少 / 这次处理多少"写清楚
                lines.append(
                    f"  · {r.name}：共 {r.total} 篇，本次处理 {processed} 篇"
                    f" → {_fmt(r.stats)}"
                )
            else:
                lines.append(f"  · {r.name}：共 {r.total} 篇 → {_fmt(r.stats)}")
    lines.append("=" * 60)
    if t["failed"]:
        lines.append(
            f"⚠ 有 {t['failed']} 篇没成功（明细见各板块那行），"
            "可以再跑一次，已经赞过的不会重复点。"
        )
    if any("need-login" in r.stats for r in results):
        lines.append("")
        lines.append("⚠ 文章页提示「登录超时，请重新登录」，说明 TBe+ 的新闻模块当前没登录。")
        lines.append("  请打开 TBe+ 客户端确认能正常看新闻，必要时重新登录，然后重跑本程序。")
    return "\n".join(lines)
