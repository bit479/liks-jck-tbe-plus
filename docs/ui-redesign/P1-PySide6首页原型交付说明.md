# P1 交付说明：PySide6 渲染体系 + 首页原型

> 阶段：迁移第二步（建立独立原型）、第三步（首页组件）、第四阶段（首页原型）与第五阶段（兼容性测试）
> 状态：**未接业务引擎**；旧 Tkinter 入口与业务文件零改动
> 日期：2026-11-03

---

## 1. 本轮结论

| 目标 | 结果 |
| --- | --- |
| 圆角/边框抗锯齿 | 达成：全部自绘走 `QPainter` + `QPainterPath`，1px 边框落在半像素，200% 下无台阶 |
| 高 DPI | 达成：`PassThrough` 缩放策略 + 逻辑像素布局，实测 100/125/150/200% 四组无重叠、无裁切 |
| 矢量图标 | 达成：27 个 SVG（24×24，stroke 1.6），`QSvgRenderer` 直接渲染，无位图、无 Emoji |
| 控件五态 | 达成：normal / hover（140ms 补间）/ pressed / disabled / focus 环，统一由 `theme` 取色 |
| 首页原型 | 达成：侧栏 + 顶栏 + 统计四卡 + 执行面板 + 阶段条 + 事件列表 + 连接状态 |
| 新旧并行 | 达成：`app.py`（Tkinter）与 `app_qt.py`（Qt）互不引用，可同时启动对比 |
| 业务解耦 | 达成：Qt 侧只依赖 `HomeViewModel`，不 import `engine/client/newsapi/config` |

**未达成（有意留到 P2）**：真实任务执行。当前按钮在未注入 controller 时弹出明确说明，不假装执行、不伪造统计。

---

## 2. 目录结构（实际落地）

```
tbeplus/ui_qt/
  __init__.py            # create_app / create_window / run，DPI 策略入口
  theme.py               # 颜色令牌、字体回落链、尺寸、QSS
  paint.py               # 抗锯齿、圆角路径、表面/文字/图标绘制助手
  icons.py               # SVG → QPainter 渲染 + 多倍图 QIcon
  viewmodel.py           # 日志文本 → 统计/阶段/状态（UI 侧适配层）
  main_window.py         # 无边框窗口、标题栏、侧栏/顶栏/页面栈、toast
  dialogs.py             # 统一弹窗 + confirm()
  resources/icons/*.svg  # 27 个线性图标
  components/
    sidebar.py           # 版本选择器、NavItem、连接状态卡
    topbar.py            # 页标题、状态胶囊、计时、快捷入口
    stat_card.py         # 统计卡（数字为视觉中心）
    task_panel.py        # 执行面板：状态/进度/阶段/四个操作
    progress_bar.py      # 圆角进度条（240ms 补间）
    stages.py            # 五段式阶段条（idle/active/done/error）
    log_view.py          # 事件列表：model + proxy + 自绘 delegate
    status_badge.py      # 状态胶囊
    button.py            # Button / IconButton / WindowButton
    card.py              # 圆角卡片容器
    chips.py             # 级别筛选胶囊组
    toggle.py            # 开关
  pages/
    overview.py          # 首页组装与交互
    placeholder.py       # 未迁移页面的诚实占位
app_qt.py                # 新入口（可指定初始尺寸）
_shot_qt.py              # 截图/DPI 验收脚本
```

代码量：23 个 Python 文件、3095 行；图标 27 个 SVG。

> 说明：此前的 `tbeplus/qtui/`（v0.1 四页静态骨架）已被 `ui_qt` 完整取代并移除，仓库不再保留两套 Qt 代码。

---

## 3. 渲染体系要点

### 3.1 抗锯齿与圆角

