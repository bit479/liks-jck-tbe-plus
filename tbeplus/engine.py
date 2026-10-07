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
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .cdp import CDPSession, CDPError, list_targets
from .config import Config

LogFn = Callable[[str], None]


@dataclass
class SectionResult:
    name: str
    total: int = 0
    stats: Dict[str, int] = field(default_factory=dict)
    error: str = ""

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

    async def _eval(self, target: Dict[str, Any], js: str) -> Any:
        async with CDPSession(target["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout) as s:
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
                       vw: window.innerWidth, vh: window.innerHeight };
            })()"""
        )
        for attempt in range(3):
            try:
                async with CDPSession(
                    target["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout
                ) as s:
                    await s.send("Runtime.enable")
                    try:
                        # 把它拉回前台，否则后台标签会被限流、坐标算不准
                        await s.send("Page.bringToFront")
                    except CDPError:
                        pass
                    if not await s.evaluate(mark_js):
                        return False
                    await asyncio.sleep(0.6 + attempt * 0.4)
                    info = await s.evaluate(rect_js)
                    if not info or not info.get("found"):
                        return False
                    if 0 <= info["x"] <= info["vw"] and 0 <= info["y"] <= info["vh"]:
                        await s.click_point(info["x"], info["y"])
                        return True
            except CDPError:
                pass
            await asyncio.sleep(0.8)
        return False

    # ------------------------------------------------------------------
    # 点赞状态
    # ------------------------------------------------------------------
    def _read_praise_js(self) -> str:
        return (
            "(() => {\n"
            f"  const active = document.querySelector({json.dumps(self.cfg.praise_active_selector)});\n"
            f"  const el = active || document.querySelector({json.dumps(self.cfg.praise_selector)});\n"
            "  if (!el) return { found: false };\n"
            "  el.scrollIntoView({ block: 'center' });\n"
            "  const r = el.getBoundingClientRect();\n"
            "  return { found: true, liked: !!active, count: el.innerText.trim(),\n"
            "           x: r.left + r.width / 2, y: r.top + r.height / 2 };\n"
            "})()"
        )

    async def _close_tab(self, marker: str) -> bool:
        """内容页是开在一个窗口里的标签页，关标签而不是关窗口。"""
        host = next(
            (t for t in self.targets() if "browser.html" in (t.get("url") or "")), None
        )
        if host is None:
            return False
        js = (
            r"""((marker) => {
              const bar = document.querySelector('.tab-bar');
              if (!bar) return false;
              for (const tab of bar.querySelectorAll('.tab-header')) {
                const span = tab.querySelector('span.title');
                const title = span ? (span.getAttribute('title') || '') : '';
                if (title.includes(marker)) {
                  const closer = tab.querySelector('em.tab-close');
                  if (closer) { closer.click(); return true; }
                }
              }
              return false;
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

    async def collect_list_titles(self, timeout: float = 40.0) -> List[str]:
        """列表页是 SPA，要等它渲染；期间目标可能重建，所以按 URL 反复找。"""
        js = (
            "(() => Array.from(document.querySelectorAll("
            + json.dumps(self.cfg.article_title_selector)
            + ")).map((el) => el.innerText.trim()).filter(Boolean))()"
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            target = self.find_webview(self.cfg.list_host)
            if target is not None:
                try:
                    titles = await self._eval(target, js)
                    if titles:
                        return titles
                except CDPError:
                    pass
            await asyncio.sleep(2.0)
        return []

    async def like_from_list(self, title: str) -> str:
        """从频道列表页点开文章并点赞。"""
        list_target = self.find_webview(self.cfg.list_host)
        if list_target is None:
            return "no-list"
        before = {t.get("id") for t in self.targets()}
        if not await self.click_text(list_target, title):
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
            return await self._like_article_target(marker)
        finally:
            await self._close_tab("id=" + article_id)

    # ------------------------------------------------------------------
    # 会话型板块
    # ------------------------------------------------------------------
    async def switch_to_messages(self) -> bool:
        main = self.find_target("dashboard.html")
        if main is None:
            return False
        if "#/chat" in (main.get("url") or ""):
            return True
        js = (
            "(() => { const el = document.querySelector('.navi-left a[title=\"消息\"]')"
            " || document.querySelector('a[title=\"消息\"]');"
            " if (!el) return false; el.click(); return true; })()"
        )
        try:
            ok = await self._eval(main, js)
        except CDPError:
            return False
        await asyncio.sleep(3)
        return bool(ok)

    async def open_session(self, name: str) -> bool:
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
        try:
            async with CDPSession(
                main["webSocketDebuggerUrl"], timeout=self.cfg.page_timeout
            ) as s:
                await s.send("Runtime.enable")
                info = await s.evaluate(js)
                if not info or not info.get("found"):
                    return False
                await s.click_point(info["x"], info["y"])
        except CDPError:
            return False
        await asyncio.sleep(6)
        return True

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
                if (Array.isArray(list)) {
                  list.forEach((a) => {
                    if (a && a.url) out.push({ title: a.title || a.text || '', url: a.url });
                  });
                }
              });
              return out;
            })()"""
        )
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        found: Dict[str, str] = {}
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
                    found[url] = item.get("title") or ""
            if len(found) > before:
                stable = 0
            else:
                stable += 1
                if stable >= 3:
                    break
            await asyncio.sleep(self.cfg.poll_interval)
        return [{"title": t, "url": u} for u, t in found.items()]

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
        self, marker: str, max_wait: Optional[float] = None
    ) -> str:
        timeout = max_wait or self.cfg.page_timeout
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline and not self._stop():
            current = self.find_webview(marker)
            if current is not None:
                try:
                    async with CDPSession(
                        current["webSocketDebuggerUrl"], timeout=timeout
                    ) as s:
                        await s.send("Runtime.enable")
                        try:
                            # 文章页也可能是后台标签，拉回前台再读，避免被限流
                            await s.send("Page.bringToFront")
                        except CDPError:
                            pass
                        state = await s.evaluate(self._read_praise_js())
                        if state and state.get("found"):
                            if state["liked"]:
                                return "already-liked"
                            if self.dry_run:
                                return f"dry-run(count={state['count']})"
                            await s.click_point(state["x"], state["y"])
                            await asyncio.sleep(self.cfg.wait_after_click)
                            after = await s.evaluate(self._read_praise_js())
                            if after and (
                                after.get("liked") or after.get("count") != state["count"]
                            ):
                                return f"liked({state['count']}->{after.get('count')})"
                            # 状态刷新有延迟，再确认一轮
                            await asyncio.sleep(self.cfg.confirm_wait)
                            again = await s.evaluate(self._read_praise_js())
                            if again and (
                                again.get("liked") or again.get("count") != state["count"]
                            ):
                                return f"liked({state['count']}->{again.get('count')})"
                            return "no-change"
                except CDPError:
                    pass
            await asyncio.sleep(self.cfg.poll_interval)
        return "no-button"

    # ------------------------------------------------------------------
    # 总入口
    # ------------------------------------------------------------------
    async def run_section(self, card_name: str, limit: int = 0) -> SectionResult:
        result = SectionResult(name=card_name)
        self.log("")
        self.log("=" * 60)
        self.log(f"板块：{card_name}")
        opened = await self.open_channel(card_name)
        if opened is None:
            self.log("  打不开板块列表页，跳过")
            result.error = "open-failed"
            return result

        await asyncio.sleep(7)
        titles = await self.collect_list_titles()
        self.log(f"  列表里共 {len(titles)} 篇")
        result.total = len(titles)
        if limit:
            titles = titles[:limit]

        for i, title in enumerate(titles, 1):
            if self._stop():
                self.log("  已手动停止")
                break
            status = await self.like_from_list(title)
            result.stats[status] = result.stats.get(status, 0) + 1
            self.log(f"  [{i}/{len(titles)}] {status:<24} {title[:32]}")

        current = self.find_webview(self.cfg.list_host)
        if current is not None:
            await self._close_tab(self.cfg.list_host)
        self.log(f"  小结：{_fmt(result.stats)}")
        return result

    async def run_session_section(self, name: str, limit: int = 0) -> SectionResult:
        result = SectionResult(name=name)
        self.log("")
        self.log("=" * 60)
        self.log(f"板块（会话）：{name}")
        if not await self.switch_to_messages():
            self.log("  切不到消息视图，跳过")
            result.error = "switch-failed"
            return result
        if not await self.open_session(name):
            self.log("  打不开会话，跳过")
            result.error = "session-not-found"
            return result

        articles = await self.collect_session_articles()
        self.log(f"  会话里共找到 {len(articles)} 篇（消息里直接带 URL，不用点卡片）")
        result.total = len(articles)
        if limit:
            articles = articles[:limit]

        for i, item in enumerate(articles, 1):
            if self._stop():
                self.log("  已手动停止")
                break
            status = await self.process_article_url(item["url"])
            result.stats[status] = result.stats.get(status, 0) + 1
            self.log(f"  [{i}/{len(articles)}] {status:<24} {item['title'][:32]}")
        self.log(f"  小结：{_fmt(result.stats)}")
        return result

    async def run(self, channels: Optional[List[str]] = None, limit: int = 0) -> List[SectionResult]:
        """跑完选定的板块。channels=None 表示三个板块全跑。"""
        results: List[SectionResult] = []
        selected = set(channels) if channels else None

        for card in self.cfg.portal_cards:
            if self._stop():
                break
            if selected and card not in selected:
                continue
            results.append(await self.run_section(card, limit))

        if self.cfg.session_channel and not self.cfg.skip_session_by_default:
            if not self._stop() and (not selected or self.cfg.session_channel in selected):
                results.append(await self.run_session_section(self.cfg.session_channel, limit))
        return results


def _fmt(stats: Dict[str, int]) -> str:
    if not stats:
        return "（没有可处理的文章）"
    parts = []
    liked = sum(v for k, v in stats.items() if k.startswith("liked"))
    if liked:
        parts.append(f"新点赞 {liked}")
    if stats.get("already-liked"):
        parts.append(f"已点赞 {stats['already-liked']}")
    other = {k: v for k, v in stats.items() if not k.startswith("liked") and k != "already-liked"}
    for k, v in other.items():
        parts.append(f"{k} {v}")
    return "，".join(parts)


def summarize(results: List[SectionResult]) -> str:
    total_liked = sum(r.liked for r in results)
    total_already = sum(r.already for r in results)
    lines = [f"全部完成：新点赞 {total_liked} 篇，原本已点赞 {total_already} 篇"]
    for r in results:
        if r.error:
            lines.append(f"  · {r.name}：失败（{r.error}）")
        else:
            lines.append(f"  · {r.name}：{r.total} 篇 → {_fmt(r.stats)}")
    return "\n".join(lines)
