"""Windows desktop entry point; only the maintainer needs a command line."""

import os
from pathlib import Path
import queue
import socket
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import urllib.request
import webbrowser

from desktop_runtime import DesktopRuntime, LocalLock, build_environment, local_path


class Launcher:
    def __init__(self, window, runtime):
        self.window, self.runtime = window, runtime
        self.events = queue.Queue()
        self.process = None
        self.log = None
        self.busy = False
        self.remote = None
        window.title("鯉魚潭水庫 — 啟動與更新")
        window.geometry("760x340")
        self.current = tk.StringVar()
        self.available = tk.StringVar(value="遠端版本：尚未檢查")
        self.status = tk.StringVar(value="初次使用請按「檢查更新」，再安裝。")
        for variable in (self.current, self.available, self.status):
            ttk.Label(window, textvariable=variable, wraplength=720).pack(padx=20, pady=10, anchor="w")
        ttk.Label(window, text="停止／更新前請先下載未保存的工作批次 JSON。關閉瀏覽器不會停止程式。").pack(padx=20)
        row = ttk.Frame(window)
        row.pack(pady=20)
        self.buttons = []
        for label, command in (("啟動程式", self.start), ("停止程式", self.stop),
                               ("檢查更新", self.check), ("安裝顯示的版本", self.update)):
            button = ttk.Button(row, text=label, command=command)
            button.pack(side="left", padx=5)
            self.buttons.append(button)
        self.refresh()
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.after(100, self.poll)

    def refresh(self):
        active = self.runtime.active()
        self.current.set("目前版本：" + (f"git-{active['commit'][:12]}\nGit commit：{active['commit']}" if active else "尚未安裝"))

    def work(self, action):
        if self.busy:
            return
        self.busy = True
        for button in self.buttons:
            button.configure(state="disabled")

        def worker():
            try:
                action()
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("done", None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == "remote":
                self.remote = value
                self.available.set(f"遠端可用版本（main）：git-{value[:12]}\nGit commit：{value}")
            elif kind == "done":
                self.busy = False
                for button in self.buttons:
                    button.configure(state="normal")
                self.refresh()
            elif kind == "error":
                self.status.set("操作失敗：" + value)
            else:
                self.status.set(value)
        if self.process and self.process.poll() is not None and not self.busy:
            self.status.set("程式已結束；若非手動停止，請維護者檢查本機 streamlit.log。")
            self.process = None
            if self.log:
                self.log.close()
                self.log = None
        self.window.after(100, self.poll)

    def check(self):
        self.remote = None
        self.available.set("遠端版本：檢查中…")
        def action():
            commit = self.runtime.available()
            self.events.put(("remote", commit))
            active = self.runtime.active()
            self.events.put(("status", "已是最新版本。" if active and active["commit"] == commit else "可按「安裝顯示的版本」完成安裝／更新。"))
        self.work(action)

    def update(self):
        if self.process and self.process.poll() is None:
            messagebox.showinfo("先停止程式", "請先下載工作批次 JSON，再按「停止程式」後更新。")
            return
        if not self.remote:
            messagebox.showinfo("檢查更新", "請先檢查遠端可用版本。")
            return
        commit = self.remote
        def action():
            self.runtime.install(commit, lambda text: self.events.put(("status", text)))
            self.events.put(("status", "安裝／更新成功，可啟動目前版本。舊版本保留於本機。"))
        self.work(action)

    def start(self):
        if self.process and self.process.poll() is None:
            webbrowser.open(self.url)
            return
        active = self.runtime.active()
        if not active:
            messagebox.showinfo("尚未安裝", "請先檢查更新並安裝。")
            return
        def action():
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            command = self.runtime.launch_command(active, port)
            self.url = f"http://127.0.0.1:{port}"
            self.log = local_path(self.runtime.root / "streamlit.log").open("ab")
            self.process = subprocess.Popen(
                command, cwd=self.runtime.repository(active), env=build_environment(application=True),
                stdout=self.log, stderr=self.log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError("啟動失敗，請維護者檢查本機 streamlit.log。")
                try:
                    with opener.open(self.url + "/_stcore/health", timeout=0.3) as response:
                        if response.status == 200:
                            webbrowser.open(self.url)
                            self.events.put(("status", "程式執行中。"))
                            return
                except OSError:
                    pass
                time.sleep(0.3)
            self.process.terminate()
            self.process.wait(timeout=10)
            raise RuntimeError("啟動逾時；請維護者檢查本機 streamlit.log。")
        self.work(action)

    def stop(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("停止程式", "未保存的工作階段將消失。已下載工作批次 JSON 並要停止嗎？"):
                return
            def action():
                self.process.terminate()
                self.process.wait(timeout=10)
                self.events.put(("status", "程式已停止。"))
            self.work(action)

    def close(self):
        if self.busy:
            messagebox.showinfo("處理中", "請等待目前操作完成後再關閉。")
        elif self.process and self.process.poll() is None:
            messagebox.showinfo("程式執行中", "請先按「停止程式」，再關閉啟動器。")
        else:
            self.window.destroy()


def main():
    window = tk.Tk()
    try:
        runtime = DesktopRuntime(Path(os.environ["LOCALAPPDATA"]) / "LiyutanEstimator")
        with LocalLock(runtime.root):
            Launcher(window, runtime)
            window.mainloop()
    except Exception as exc:
        messagebox.showerror("啟動器無法開啟", str(exc))
        window.destroy()


if __name__ == "__main__":
    main()
