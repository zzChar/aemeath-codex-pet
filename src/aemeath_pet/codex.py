"""Read-only account RPC and incremental local turn-lifecycle observation."""
import json
import math
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass

import psutil

def codex_home():
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))

def locate_codex(override=""):
    if override and Path(override).is_file():
        return override
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin"
    choices = list(base.glob("*/codex.exe")) if base.exists() else []
    if choices:
        return str(max(choices, key=lambda p:p.stat().st_mtime))
    return shutil.which("codex")

class AccountRPC:
    def __init__(self, executable):
        self.executable = executable
        self.process = None
        self.serial = 0
        self.replies = queue.Queue()
        self.lock = threading.Lock()

    def _reader(self, process):
        try:
            for line in process.stdout:
                try:
                    msg = json.loads(line)
                    if "id" in msg:
                        self.replies.put(msg)
                except (ValueError, TypeError):
                    continue
        except (OSError, ValueError):
            pass
        self.replies.put({"closed": True})

    def _call(self, method, params=None, timeout=18):
        self.serial += 1
        request = {"id": self.serial, "method": method}
        if params is not None:
            request["params"] = params
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                msg = self.replies.get(timeout=max(.1, deadline - time.monotonic()))
            except queue.Empty:
                break
            if msg.get("closed"):
                raise RuntimeError("Codex 额度连接已关闭")
            if msg.get("id") != self.serial:
                continue
            if "error" in msg:
                # Do not save or expose raw server messages/account identity.
                raise RuntimeError("Codex 未能提供额度，请确认桌面端已登录")
            return msg.get("result", {})
        raise TimeoutError("读取额度超时，请稍后刷新")

    def read_limits(self):
        with self.lock:
            if not self.executable:
                raise RuntimeError("未找到 Codex；请在设置中选择 codex.exe")
            if not self.process or self.process.poll() is not None:
                self.replies = queue.Queue()
                self.process = subprocess.Popen(
                    [self.executable, "app-server", "--listen", "stdio://"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, encoding="utf-8", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                threading.Thread(target=self._reader, args=(self.process,), daemon=True).start()
                self._call("initialize", {
                    "clientInfo": {"name": "aemeath-pet", "title": "Aemeath Pet", "version": "0.4.0"},
                    "capabilities": {"experimentalApi": True},
                })
                self.process.stdin.write('{"method":"initialized"}\n')
                self.process.stdin.flush()
            try:
                result = self._call("account/rateLimits/read")
                return normalize_limits(result)
            except Exception:
                self.close()
                raise

    def close(self):
        process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()

def normalize_limits(result):
    by_id = result.get("rateLimitsByLimitId") or {}
    bucket = by_id.get("codex") or result.get("rateLimits")
    if not isinstance(bucket, dict):
        raise RuntimeError("当前账号未返回 Codex 限额信息")
    windows = []
    for key in ("primary", "secondary"):
        value = bucket.get(key)
        if not isinstance(value, dict) or value.get("usedPercent") is None:
            continue
        used = float(value["usedPercent"])
        if not math.isfinite(used):
            continue
        windows.append({"key": key, "remaining": max(0, min(100, 100-used)),
                        "minutes": value.get("windowDurationMins"), "resets_at": value.get("resetsAt")})
    if not windows:
        raise RuntimeError("暂无可用限额；请确认使用 ChatGPT 账号登录")
    return {"windows": windows, "checked_at": time.time()}

def desktop_open():
    for p in psutil.process_iter(["name", "exe"]):
        try:
            exe = (p.info.get("exe") or "").lower().replace("\\", "/")
            if "/openai.codex_" in exe and exe.endswith("/chatgpt.exe"):
                return True
            if (p.info.get("name") or "").lower() in ("codex desktop.exe", "codexdesktop.exe"):
                return True
        except (psutil.Error, OSError):
            continue
    return False

@dataclass
class TaskEvent:
    thread_id: str
    title: str
    kind: str
    turn_id: str

class RolloutReader:
    """Retain lifecycle/item types and call IDs, never message/reasoning content.

    Local persisted items arrive after streaming; phase is an observation of the
    latest available activity, not an exact live model/server status.
    """
    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.active = None
        self.seen = set()
        self.initialized = False
        self.phase = None
        self.phase_at = ""
        self.pending_calls = set()

    def observe_item(self, payload, stamp=""):
        if not self.active or not isinstance(payload, dict):
            return
        kind = payload.get("type")
        call = payload.get("call_id")
        if kind in ("function_call", "custom_tool_call") and call:
            self.pending_calls.add(call)
        elif kind in ("function_call_output", "custom_tool_call_output") and call:
            self.pending_calls.discard(call)
        elif kind not in ("reasoning", "Reasoning", "agent_message", "AgentMessage", "message"):
            return
        elif kind == "message" and payload.get("role") != "assistant":
            return
        self.phase = "working" if self.pending_calls else "thinking"
        self.phase_at = stamp or self.phase_at

    def poll(self):
        events = []
        if not self.path.exists():
            return events
        if self.path.stat().st_size < self.offset:
            self.offset, self.active, self.initialized = 0, None, False
            self.seen.clear()
            self.phase = None; self.pending_calls.clear(); self.phase_at = ""
        bootstrap = not self.initialized
        with self.path.open("rb") as f:
            f.seek(self.offset)
            while True:
                start = f.tell()
                line = f.readline()
                if not line:
                    self.offset = f.tell()
                    break
                if not line.endswith(b"\n"):
                    self.offset = start
                    break
                self.offset = f.tell()
                if b'"event_msg"' not in line[:200] and b'"response_item"' not in line[:200]:
                    continue
                try:
                    raw = json.loads(line)
                    payload = raw.get("payload", {})
                    if raw.get("type") == "response_item":
                        self.observe_item(payload, raw.get("timestamp", ""))
                        continue
                    if raw.get("type") != "event_msg" or not isinstance(payload, dict):
                        continue
                    if payload.get("type") == "item_completed":
                        if payload.get("turn_id") == self.active:
                            self.observe_item(payload.get("item"), raw.get("timestamp", ""))
                        continue
                    kind = {"task_started": "started", "task_complete": "completed", "turn_aborted": "aborted"}.get(payload.get("type"))
                    turn = payload.get("turn_id")
                    if not kind or not turn or (turn, kind) in self.seen:
                        continue
                    self.seen.add((turn, kind))
                    if kind == "started":
                        self.active = turn
                        self.pending_calls.clear(); self.phase = "thinking"
                        self.phase_at = raw.get("timestamp", "")
                    elif self.active == turn:
                        self.active = None
                        self.pending_calls.clear(); self.phase = None
                    if not bootstrap:
                        events.append((kind, turn))
                except (ValueError, TypeError, AttributeError):
                    continue
        self.initialized = True
        return events

class TaskMonitor:
    def __init__(self, home=None, is_open=desktop_open):
        self.home = Path(home) if home else codex_home()
        self.is_open = is_open
        self.readers = {}
        self.titles = {}
        self.status = "等待 Codex"
        self.error = ""
        self.phase = None
        self.phases = {}

    def poll(self):
        self.phase = None; self.phases = {}
        if not self.is_open():
            self.status = "Codex 未打开"
            self.readers.clear()
            return [], []
        choices = sorted(self.home.glob("state_*.sqlite"), key=lambda p:p.stat().st_mtime, reverse=True)
        if not choices:
            self.status = "未找到本地任务记录"
            return [], []
        events = []
        try:
            with sqlite3.connect(choices[0].as_uri()+"?mode=ro", uri=True, timeout=1) as db:
                db.row_factory = sqlite3.Row
                cols = {r[1] for r in db.execute("pragma table_info(threads)")}
                # Exclude automatic agents; accept old migrated desktop records.
                origin = " AND (originator IS NULL OR originator IN ('Codex Desktop','codex_work_desktop'))" if "originator" in cols else ""
                rows = list(db.execute("SELECT id,title,rollout_path FROM threads WHERE archived=0 AND source='vscode' AND agent_path IS NULL" + origin + " ORDER BY updated_at DESC LIMIT 20"))
            ids = {row["id"] for row in rows}
            self.readers = {k:v for k,v in self.readers.items() if k in ids}
            for row in rows:
                tid, path = row["id"], row["rollout_path"]
                if not path:
                    continue
                self.titles[tid] = row["title"] or "Codex 任务"
                if tid not in self.readers:
                    self.readers[tid] = RolloutReader(path)
                reader = self.readers[tid]
                was_new = not reader.initialized
                for kind, turn in reader.poll():
                    events.append(TaskEvent(tid, self.titles[tid], kind, turn))
                # A historical interrupted session can lack an ending marker.
                # Do not announce it as running on startup without a live writer
                # or recent activity. Never manufacture a completion event.
                if was_new and reader.active:
                    lock = self.home / "thread-writer-locks" / (tid + ".lock")
                    if not lock.exists() and time.time() - reader.path.stat().st_mtime > 60:
                        reader.active = None
            active = [(tid, self.titles[tid]) for tid, reader in self.readers.items() if reader.active]
            self.phases = {tid: self.readers[tid].phase for tid, _ in active}
            # Parallel work: any pending tool keeps the shared pet at the desk.
            self.phase = ("working" if "working" in self.phases.values() else "thinking") if active else None
            self.status = f"{len(active)} 个任务进行中" if active else "Codex 已打开 · 空闲"
            self.error = ""
            return events, active
        except (OSError, sqlite3.Error) as exc:
            self.status = "任务状态暂不可用"
            self.error = type(exc).__name__
            return [], []
