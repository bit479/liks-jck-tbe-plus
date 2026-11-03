# PySide6 迁移可行性分析

## 1. 目标与硬约束

**目标**：建立抗锯齿、高 DPI、矢量图标、模块化的现代桌面 UI；先首页，后其余页面。

**硬约束**：

- 不删除、不重写 `engine.py` / `client.py` / `newsapi.py` / `config.py`。
- 不改变自动点赞流程、资讯读取、客户端接管、配置格式、统计口径、日志来源。
- 旧 Tkinter 界面保留，可独立启动；**不得覆盖现有正式入口**（`app.py` → `tbeplus.ui.run_ui`）。
- 新 UI 必须以独立入口启动，便于 A/B 对比与回退。

## 2. 已完成的可行性证据（独立原型）

在此前工作中已建立一个**与正式入口完全隔离**的 PySide6 原型：

| 产物 | 说明 |
| --- | --- |
| `tbeplus/qtui/theme.py` | 深色令牌 + `@token@` 占位 QSS（避免 f-string 花括号转义） |
| `tbeplus/qtui/shell.py` | 无边框窗口、自绘标题栏拖拽、`startSystemResize` 原生边缘缩放、DWM 圆角、侧栏导航、QStackedWidget |
| `tbeplus/qtui/pages.py` | 概览 / 日志 / 设置 / 连接四页静态骨架 |
| `app_qt.py` | 独立入口，不修改 `app.py` |
| `_shot_qt.py` | `QWidget.grab()` 截图脚本 |

运行环境：隔离 venv `C:\Users\Administrator\.workbuddy\binaries\python\envs\tbeplus-qt`，安装 **PySide6-Essentials 6.12.0**（Qt 6.12.0）。已验证：

- 导入自检通过（Qt 6.12.0，QSS 5020 字符）。
- 4 个页面在 1280×800 下成功渲染（`docs/ui-redesign/_preview/01~04-*.png`），无切字、无重叠。
- 无边框拖动、最小化/最大化/关闭、页面切换可用。

**结论：PySide6 在本机、本 Python 3.13 上可运行，技术前提成立。** 该原型尚未接入真实业务，仅证明渲染与外壳可行。

## 3. 能力对比（针对本项目实际痛点）

| 能力 | 当前 Tkinter | PySide6 | 对本项目的意义 |
| --- | --- | --- | --- |
| 抗锯齿 | Canvas 无 AA，折线硬边 | QPainter 默认可开 Antialiasing / TextAntialiasing，QPainterPath 圆角 | 直接消除卡片/按钮/胶囊边缘毛刺 |
| 圆角与描边 | 多边形模拟，1px 环填充 | `QPainterPath.addRoundedRect` + 亚像素描边 | 1px 边框干净，圆角平滑 |
| 高 DPI | System-DPI-Aware，无 PMv2 | Qt 6 逻辑像素 + Per-Monitor DPI + 缩放变化事件 | 多屏 125%/150%/200% 一致 |
| 图标 | MDL2 字体码位 + 固定 PNG | SVG（QSvgRenderer/QIcon）任意缩放，线宽统一 | 100%–200% 清晰一致 |
| 控件状态 | 自绘，需逐控件补 | QSS + 伪状态（hover/pressed/disabled/focus） | 状态矩阵统一，减少重复代码 |
| 弹窗/菜单 | `messagebox`/`filedialog` 原生，风格割裂 | QDialog/QMenu/QToolTip 可统一深色 | 确认框与主界面一致 |
| 日志大列表 | Text + tag，手工裁剪 | QAbstractTableModel + View 虚拟化 | 长任务更稳，筛选/排序更清晰 |
| 键盘/焦点 | 仅 Btn 支持基本键盘 | 标准控件与焦点链、accessibleName | 可用性与可测试性提升 |
| 打包 | PyInstaller 单文件，已成熟 | 需收集 Qt 平台插件/DLL，包体更大 | 成本最高的不确定性 |

## 4. 复用矩阵

### 原样复用（零改动）
- `engine.py`（点赞/枚举/限流/重试/统计）
- `client.py`、`dingtalk.py`、`newsapi.py`、`winproc.py`
- `config.py`（`config.json` 格式、`APP_DIR`）
- `ws.py`、`cdp.py`、`tests/test_websocket.py`
- `cli.py`（GUI 之外的回退通道）

### 轻量适配
- 日志：`log(str)` 回调 → 适配为 `LogRecord` / `RunEvent`（首版可在适配器内保留现有正则解析）。
- 运行参数：新增 `RunRequest` 快照，worker 不再读 UI 控件。
- 状态刷新、token 校验：改为后台执行 + 信号回主线程。
- 配置写回：继续调用 `save_debug_port()` 等既有函数。