- 所有自绘控件在 `paint.quality()` 中统一开启 `Antialiasing`、`TextAntialiasing`、`SmoothPixmapTransform`；
- 圆角一律 `QPainterPath.addRoundedRect`，不再出现 Tk 版 `round_rect()` 的 18 段折线；
- 边框绘制在 `rect.adjusted(0.5, 0.5, -0.5, -0.5)` 上，1px 线在任意 DPR 下都落在像素中心；
- 卡片层次用「1px 低对比边框 + 顶部 1px 极淡高光」，不用投影和渐变。

### 3.2 高 DPI

| 项 | 做法 |
| --- | --- |
| 感知级别 | Qt 6 默认 Per-Monitor V2（Windows），无需 ctypes |
| 缩放取整 | `HighDpiScaleFactorRoundingPolicy.PassThrough`，125% 不再被四舍五入 |
| 尺寸单位 | 全部逻辑像素（`setFixedHeight(32)`、QSS 用 `px`），由 Qt 换算 |
| 字体 | `QFont.setPixelSize`，避免 pt→px 的二次缩放 |
| 图标 | 矢量渲染，不随 DPI 生成位图；`QIcon` 仅用于菜单等位图场景（提供 1x/1.25x/1.5x/2x/3x） |

### 3.3 图标

- 统一 24×24 viewBox、`stroke-width 1.6`、`round` 端点与连接，颜色由 `currentColor` 替换；
- 名称语义化：`nav-dashboard` / `nav-log` / `nav-sliders` / `nav-folder` / `nav-link` / `nav-info` / `nav-gear` / `play` / `square` / `eye` / `plug` / `refresh` / `search` / `copy` / `download` / `trash` / `check` / `x` / `alert` / `clock` / `pause` / `minus` / `maximize` / `more` / `filter` / `chevron-down` / `chevron-right`。

### 3.4 控件状态

| 状态 | 表现 | 实现 |
| --- | --- | --- |
| normal | `bg_surface` + `border_strong`（200α） | `Button._colors` |
| hover | 背景提亮，140ms `OutCubic` 补间 | `QVariantAnimation` |
| pressed | 背景再下沉一档（primary → `accent_pressed`） | `isDown()` |
| disabled | 主按钮转 `accent_subtle` + 灰字；次按钮仅留边框 | `isEnabled()` |
| focus | 1px accent 30% 焦点环，键盘可见 | `hasFocus()` |
| 选中（导航） | `bg_active` + accent 图标 + 白字 | `NavItem.set_active` |

---

## 4. 首页结构与数据来源

| 区域 | 组件 | 数据来源（真实字段） |
| --- | --- | --- |
| 统计四卡 | `StatCard` ×4 | `RunStats.liked / already / failed / done`，全 0 时显示 `0`，不用横线 |
| 执行面板 | `TaskPanel` | 状态（idle/run/done/stopped/error）、`done/total`、`pending`、`success`、`failed`、用时 |
| 执行阶段 | `StageSteps` | 五阶段由日志事件推导：连接 → 板块 → 时间段筛选 → 逐条处理 → 本板块小结 |
| 实时日志 | `LogView` | 时间 / 级别 / 来源 / 内容，五档筛选 + 搜索 + 暂停滚动 + 复制 / 导出 / 清空 |
| 客户端状态 | `Sidebar.status_card` | 外部注入；原型阶段固定为「未接管客户端 + 业务引擎 P2 接入」 |
| 版本与模式 | `VersionPicker` | `tbeplus.__version__`；模式切换待 P2 接 `App._switch_mode` |

### 4.1 契约（`viewmodel.py`）

```python
vm.start(note)                  # 任务开始
vm.ingest_line("  列表里共 12 篇")  # 单行日志 → 统计/阶段/日志记录
vm.finish("done")               # 任务结束
vm.mark_connected("客户端已接管")  # 连接事件
```

信号：`stats_changed(RunStats)`、`stages_changed(list[str])`、`log_added(LogRecord)`、`run_state_changed(state, note)`、`current_changed(text, ts)`。

