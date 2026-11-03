# UI 与业务层解耦方案

## 1. 现状：耦合点清单

| 位置 | 耦合内容 | 影响 |
| --- | --- | --- |
| `ui.py::_run_job` | 在工作线程里读 `since_var` / `until_var` / `limit_stepper` 等 Tk 变量 | 跨线程访问 UI，偶发异常/读到中间值 |
| `ui.py::_apply_settings` | 直接改写 `Config` 字段并调用 `save_debug_port()` | UI 承担配置写回 |
| `ui.py::_ingest` | 正则解析引擎日志文案驱动统计与阶段 | 文案变化即统计失效 |
| `ui.py::_refresh_status` | UI 直接编排 `client.resolve_debug_port()` / `dingtalk.is_home_running()` | UI 承担连接探测 |
| `ui.py::_save_dt_token` | 主线程同步网络校验（最长 8s） | 阻塞事件循环 |
| `ui.py::_drain` | 日志双视图各写一份 | 重复解析/渲染，长任务成本翻倍 |
| `ui.py::_start/_finish` | 用 `self.running/_last_error/stop_flag` 管任务生命周期 | 状态分散，无 run_id |
| `engine.py` | 仅通过 `log(str)` 输出，不依赖 UI | ✅ 方向正确，可原样复用 |
| `client/newsapi/config` | 纯 Python，无 UI 依赖 | ✅ 可原样复用 |

**结论**：依赖方向总体正确（UI → 业务），但“业务编排、日志解析、状态机”被塞进了 UI 层。解耦的重点是把这三件事抽出来，而不是改业务算法。

## 2. 目标目录（含命名冲突处理）

任务建议的 `tbeplus/ui/` 会与现有 `tbeplus/ui.py` **同名冲突**（模块与包不能共存）。迁移期采用：

```text
tbeplus/
  ui_qt/                     # 迁移期新包（避免与 ui.py 冲突）
    __init__.py
    main_window.py
    theme.py
    resources/
      icons/*.svg
    components/
      sidebar.py
      topbar.py
      stat_card.py
      task_panel.py
      progress_bar.py
      log_view.py
      status_badge.py
      dialogs.py
    adapters/
      run_controller.py
      connection_controller.py
      log_bridge.py
    models/
      run_state.py
      log_model.py
  ui.py                      # 旧 Tk 界面（正式入口，暂不改名）
  engine.py / client.py / newsapi.py / config.py   # 不动
```

正式切换时二选一：把旧 `ui.py` 重命名为 `ui_tk.py` 并把 `ui_qt/` 改名为 `ui/`（需人工批准，一次性提交）；或长期保留 `ui_qt` 名称。**在此之前不重命名任何现有文件。**

> 已有 `tbeplus/qtui/` 原型可整体演进为 `ui_qt/`，避免重复劳动。

## 3. 数据契约

### 3.1 RunRequest（主线程生成，不可变）

```text
run_id: str
mode: "old" | "dingtalk"
dry_run: bool
boards: list[str]
since: str            # YYYY-MM-DD 或 ""
until: str            # 同上
limit: int
restart_every: int
read_state: bool
verify: bool
config_snapshot: Config  # dataclasses.replace 的副本
```

- 由 UI 在点击“开始/试运行”时一次性组装；worker 只读该对象。
- 日期格式校验在生成前完成，错误以字段级提示返回。

### 3.2 RunEvent（worker → UI，带 run_id）

```text
RunStarted(run_id, request)
ConnectionChanged(state, detail)
SectionStarted(run_id, board)
SectionTotal(run_id, board, total)
ArticleFinished(run_id, board, index, total, status, title)
SectionFinished(run_id, board, stats)
RunFinished(run_id, result | error | stopped)
LogAppended(run_id, record)
```

- UI 丢弃 `run_id != current_run_id` 的迟到事件。
- 首版 `RunEvent` 可由 `LegacyLogAdapter` 从现有日志文本生成，保证不改 `engine.py`；适配器必须有单元测试，且与旧 `_ingest` 统计结果逐项对账。

### 3.3 LogRecord

```text
ts: str              # HH:MM:SS（界面收到时刻，不伪造服务端时间）
level: info|ok|warn|err
source: 连接|资讯|点赞|系统   # UI 侧归类
message: str
raw: str             # 原始日志，用于导出/复制
```

### 3.4 ConnectionState

```text
mode: old|dingtalk
client_running: bool
port: int | None
token: 未设置|已设置|已失效
detail: str
```

## 4. 线程模型

```text
┌────────────── UI 主线程（Qt event loop）──────────────┐
│ MainWindow / Components  ← 只在这里碰控件             │
│      ▲ queued signals                                 │
│      │                                                │
│  LogBridge(QObject) / RunBridge(QObject)              │
└──────┼────────────────────────────────────────────────┘
       │ emit(record/event)
┌──────┴──────── Worker 线程（asyncio loop）────────────┐
│ RunController → LikeEngine.ensure_http()/run()        │
│ ConnectionController → client/dingtalk 探测与接管      │
│ 不导入任何 Qt 控件；只持有 bridge 的 emit 方法         │
└───────────────────────────────────────────────────────┘
```

规则：