### 重写
- `ui.py`、`widgets.py`、`theme.py`、`motion.py`。
- 标题栏/无边框窗口/边缘缩放。
- 图标资产：MDL2 → SVG；PNG → SVG 或 @2x。
- 首页及后续页面布局。

### 保留不动
- `ui_classic.py`（历史 ttk 备份，非正式入口）。
- `app.py`（正式启动入口，切换前不改）。

## 5. 成本与风险

| 项 | 评估 | 缓解 |
| --- | --- | --- |
| 依赖体积 | `PySide6-Essentials` wheel 约 82MB，安装后更大；完整 `PySide6` 还会带 131MB WebEngine/PDF（本项目用不到） | 只依赖 `PySide6-Essentials`；打包时排除 QtWebEngine/QtPdf |
| 打包复杂度 | 需收集 `platforms/qwindows.dll`、styles、imageformats、SVG 插件 | 独立实验 spec，先在干净 Windows 验证 |
| 启动时间 | Qt 首次加载较 Tk 慢 | 实测后决定 onefile/onedir；必要时保留启动画面 |
| 许可 | PySide6/Qt 为 LGPLv3/GPLv3/商业许可组合 | 分发前做许可评审，随包附声明 |
| 无边框窗口 | 需保证 snap、跨屏、任务栏行为 | 优先原生标题栏或验证过的 DWM 方案，不叠补丁 |
| 字体 | 中文用微软雅黑，英文/数字需回退 | 明确字体回退链，不默认捆绑未授权字体 |
| 线程模型 | 迁移时容易把 asyncio 与 Qt 事件循环混用 | 业务仍在独立线程跑 asyncio；只通过 Signal 回主线程 |
| 双入口维护 | 迁移期两套 UI 并存 | 明确 `app.py`（Tk，正式）/`app_qt.py`（Qt，实验），切换需人工批准 |

## 6. 迁移策略：并行新壳 + 逐页替换

```text
app.py  ──→ tbeplus.ui（Tk，保持可用，正式入口）
                 │
                 ├─ 共享 RunRequest / RunEvent / Controllers
                 │
app_qt.py ──→ tbeplus/ui（PySide6，新壳）
                 ├─ main_window.py
                 ├─ components/（sidebar/topbar/stat_card/task_panel/...）
                 └─ resources/icons（SVG）
```

- 不在同一顶层窗口混嵌 Tk 与 Qt。
- 不一次性迁移全部页面：先首页，验收后再迁移日志/设置/连接。
- 切换正式入口必须单独提交、单独验证、可一键回退。
- 迁移期间 `engine/client/newsapi/config` 保持零改动；任何必要适配都放在 `ui/adapters/`。

## 7. 建议阶段与验收门

| 阶段 | 内容 | 验收门 |
| --- | --- | --- |
| P0 审查 | 本文档 + 渲染审查 + 解耦方案 | 人工确认路线与约束 |
| P1 技术验证 | 首页静态原型：AA、SVG、DPI、无边框、组件状态 | 100/125/150/200% 截图与控件状态矩阵通过 |
| P2 契约 | RunRequest / RunEvent / LogRecord / Controllers | 用录制日志驱动，统计与旧版一致 |
| P3 首页接入 | 真实统计、五阶段、日志、连接状态、四个操作 | 与旧版首页逐项对照，无功能缺失 |
| P4 其余页面 | 日志 / 设置 / 配置 / 版本 / 偏好 / 连接 | 每页独立验收，可单独回退 |
| P5 回归 | 协议测试、fake 全流程、双入口冒烟 | 旧版不退化；新版无阻塞/无假数据 |
| P6 打包 | 实验 spec、干净 Windows、许可声明 | 人工批准后才切换正式入口 |

## 8. 可行性结论

**可行，且推荐。** 依据：

1. 原型已在本机跑通 Qt 6.12，四页渲染正常。
2. 业务层无 Qt/Tk 依赖，迁移不需要重写自动化逻辑。
3. 当前痛点是 AA、DPI、图标、控件状态——正好是 Qt 的强项，而 Tk 的结构性短板。

**前置条件（必须人工确认）**：

- 接受 `PySide6-Essentials` 作为唯一新增 GUI 依赖。
- 完成 PySide6/Qt 许可与分发方式评审。
- 接受迁移期双入口并存，正式入口切换需单独批准。

## 9. 待确认问题

1. 正式发布是否允许包体增大（Qt 运行时）？
2. 是否接受 onefile（启动慢、解包）或改为 onedir（启动快、目录多）？
3. 图标是否统一采用自绘 SVG 集，还是引入第三方开源图标集（需记录许可）？
4. 是否需要保留 `ui_classic.py` 作为第二回退，还是仅保留 Git 标签/旧构建产物？
5. 正式切换的时间点：首页验收后，还是全部页面验收后？
