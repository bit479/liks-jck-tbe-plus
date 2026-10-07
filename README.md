# liks jck tbe+

一个自动给 TBe+ 新闻资讯点赞的小工具。

自动遍历 **【Tbe+新闻资讯】**、**【进出口公司新闻资讯】**、**【开讲了】** 三个板块，
把每一篇还没点赞的文章都点上；已经点过的会自动跳过，不会重复点。

包装成了一个双击就能跑的小程序（图形界面），不用记命令，**也不用装任何第三方库**。

---

## 快速开始

1. 装好 Python 3.10 以上版本（安装时**记得勾选 "Add Python to PATH"**）
2. 双击 **`启动.bat`**
3. 在窗口里依次点三个按钮：

   | 按钮 | 作用 |
   | --- | --- |
   | **① 准备：重启并接管 TBe+** | 关闭并带调试端口重启 TBe+ 客户端（登录态保留） |
   | **② 试运行（只看不点）** | 只扫描，列出哪些文章没点赞，**不点** |
   | **③ 开始点赞** | 正式执行 |

**环境要求：只有一个——电脑上装真正的 Python 3.10+。**
不用 pip、不用联网装依赖（详见下面「为什么零依赖」）。

> **没装 Python 或装成了微软商店版？**
> `启动.bat` 会直接告诉你，并给出官方下载地址。
> 注意微软商店那个 Python 是跑不了脚本的，一定要从
> [python.org](https://www.python.org/downloads/) 下载官方安装包，
> 安装时勾选 **Add python.exe to PATH**。

> **为什么要点「① 准备」？**
> TBe+ 没有网页版登录入口，新闻站还有 WAF 只放行客户端内部的请求，
> 所以只能"接管"桌面客户端。这一步会给客户端加上调试端口并重启它，
> 之后程序就能像操作浏览器一样操作它——**不需要账号密码，也不用过验证码**。
> 代价是会中断当前打开的对话窗口（登录态保留）。

---

## 命令行用法

不想用界面就双击 `命令行.bat`，或者直接跑：

```bash
python cli.py --check          # 看看客户端接管了没
python cli.py --attach         # 重启并接管客户端
python cli.py --list           # 只列板块和篇数
python cli.py --dry-run        # 试运行，只看不点
python cli.py                  # 正式点赞三个板块
python cli.py --only 开讲了     # 只跑一个板块
python cli.py --max 5          # 每个板块最多处理 5 篇
python cli.py --no-session     # 跳过【进出口公司新闻资讯】
```

---

## 它到底是怎么做到的

### 为什么不用 Playwright

一开始用 Playwright，但 TBe+ 桌面端是 2023 年版本的老 Electron 内核，
`connect_over_cdp` 一连上就报：

```
Protocol error (Browser.setDownloadBehavior): Browser context management is not supported
```

所以改成了自己写一个极简的 CDP 客户端（`tbeplus/cdp.py`，200 行不到），
只用 `Runtime` / `Input` / `Page` 这几个最基础的域——老内核全都支持。

### 为什么零依赖

CDP 需要一条 WebSocket 连接。一开始用了第三方库 `websockets`，
结果用户那边 `启动.bat` 经常卡在
`[错误] 依赖安装失败，请检查网络后重试`——因为 Windows 上
`where python` 很容易命中**微软商店的占位符**，那个东西根本没装 Python，
后面 `import websockets`、`pip install` 自然全失败。

所以干脆用标准库自己实现了 WebSocket 客户端（`tbeplus/ws.py`，
握手 / 掩码 / 分片 / ping-pong / 关闭帧都覆盖了），
**从此一个第三方包都不需要**：不用 pip、不用联网、不会再有依赖问题。

这套实现有自带的自检：

```bash
python tests/test_websocket.py
# [PASS] WebSocket 实现全部通过（握手 / 掩码 / 大消息 / 分片 / ping-pong / 事件）
```

### 实测出来的页面结构

```
门户页 miniapp://portal/#/
└── 卡片 .card-title = "新闻资讯" / "开讲了"
    └── 卡片里的 .card-header-button（"更多"）
        └── 频道文章列表页  https://<内容站>/pubacc-front/article/list?contentSource=[...]
            └── 文章条目 .article-item > .article-title
                └── 点标题 → 文章窗口里开一个新标签页
                     https://<新闻站>/pc/show?id=...&f=...&yzj_id=...
                     └── 点赞按钮  ★关键★
                           未点赞  <span class="praise-num">10476</span>
                           已点赞  <span class="praise-active">10477</span>
```

几个踩过的坑，都写在代码注释里了：

- **列表页本身没有点赞按钮**，点赞在文章详情页里，所以必须逐篇打开。
- **文章是开在同一个窗口的多个标签页里的**，关闭时要关标签（`em.tab-close`），
  关掉全部标签会让窗口消失。
- **`Target.closeTarget` 和 `window.close()` 都关不掉这个窗口**。
- **会话型板块里的文章卡片点不开**（`<a>` 高度甚至是 0），
  换成读取消息组件数据里自带的文章 URL，再直接导航过去。
- **点赞后状态刷新有延迟**，判"没点上"之后再等一轮确认。

### 【进出口公司新闻资讯】的特殊处理

这个板块不在门户卡片里，而是左侧消息列表里的订阅号会话。
它的文章卡片用合成事件点不开，但消息背后的 Vue 组件
（`name: NewsItem`）的 `$props.msg.param.list` 里**直接带着每篇文章的完整地址**：

```json
{
  "title": "坦桑尼亚能源部部长视察特变电工东北电网一期Morogoro 220千伏变电项目",
  "url": "https://<新闻站>/pc/show?id=13987&f=HLvsJiPj&yzj_id=0d41f4fe-..."
}
```

所以流程是：滚动会话加载历史 → 抽出所有 `{title, url}` → 用客户端里一个
现成的 webview 挨个导航过去 → 读状态、按需点赞。比点卡片稳得多，也不会开一堆窗口。

---

## 配置

所有可调项都在 `tbeplus/config.py` 的 `DEFAULTS` 里。
想改的话，把 `config.example.json` 复制成 `config.json` 再改，
**`config.json` 不会被提交到 git**，方便各自放自己环境的东西。

常用的几项：

| 配置 | 说明 |
| --- | --- |
| `debug_port` | 调试端口，默认 9222 |
| `client_exe_candidates` | TBe.exe 可能的位置，找到哪个用哪个 |
| `portal_cards` | 门户首页上的新闻卡片名 |
| `session_channel` | 会话型板块名 |
| `skip_session_by_default` | 是否默认跳过会话型板块 |
| `praise_selector` | 未点赞按钮的选择器 |
| `praise_active_selector` | 已点赞按钮的选择器 |
| `wait_after_click` / `confirm_wait` | 点赞后的等待时间 |

---

## 常见问题

**双击 `启动.bat` 提示"没有找到可用的 Python"**

电脑上没装 Python，或者装的是微软商店版。请到
[python.org](https://www.python.org/downloads/) 下载官方安装包，
安装时勾选 **Add python.exe to PATH**。
（老版本会报 `依赖安装失败，请检查网络后重试`，那是把
"找不到 Python"误报成了网络问题，现在已修好。）

**提示"文章页显示登录超时"**

说明 TBe+ 客户端里新闻模块的会话过期了。先在客户端里手动点开
一篇新闻，确认能正常显示；如果客户端自己也打不开，请在客户端里
重新登录一次，然后重跑本程序。

**提示"没检测到 TBe+ 在运行"**
先把 TBe+ 客户端打开，再点「① 准备」。

**点「① 准备」之后一直等不到端口**
可能客户端装了但路径不一样。找到 `TBe.exe`，把完整路径填进
`config.json` 的 `client_exe_candidates`。

**某个板块打不开 / 篇数是 0**
多半是版面改过了。用 `python cli.py --list` 看看，再对照
`config.example.json` 里的选择器调整。

**点了半天没反应**
确认 TBe+ 客户端是"接管后"启动的（界面上状态应该显示
`● 已接管`）。普通启动的客户端没有调试接口。

**会不会把已经点过的又点一遍？**
不会。程序先读状态，`praise-active` 的会直接跳过。重复运行是安全的。

---

## 目录结构

```
liks-jck-tbe-plus/
├── 启动.bat              双击启动图形界面
├── 命令行.bat            打开命令行
├── _python.bat           查找可用的 Python（启动脚本共用）
├── app.py                图形界面入口
├── cli.py                命令行入口
├── requirements.txt      说明"无第三方依赖"
├── config.example.json   配置示例（复制成 config.json 生效）
├── tbeplus/
│   ├── config.py         配置
│   ├── ws.py             极简 WebSocket 客户端（标准库实现）
│   ├── cdp.py            极简 CDP 客户端
│   ├── client.py         接管客户端（带调试端口重启）
│   ├── engine.py         点赞引擎
│   └── ui.py             图形界面
├── tests/
│   └── test_websocket.py WebSocket 实现自检
└── LICENSE
```

---

## 使用须知

- 本工具操作的是公司内部系统，**请在公司规定允许的范围内使用**，
  只对自己有权操作的账号和内容使用。
- 建议把仓库设为 **Private**：代码和文档里包含内网域名、客户端路径
  等信息，不适合公开。
- 点赞属于对系统的写操作。建议先跑「② 试运行」确认无误再正式执行。
