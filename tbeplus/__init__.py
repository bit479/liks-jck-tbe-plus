"""liks jck tbe+ —— TBe+ 新闻资讯自动点赞工具。

模块划分：
    config   配置（板块、端口、选择器、节奏）
    ws       极简 WebSocket 客户端（纯标准库，零依赖）
    cdp      极简 Chrome DevTools Protocol 客户端
    client   接管桌面客户端（带调试端口重启）
    engine   点赞引擎：遍历板块 → 找出未点赞 → 批量点击
    ui       图形界面
"""

__version__ = "1.0.0"
__all__ = ["config", "ws", "cdp", "client", "engine", "ui"]