1. **业务模块（engine/client/dingtalk/newsapi）不导入 Qt**；bridge 只属于 `ui_qt/adapters`。
2. 跨线程只通过 Qt Signal（`Qt.QueuedConnection`）传递不可变数据。
3. `stop_flag` 换成 `threading.Event` 或 `CancellationToken`，由 `RunController` 持有。
4. 退出时 `stop_flag.set()` + `thread.join(timeout)`，避免后台线程回写已销毁对象。
5. 不做 asyncio 与 Qt 事件循环的嵌套；worker 内自建 loop，结束后关闭。

## 5. 控制器与模型职责

| 对象 | 职责 | 不做什么 |
| --- | --- | --- |
| `RunController` | 组装 `RunRequest`、启动/停止 worker、维护 `run_id` 与运行状态 | 不解析日志、不碰控件 |
| `ConnectionController` | 端口/客户端/token 探测与准备，产出 `ConnectionState` | 不更新 UI 文案 |
| `LogBridge` | 接收 `log(str)`，转成 `LogRecord`，emit | 不做统计 |
| `LegacyLogAdapter` | 从文本生成 `RunEvent`（板块/总数/筛选/条目/小结） | 不改引擎 |
| `RunStateModel` | 聚合 processed/liked/already/failed/pending、阶段状态 | 不画图 |
| `LogModel` | 有界日志、筛选、搜索、复制/导出 | 不直接引用视图 |
| `SettingsModel` | 读取/写回 `Config`，生成摘要 | 不启动任务 |

## 6. 旧调用 → 新归属映射

| 现位置 | 新归属 |
| --- | --- |
| `ui._run_job` 中 `ensure_http` + 日期校验 + `engine.run` | `RunController.start(RunRequest)` |
| `ui._apply_settings` | `SettingsModel.to_config()` |
| `ui._refresh_status` | `ConnectionController.refresh()` |
| `ui._on_attach/_attach_job` | `ConnectionController.attach_client()` |
| `ui._on_dingtalk_prepare/_start_dt_watch` | `ConnectionController.prepare_dingtalk()` |
| `ui._save_dt_token` | `ConnectionController.validate_token_async()` |
| `ui._drain/_ingest` | `LogBridge` + `LegacyLogAdapter` + `RunStateModel` |
| `ui._recalc` 的统计部分 | `RunStateModel` 聚合；视图只绑定结果 |
| `ui._copy_log/_export_log` | 调用 `LogModel.copy_visible()/dump()` |

## 7. 首页迁移映射

| 首页区域 | 数据来源 | 迁移方式 |
| --- | --- | --- |
| 四张统计卡 | `RunStateModel`：processed/total、liked、already、failed | 绑定数值与说明；无数据为 0 |
| 执行面板 | `RunState`：状态、processed/pending、success/fail、elapsed | 由事件驱动，不读控件 |
| 五阶段 | `RunState.stages`：连接/获取/筛选/点赞/统计 | “筛选”由 `LegacyLogAdapter` 解析“时间段筛选”得出；引擎改文案需同步 |
| 进度条 | processed/total | 目标值立即更新，显示值平滑 |
| 实时日志 | `LogModel` 的 `LogRecord` | 时间/级别/来源/内容四列；有界 3000 行 |
| 连接状态 | `ConnectionState` | 侧栏底部 + 页头状态胶囊 |
| 四个按钮 | `RunController.start(dry_run)` / `stop()` / `ConnectionController.prepare()` | 保持原语义与确认流程 |

## 8. 兼容与回退

- 双入口：`app.py`（Tk，正式）与 `app_qt.py`（Qt，实验）并存；启动参数/快捷方式暂不改。
- 配置：继续读写 `config.json`、`state.json`；路径由 `config.APP_DIR` 决定，不新增格式。
- 接管：继续调用 `client.restart_client()` / `dingtalk.auto_prepare()`；不重写进程/端口逻辑。
- 统计：迁移期用录制日志同时跑旧 `_ingest` 与新 `LegacyLogAdapter`，逐项对账（processed/liked/already/failed/pending）。
- 回退：任一阶段出现问题，删除/停用 `app_qt.py` 即可，旧 UI 不受影响。

## 9. 测试与风险

**必须补的测试**

1. `LegacyLogAdapter`：板块/总数/筛选/条目/小结/停止，含中英文状态词与重复序号。
2. `RunController`：重复点击、停止、异常、迟到事件（run_id 不匹配）。
3. `SettingsModel`：日期、板块、limit/restart、快速/复查。
4. `LogModel`：五档筛选、搜索、复制可见、导出全部、3000 行裁剪。
5. `ConnectionController`：token 校验不阻塞主线程、准备失败可恢复。

**主要风险**

- 日志解析仍是隐式契约：引擎文案变更会破坏阶段/统计；需测试兜底，长期再推动引擎发结构化事件。
- asyncio 与 Qt 生命周期：窗口关闭时 worker 未退出会导致崩溃；必须 join + run_id 失效。
- 阶段“结果统计”在运行中应为 idle、完成/失败时才置 done/error；避免虚假完成。
- 图标/字体许可与打包：SVG 集与字体来源需记录。

## 10. 实施顺序（与可行性分析一致）

1. 建立 `ui_qt/` 骨架与 `RunRequest/RunEvent/LogRecord` 契约。
2. 用 fake worker 驱动首页，验证统计/阶段/日志不依赖真实点赞。
3. 接 `RunController` 与真实 `engine.run`，保持旧 UI 可对照。
4. 逐页迁移日志、设置、配置、版本、偏好、连接。
5. 回归与打包；正式切换需单独批准。