解析规则与 `tbeplus/ui.py::App._ingest` 同源（`板块：` / `列表里共 N 篇` / `时间段筛选：` / `[i/n] MM-DD <状态> 标题` / `本板块小结`），状态词表直接复用 `STATUS_KINDS`，便于新旧界面对账。

> 这是**最小侵入式适配**：引擎一行未改。若后续引擎改日志文案，解析需同步（已在遗留问题登记）。

---

## 5. 测试结果

### 5.1 DPI / 分辨率矩阵（`_shot_qt.py --dpr=`）

| 场景 | 窗口逻辑尺寸 | 输出 | 结果 |
| --- | --- | --- | --- |
| 1920×1080 @100% | 1920×1080 | 1920×1080 (dpr 1.0) | 通过 |
| 2560×1440 @125% | 2048×1152 | 2560×1440 (dpr 1.25) | 通过 |
| 2560×1440 @150% | 1707×960 | 2561×1440 (dpr 1.5) | 通过 |
| 3840×2160 @200% | 1920×1080 | 3840×2160 (dpr 2.0) | 通过 |

检查项：圆角平滑（200% 下无台阶）、字体清晰（无位图放大）、图标不模糊、窗口缩放正常、控件无重叠、无裁切、滚动条正常。

> 复现说明：Windows 下 `QT_SCALE_FACTOR` 是相对系统 DPI 的乘数，脚本用 `shcore.GetScaleFactorForDevice(0)` 换算成 `目标 / 系统百分比`，才能精确得到 100/125/150/200。

### 5.2 窗口尺寸

| 尺寸 | 结果 |
| --- | --- |
| 1280×800 | 通过（默认） |
| 1600×1000 | 通过 |
| 900×700 / 900×660 | 通过（低于最小宽度 980 的越界测试，仍无重叠、无截字） |

### 5.3 交互冒烟（脚本实测输出）

| 项 | 结果 |
| --- | --- |
| 7 页导航切换 + 标题 + 导航高亮 | 全部正确（home/log/settings/config/connect/version/prefs） |
| 级别筛选 | 错误 1、警告 2、关键 3、成功 6、全部 12，计数标签同步 |
| 关键词搜索 | "安全生产" → 2 条；无匹配 → 0 条 + 空状态「没有匹配的事件」 |
| 暂停自动滚动 | `auto_scroll=False/True` 正确切换 |
| 复制日志 | 12 行写入剪贴板，首行为 `17:05:40	KEY	连接	客户端已接管，调试端口 9222` |
| 清空日志 | 行数归零、空状态恢复、toast 提示「已清空日志」 |
| 运行状态联动 | 运行中禁用开始/试运行/接管，启用停止；`7/12`、待处理 5、成功 6、失败 1、用时 01:35 |
| 阶段条 | 前三段 done（绿）、第四段 active（紫）、第五段 idle |
| 编译检查 | `py_compile` 全部通过 |

### 5.4 未执行的验证

- **真实客户端接管与真实点赞**：按任务要求不动业务链路，未执行（也不应在原型阶段执行）。
- **多显示器热插拔 / 跨屏拖动**：需要物理双屏，未实机验证。
- **打包（PyInstaller + Qt 插件）**：未做，是最大不确定项（见遗留问题）。

---

## 6. 截图

`docs/ui-redesign/_preview/`：

| 文件 | 内容 |
| --- | --- |
| `qt2-home.png` | 首页空闲态（真实 0 数据，空状态引导） |
| `qt2-home-run.png` | 首页运行态（演练日志驱动，7/12） |
| `qt2-home-900.png` / `qt2-home-run-900.png` | 紧凑尺寸 |
| `qt2-home-filter-error.png` | 级别筛选「错误」 |
| `qt2-home-search-empty.png` | 搜索无结果空状态 |
| `qt2-hover.png` | 导航/按钮/胶囊/图标按钮 hover + 输入框 focus |
| `qt2-settings.png` | 未迁移页面的占位 |
| `dpi-1920x1080-100.png` 等 4 张 | DPI 矩阵 |

