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
from tbeplus.engine import LikeEngine, summarize  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TBe+ 新闻资讯自动点赞", formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--check", action="store_true", help="只检查客户端接管状态")
    parser.add_argument("--attach", action="store_true", help="带调试端口重启并接管客户端")
    parser.add_argument("--list", action="store_true", help="只列出各板块篇数")
    parser.add_argument("--dry-run", action="store_true", help="只判断不点赞")
    parser.add_argument("--max", type=int, default=0, help="每个板块最多处理几篇（0=全部）")
    parser.add_argument("--only", action="append", default=[], help="只跑指定板块（可重复）")
    parser.add_argument("--no-session", action="store_true", help="跳过会话型板块")
    return parser.parse_args()


async def main() -> int:
    args = parse_args()
    cfg = load_config()
    if args.no_session:
        cfg.skip_session_by_default = True

    def log(message: str) -> None:
        print(message, flush=True)

    if args.check:
        if client_mod.is_attached(cfg.debug_port):
            log(f"已接管（端口 {cfg.debug_port}）")
            for kind, title in client_mod.summarize_targets(cfg.debug_port):
                log(f"  {kind:9} {title[:60]}")
            return 0
        log("没有调试接口。先跑：python cli.py --attach")
        return 1

    if args.attach:
        ok = client_mod.attach(
            cfg.client_exe_candidates,
            cfg.debug_port,
            restart=True,
            timeout=cfg.restart_wait_seconds,
            log=log,
        )
        return 0 if ok else 2

    if not client_mod.is_attached(cfg.debug_port):
        log(f"端口 {cfg.debug_port} 上没有调试接口，请先跑：python cli.py --attach")
        return 1

    engine = LikeEngine(cfg, dry_run=args.dry_run, log=log)

    if args.list:
        for card in cfg.portal_cards:
            opened = await engine.open_channel(card)
            if opened is None:
                log(f"{card}: 打开失败")
                continue
            await asyncio.sleep(7)
            titles = await engine.collect_list_titles()
            log(f"{card}: {len(titles)} 篇")
        if cfg.session_channel and not cfg.skip_session_by_default:
            if await engine.switch_to_messages() and await engine.open_session(
                cfg.session_channel
            ):
                articles = await engine.collect_session_articles()
                log(f"{cfg.session_channel}: {len(articles)} 篇")
        return 0

    results = await engine.run(channels=args.only or None, limit=args.max)
    log("")
    log(summarize(results))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
