#!/usr/bin/env python
"""命令行入口（不想用界面时用这个）。

示例
----
python cli.py --check                 # 看看客户端接管了没
python cli.py --attach                # 带调试端口重启并接管客户端
python cli.py --list                  # 只列板块和篇数
python cli.py --dry-run --max 3       # 试运行：找出未点赞的，不点
python cli.py                         # 正式点赞三个板块
python cli.py --only 开讲了            # 只跑一个板块
python cli.py --no-session            # 跳过会话型板块
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tbeplus import client as client_mod  # noqa: E402
from tbeplus.config import load_config  # noqa: E402
from tbeplus.engine import LikeEngine, parse_user_date, summarize  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TBe+ 新闻资讯自动点赞", formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="只检查客户端接管状态")
    parser.add_argument("--attach", action="store_true", help="带调试端口重启并接管客户端")
    parser.add_argument(
        "--client-exe", metavar="路径", help="手动指定 TBe.exe（会记进 config.json）"
    )
    parser.add_argument("--list", action="store_true", help="只列出各板块篇数")
    parser.add_argument("--dry-run", action="store_true", help="只判断不点赞")
    parser.add_argument("--max", type=int, default=0, help="每个板块最多处理几篇（0=全部）")
    parser.add_argument("--only", action="append", default=[], help="只跑指定板块（可重复）")
    parser.add_argument("--no-session", action="store_true", help="跳过会话型板块")
    parser.add_argument(
        "--since", metavar="YYYY-MM-DD", help="只处理这天及之后的新闻"
    )
    parser.add_argument(
        "--until",
        metavar="YYYY-MM-DD",
        help="只处理这天及之前的新闻；不填表示一直到今天",
    )
    parser.add_argument(
        "--last-days", type=int, metavar="N", help="只处理最近 N 天的新闻（等价于 --since）"
    )
    parser.add_argument(
        "--restart-every",
        type=int,
        metavar="N",
        help="每处理 N 篇重启一次客户端，清理它累积的页面（0=不重启）",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="不用接口模式，改回逐个打开文章页面的老办法",
    )
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    cfg = load_config()
    if args.no_session:
        cfg.skip_session_by_default = True

    # 时间段
    since_text = args.since
    if args.last_days is not None and not since_text:
        from datetime import date, timedelta

        since_text = (date.today() - timedelta(days=args.last_days)).isoformat()
        print(f"按最近 {args.last_days} 天处理：{since_text} 起", flush=True)
    for label, value in (("--since", since_text), ("--until", args.until)):
        if value and parse_user_date(value) is None:
            print(f"{label} 的日期格式不对：{value!r}，请用 YYYY-MM-DD", flush=True)
            return 2
    if since_text:
        cfg.filter_since = since_text
    if args.until:
        cfg.filter_until = args.until
    if args.restart_every is not None:
        cfg.restart_every_n_articles = args.restart_every
    if args.no_api:
        cfg.api_mode = False

    def log(message: str) -> None:
        print(message, flush=True)

    if args.check:
        port = client_mod.resolve_debug_port(cfg.debug_port, log)
        if port:
            cfg.debug_port = port
            log(f"已接管（端口 {port}）")
            for kind, title in client_mod.summarize_targets(port):
                log(f"  {kind:9} {title[:60]}")
            return 0
        log("没有调试接口。先跑：python cli.py --attach")
        exe = client_mod.resolve_client_exe(cfg.client_exe_candidates, lambda _: None)
        log(f"TBe.exe：{exe}" if exe else "TBe.exe：没找到（可用 --client-exe 手动指定）")
        return 1

    if args.client_exe:
        exe = Path(args.client_exe)
        if not exe.is_file():
            log(f"指定的文件不存在：{exe}")
            return 1
        if client_mod.save_client_exe(exe, cfg.client_exe_candidates):
            log(f"已记到 config.json：{exe}")
        cfg.client_exe_candidates = [str(exe)] + list(cfg.client_exe_candidates)

    if args.attach:
        preferred = cfg.debug_port
        found = client_mod.resolve_debug_port(preferred, log)
        if found:
            cfg.debug_port = found
            if found != preferred:
                client_mod.save_debug_port(found)
            log(f"已经处于可接管状态（端口 {found}）")
            return 0
        exe = client_mod.resolve_client_exe(cfg.client_exe_candidates, log)
        if exe is None:
            log("")
            log("自动没找到 TBe.exe。手动指定一次即可，例如：")
            log('  python cli.py --client-exe "D:\\...\\tbea\\2305251534\\TBe.exe"')
            log("指定过的路径会记进 config.json，以后不用再填。")
            return 2
        used = client_mod.restart_client(exe, preferred, cfg.restart_wait_seconds, log)
        if not used:
            return 2
        cfg.debug_port = used
        if used != preferred:
            client_mod.save_debug_port(used)
            log(f"已经把端口 {used} 记到 config.json，以后会自动用它。")
        return 0

    port = client_mod.resolve_debug_port(cfg.debug_port)
    if port is None:
        log(
            f"端口 {cfg.debug_port} 往后都没找到调试接口，请先跑：python cli.py --attach"
        )
        return 1
    cfg.debug_port = port

    engine = LikeEngine(cfg, dry_run=args.dry_run, log=log)

    if args.list:
        def describe(articles):
            days = sorted(a["date"] for a in articles if a.get("date"))
            kept = len(engine.filter_by_date(articles))
            span = f"{days[0]} ~ {days[-1]}" if days else "日期未知"
            tail = f"，时间段内 {kept} 篇" if (cfg.filter_since or cfg.filter_until) else ""
            return f"{len(articles)} 篇（{span}{tail}）"

        for card in cfg.portal_cards:
            opened = await engine.open_channel(card)
            if opened is None:
                log(f"{card}: 打开失败")
                continue
            await asyncio.sleep(7)
            articles = await engine.collect_list_articles()
            log(f"{card}: {describe(articles)}")
        if cfg.session_channel and not cfg.skip_session_by_default:
            if await engine.switch_to_messages() and await engine.open_session(
                cfg.session_channel
            ):
                articles = await engine.collect_session_articles()
                log(f"{cfg.session_channel}: {describe(articles)}")
        return 0

    results = await engine.run(channels=args.only or None, limit=args.max)
    log("")
    log(summarize(results))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
