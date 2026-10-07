"""Foreground the installed Codex desktop; never send input or task prompts."""
import ctypes
from ctypes import wintypes
import sys
import uuid
import psutil

def thread_url(thread_id):
    # URI contains only a validated existing thread UUID, no prompt parameters.
    return 'codex://threads/'+str(uuid.UUID(thread_id))

def codex_windows():
    if sys.platform!='win32':return []
    pids=set()
    for process in psutil.process_iter(['pid','name','exe']):
        try:
            exe=(process.info.get('exe') or '').lower().replace('\\','/')
            if ('/openai.codex_' in exe and exe.endswith('/chatgpt.exe')) or (process.info.get('name') or '').lower() in ('codex desktop.exe','codexdesktop.exe'):
                pids.add(process.pid)
        except (psutil.Error,OSError):pass
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)];user.GetWindowThreadProcessId.restype=wintypes.DWORD
    user.IsWindowVisible.argtypes=[wintypes.HWND];user.IsWindowVisible.restype=wintypes.BOOL
    callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,ctypes.c_ssize_t)
    windows=[]
    def visit(window,unused):
        pid=wintypes.DWORD();user.GetWindowThreadProcessId(window,ctypes.byref(pid))
        if pid.value in pids and user.IsWindowVisible(window):windows.append(window)
        return True
    callback=callback_type(visit)
    user.EnumWindows.argtypes=[callback_type,ctypes.c_ssize_t];user.EnumWindows.restype=wintypes.BOOL
    user.EnumWindows(callback,0)
    return windows

def focus_codex():
    windows=codex_windows()
    if not windows:return False
    user=ctypes.WinDLL('user32',use_last_error=True)
    for name,args,result in (
        ('IsIconic',[wintypes.HWND],wintypes.BOOL),
        ('ShowWindow',[wintypes.HWND,ctypes.c_int],wintypes.BOOL),
        ('SetForegroundWindow',[wintypes.HWND],wintypes.BOOL),
        ('GetForegroundWindow',[],wintypes.HWND),
    ):
        api=getattr(user,name);api.argtypes=args;api.restype=result
    if user.GetForegroundWindow() in windows:return True
    for window in windows:
        if user.IsIconic(window):user.ShowWindow(window,9)
        user.SetForegroundWindow(window)
        if user.GetForegroundWindow() in windows:return True
    return False
