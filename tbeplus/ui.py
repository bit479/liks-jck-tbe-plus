"""图形界面：双击就能用，三个按钮走完全流程。"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from tkinter import BOTH, END, LEFT, X, BooleanVar, IntVar, StringVar, Tk, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from . import client as client_mod
from .config import APP_DIR, Config, load_config
from .engine import LikeEngine, summarize


class App:
    def __init__(self, root: Tk, config: Config):
        self.root = root
        self.cfg = config
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()

        root.title("TBe+ 新闻资讯自动点赞")
        root.geometry("840x620")
        root.minsize(720, 520)

        self._build()
        self._refresh_status()
        self.root.after(120, self._drain_log)

    # ------------------------------------------------------------------
    # 界面搭建
    # ------------------------------------------------------------------
    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}

        header = ttk.Frame(self.root)
        header.pack(fill=X, **pad)
        ttk.Label(
            header, text="TBe+ 新闻资讯自动点赞", font=("Microsoft YaHei UI", 15, "bold")
        ).pack(anchor="w")
        ttk.Label(
            header,
            text="自动遍历【Tbe+新闻资讯】【进出口公司新闻资讯】【开讲了】，"
            "把没点赞的文章都点上。",
            foreground="#555",
        ).pack(anchor="w")

        status = ttk.LabelFrame(self.root, text="客户端状态")
        status.pack(fill=X, **pad)
        self.status_var = StringVar(value="正在检查客户端…")
        ttk.Label(status, textvariable=self.status_var).pack(anchor="w", padx=10, pady=8)
        ttk.Button(status, text="刷新状态", command=self._refresh_status).pack(
            anchor="e", padx=10, pady=(0, 8)
        )

        buttons = ttk.LabelFrame(self.root, text="操作")
        buttons.pack(fill=X, **pad)

        row1 = ttk.Frame(buttons)
        row1.pack(fill=X, padx=10, pady=8)
        self.btn_attach = ttk.Button(
            row1, text="① 准备：重启并接管 TBe+", command=self._on_attach
        )
        self.btn_attach.pack(side=LEFT)
        self.btn_dry = ttk.Button(row1, text="② 试运行（只看不点）", command=self._on_dry_run)
        self.btn_dry.pack(side=LEFT, padx=8)
        self.btn_run = ttk.Button(row1, text="③ 开始点赞", command=self._on_run)
        self.btn_run.pack(side=LEFT)

        row2 = ttk.Frame(buttons)
        row2.pack(fill=X, padx=10, pady=(0, 8))
        self.btn_stop = ttk.Button(row2, text="停止", command=self._on_stop, state="disabled")
        self.btn_stop.pack(side=LEFT)
        ttk.Button(row2, text="打开程序目录", command=self._open_output).pack(side=LEFT, padx=8)
        ttk.Button(row2, text="重新读取配置", command=self._reload_config).pack(side=LEFT)

        row3 = ttk.Frame(buttons)
        row3.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Label(row3, text="每个板块最多处理").pack(side=LEFT)
        self.limit_var = IntVar(value=0)
        ttk.Spinbox(row3, from_=0, to=999, width=5, textvariable=self.limit_var).pack(
            side=LEFT, padx=4
        )
        ttk.Label(row3, text="篇（0 = 全部）").pack(side=LEFT)
        self.skip_session_var = BooleanVar(value=self.cfg.skip_session_by_default)
        ttk.Checkbutton(
            row3, text="跳过【进出口公司新闻资讯】", variable=self.skip_session_var
        ).pack(side=LEFT, padx=20)

        log_frame = ttk.LabelFrame(self.root, text="运行日志")
        log_frame.pack(fill=BOTH, expand=True, **pad)
        self.log_box = ScrolledText(log_frame, height=16, wrap="word", state="disabled")
        self.log_box.pack(fill=BOTH, expand=True, padx=8, pady=8)

    # ------------------------------------------------------------------
    # 日志与状态
    # ------------------------------------------------------------------
    def log(self, message: str) -> None:
        self.log_queue.put(message)

    def _drain_log(self) -> None:
        drained = 0
        while drained < 300:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_box.configure(state="normal")
            self.log_box.insert(END, message + "\n")
            self.log_box.see(END)
            self.log_box.configure(state="disabled")
            drained += 1
        self.root.after(120, self._drain_log)

    def _refresh_status(self) -> None:
        port = self.cfg.debug_port
        if client_mod.is_attached(port):
            targets = client_mod.summarize_targets(port)
            titles = "，".join(t for _, t in targets if t)[:70]
            self.status_var.set(
                f"● 已接管（端口 {port}）　视图 {len(targets)} 个　{titles}"
            )
        elif client_mod.is_client_running():
            self.status_var.set(f"● TBe+ 正在运行，但没开调试端口 {port}。请点「① 准备」。")
        else:
            self.status_var.set("● 没检测到 TBe+ 在运行。")

    def _reload_config(self) -> None:
        self.cfg = load_config()
        self.skip_session_var.set(self.cfg.skip_session_by_default)
        self.log("已重新读取配置")
        self._refresh_status()

    def _open_output(self) -> None:
        try:
            os.startfile(str(APP_DIR))  # noqa: S606  (Windows 专用)
        except Exception as exc:  # noqa: BLE001
            messagebox.showinfo("程序目录", f"{APP_DIR}\n\n({exc})")

    # ------------------------------------------------------------------
    # 任务调度
    # ------------------------------------------------------------------
    def _busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (self.btn_attach, self.btn_dry, self.btn_run):
            button.configure(state=state)
        self.btn_stop.configure(state="normal" if busy else "disabled")

    def _start(self, title: str, coro_factory) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("正在运行", "上一个任务还没结束，请稍等。")
            return
        self.stop_flag.clear()
        self._busy(True)
        self.log("")
        self.log(f"===== {title} =====")

        def worker() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(coro_factory())
            except Exception as exc:  # noqa: BLE001
                self.log(f"[出错] {exc}")
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
                self.root.after(0, lambda: self._busy(False))
                self.root.after(0, self._refresh_status)

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    # ------------------------------------------------------------------
    # 三个按钮
    # ------------------------------------------------------------------
    def _on_attach(self) -> None:
        if not messagebox.askyesno(
            "准备接管",
            "这一步会关闭正在运行的 TBe+ 客户端，再带调试端口重新启动。\n\n"
            "登录状态会保留，但正在进行的对话窗口会被中断。\n\n确定继续吗？",
        ):
            return
        self._start("准备：重启并接管 TBe+", self._attach_job)

    async def _attach_job(self) -> None:
        ok = client_mod.attach(
            self.cfg.client_exe_candidates,
            self.cfg.debug_port,
            restart=True,
            timeout=self.cfg.restart_wait_seconds,
            log=self.log,
        )
        self.log("准备完成，可以点「② 试运行」或「③ 开始点赞」了。" if ok else "准备失败。")

    def _on_dry_run(self) -> None:
        self._start("试运行（只看不点）", lambda: self._run_job(True))

    def _on_run(self) -> None:
        if not messagebox.askyesno(
            "开始点赞", "将对三个板块里所有未点赞的文章执行点赞。\n\n确定继续吗？"
        ):
            return
        self._start("正式点赞", lambda: self._run_job(False))

    async def _run_job(self, dry_run: bool) -> None:
        if not client_mod.is_attached(self.cfg.debug_port):
            self.log("客户端还没接管，请先点「① 准备」。")
            return
        self.cfg.skip_session_by_default = bool(self.skip_session_var.get())
        engine = LikeEngine(
            self.cfg,
            dry_run=dry_run,
            log=self.log,
            stop_flag=self.stop_flag.is_set,
        )
        results = await engine.run(limit=self.limit_var.get())
        self.log("")
        self.log(summarize(results))
        if not dry_run:
            self.log("提示：已点赞的会自动跳过，重复运行不会重复点。")

    def _on_stop(self) -> None:
        self.stop_flag.set()
        self.log("已请求停止，正在结束当前文章…")


def run_ui(config: Config | None = None) -> None:
    root = Tk()
    try:
        root.call("tk", "scaling", 1.25)
    except Exception:
        pass
    App(root, config or load_config())
    root.mainloop()