> `qt2-home-run*.png` 使用与引擎相同格式的**演练日志**驱动同一套解析逻辑（`_shot_qt.py --demo`），用于检查运行态样式；产品运行路径不会注入这些数据。

---

## 7. 文件变更清单

**新增**

- `tbeplus/ui_qt/**`（23 个 py，27 个 svg）
- `app_qt.py`（新入口，替换原 v0.1 入口内容）
- `_shot_qt.py`（截图/DPI 验收脚本，替换原 v0.1 内容）
- `docs/ui-redesign/P1-PySide6首页原型交付说明.md`

**移除**

- `tbeplus/qtui/`（v0.1 静态骨架，已被 `ui_qt` 取代；未提交过，无历史包袱）

**未改动**

- `tbeplus/engine.py`、`client.py`、`newsapi.py`、`config.py`
- `tbeplus/ui.py`、`ui_classic.py`、`widgets.py`、`theme.py`、`motion.py`
- `app.py`、`启动.bat`、`*.spec`、`build_exe.py`、`requirements.txt`

---

## 8. 遗留问题

1. **业务未接入**：`controller` 接口已预留（`start_run / start_dry_run / stop_run / attach_client / switch_mode / refresh_status`），但尚无实现。点击开始/试运行/接管会弹出说明，属有意为之。
2. **日志解析是隐式契约**：阶段与统计依赖引擎日志文案，文案变更需同步 `viewmodel.py`；P2 建议用录制日志做新老统计对账。
3. **来源列是推断值**：UI 按关键词归组（连接/资讯/点赞/系统），非引擎字段。
4. **停止无二次确认**：原型未接业务，故未加确认弹窗；P2 接入时应补 `dialogs.confirm`。
5. **导出为同步写盘**：日志量大时（>3000 行已被截断）可能短暂卡顿，P2 建议改线程或改为流式写。
6. **未做打包验证**：PyInstaller 需要包含 `PySide6` 插件与 `resources/icons`，`_shot_qt.py` 不参与打包；这是下一步的硬门槛。
7. **多显示器未实机验证**：仅用离屏/缩放模拟；跨屏 DPI 变化（Per-Monitor）需真机确认。
8. **字体依赖系统**：Inter 未安装时回落 Segoe UI + 微软雅黑 UI；若后续要统一视觉，需随包分发字体并做许可声明。

---

## 9. 启动与测试命令

```bash
# 依赖（隔离环境，不动项目 requirements.txt）
# C:/Users/Administrator/.workbuddy/binaries/python/envs/tbeplus-qt  （PySide6-Essentials 6.12.0）

# 启动新界面
"C:/Users/Administrator/.workbuddy/binaries/python/envs/tbeplus-qt/Scripts/python.exe" app_qt.py
# 指定初始尺寸
... app_qt.py 1600x1000

# 启动旧界面（Tkinter，完全不受影响）
python app.py

# 截图验收
... _shot_qt.py out.png home 1280x800          # 空闲
... _shot_qt.py out.png home 1280x800 --demo   # 运行态（演练日志）
... _shot_qt.py out.png home 1920x1080 --dpr=2.0   # 4K@200%

# 协议回归（与 UI 无关）
python tests/test_websocket.py
```

---

## 10. 下一步建议（P2）

1. 写 `QtController`：把 `engine.run()` 的日志回调 → `vm.ingest_line()`，按钮信号 → 原 `App._on_run/_on_dry_run/_on_stop/_on_prepare` 同等逻辑；
2. 用录制日志对新旧统计做逐条对账（同一份日志，两套界面统计必须一致）；
3. 迁移"运行日志"页（复用 `LogView`，几乎零成本）与"账号与连接"页；
4. 补 `停止` 二次确认、真实连接状态轮询；
5. 打包验证（onedir 优先，确认 Qt 插件与图标资源被收集）。
