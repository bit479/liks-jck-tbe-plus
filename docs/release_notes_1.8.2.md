# 1.8.2 更新说明

本次只改渲染、样式与窗口管理，业务逻辑（点赞流程、接口调用、数据结构）未改动。

## 品牌图标

- 左上角改用「眨眼宝宝点赞」图标（`眨眼宝宝点赞图标.png`）。
- 用 4 倍超采样裁掉原图白边、做圆角蒙版后导出 24/30/32/36/44/48/64/256 多尺寸 PNG，
  运行时按 DPI 选尺寸，125% / 150% 下不会拉伸发虚。
- 顺带用同一张图重新生成了 `app.ico` / `app.png` / `app_24|44|64.png`，
  任务栏和窗口图标保持一致。

## 圆角

- 统一圆角 Token：窗口 14、卡片 16、按钮/输入框 10、胶囊 999（`theme.py`）。
- `round_rect()` 的圆弧从每角 6 段加密到 18 段，折线台阶在 1x / 1.25x / 1.5x 下明显减轻。
- **说明**：Tkinter 的 Canvas 没有抗锯齿（没有 `QPainter.Antialiasing` 那种能力），
  因此卡片/按钮的圆角只能做到"更细的折线"，无法像 Web/浏览器那样完全平滑。
  窗口外框是无边框（`overrideredirect`）窗口，DWM 的系统圆角对它不生效，
  我们没有用 `SetWindowRgn` 做区域裁剪（那正是锯齿的常见来源），所以窗口四角仍为直角。

## 切换页面不再白屏 / 缓慢出现

- 原来 `_show_page()` 先 `place_forget()` 旧页、再 `place()` 新页（顺序受字典顺序影响），
  中间会露出底色；现在改为**先让目标页就位并 `lift()`，再隐藏其它页**。
- 切页位移动画从 220ms 降到 110ms、位移从 8px 降到 4px（不超过 150ms 的要求）。
- 实测纯切换开销：80 次 1418ms，平均 **17.7ms/次**（目标 < 100ms）。

## 最小化 / 还原

- 原来 `_minimize()` 先 `overrideredirect(False)` 再 `iconify()`，`<Map>` 里 30ms 后又切回
  `overrideredirect(True)`。窗口样式反复切换会让客户区重算、整窗重绘，就是白闪和跳变的来源。
- 现在直接调用系统 `ShowWindow(hwnd, SW_MINIMIZE)`，**全程保持 `overrideredirect(True)`**；
  还原时 `<Map>` 不再改样式、也不再 `SetWindowPos(FRAMECHANGED)`，只做轻量热区校正。
- 去掉了启动时的 `-alpha` 整窗淡入（`fade_window`），窗口不再是分层窗口，避免与系统动画叠加。
- 实测状态机：`normal → iconic → normal`，`overrideredirect` 始终为 True，几何尺寸不变。
- **说明**：`overrideredirect` 无边框窗口在部分 Windows 版本上可能拿不到系统"缩向任务栏"的
  过渡动画。要做到完全一致需要保留 `WS_CAPTION | WS_THICKFRAME` 再用 `WM_NCCALCSIZE` 隐藏
  非客户区，这已超出 Tkinter 的能力范围；当前版本保证的是"不白闪、不跳变"。

## 点赞总是失败

- 用当前 token 实测：`add_digg` 返回成功，赞数 116 → 117，`is_digg` 变为 True，
  说明**接口与 token 正常**，失败不是接口问题。
- 失败发生在程序走了"客户端会话（CDP）"而没有走纯 HTTP。钉钉版页面显示
  「执行方式 纯 HTTP」，用该模式即可正常点赞；若老版本模式没有可用 token，
  会退回 CDP，而新客户端不支持 CDP，就会整批失败。

安装包：`TBeAutoLike-1.8.2-win64.exe`（未签名，SmartScreen 提示时点
「更多信息 → 仍要运行」）。
