"""Read OS cursor/window state and a keyboard-activity timestamp only.

The hook never dereferences key data, converts text, stores keys, blocks input,
or sends events. Its dedicated message thread keeps the callback short.
"""
import ctypes
from ctypes import wintypes
import sys
import threading
import time
from dataclasses import dataclass

@dataclass
class InputState:
    cursor_visible: bool = True
    fullscreen: bool = False
    last_key_at: float = float('-inf')

def covers_monitor(window, monitor, tolerance=2):
    left,top,right,bottom=window;ml,mt,mr,mb=monitor
    return right>left and bottom>top and left<=ml+tolerance and top<=mt+tolerance and right>=mr-tolerance and bottom>=mb-tolerance

class WindowsInputObserver:
    def __init__(self):
        self.last_key_at=float('-inf');self.keyboard_ready=False;self.thread_id=None
        self.error="";self.thread=None;self._ready=threading.Event()
        self.enabled=sys.platform=='win32'
        if not self.enabled:return
        self.user=ctypes.WinDLL('user32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        class CursorInfo(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('flags',wintypes.DWORD),('hCursor',wintypes.HANDLE),('ptScreenPos',wintypes.POINT)]
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',wintypes.DWORD),('rcMonitor',wintypes.RECT),('rcWork',wintypes.RECT),('dwFlags',wintypes.DWORD)]
        self.CursorInfo,self.MonitorInfo=CursorInfo,MonitorInfo
        signatures={
            'GetCursorInfo':([ctypes.POINTER(CursorInfo)],wintypes.BOOL),
            'GetForegroundWindow':([],wintypes.HWND),
            'GetClassNameW':([wintypes.HWND,wintypes.LPWSTR,ctypes.c_int],ctypes.c_int),
            'GetClientRect':([wintypes.HWND,ctypes.POINTER(wintypes.RECT)],wintypes.BOOL),
            'ClientToScreen':([wintypes.HWND,ctypes.POINTER(wintypes.POINT)],wintypes.BOOL),
            'MonitorFromWindow':([wintypes.HWND,wintypes.DWORD],wintypes.HANDLE),
            'GetMonitorInfoW':([wintypes.HANDLE,ctypes.POINTER(MonitorInfo)],wintypes.BOOL),
            'CallNextHookEx':([wintypes.HANDLE,ctypes.c_int,ctypes.c_size_t,ctypes.c_ssize_t],ctypes.c_ssize_t),
            'UnhookWindowsHookEx':([wintypes.HANDLE],wintypes.BOOL),
            'GetMessageW':([ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT],ctypes.c_int),
            'PostThreadMessageW':([wintypes.DWORD,wintypes.UINT,ctypes.c_size_t,ctypes.c_ssize_t],wintypes.BOOL),
            'PeekMessageW':([ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT],wintypes.BOOL),
            'GetAsyncKeyState':([ctypes.c_int],ctypes.c_short),
        }
        for name,(args,result) in signatures.items():
            function=getattr(self.user,name);function.argtypes=args;function.restype=result
        self.kernel.GetCurrentThreadId.argtypes=[];self.kernel.GetCurrentThreadId.restype=wintypes.DWORD
        self.kernel.GetModuleHandleW.argtypes=[wintypes.LPCWSTR];self.kernel.GetModuleHandleW.restype=wintypes.HMODULE
        self.thread=threading.Thread(target=self._keyboard_loop,name='pet-input-activity',daemon=True)
        self.thread.start();self._ready.wait(.5)

    def _keyboard_loop(self):
        callback_type=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,ctypes.c_int,ctypes.c_size_t,ctypes.c_ssize_t)
        def keyboard(code,message,opaque_key_data):
            if code>=0 and message in (0x100,0x104):self.last_key_at=time.monotonic()
            return self.user.CallNextHookEx(None,code,message,opaque_key_data)
        callback=callback_type(keyboard)
        install=self.user.SetWindowsHookExW
        install.argtypes=[ctypes.c_int,callback_type,wintypes.HINSTANCE,wintypes.DWORD];install.restype=wintypes.HANDLE
        self.thread_id=self.kernel.GetCurrentThreadId()
        message=wintypes.MSG()
        self.user.PeekMessageW(ctypes.byref(message),None,0,0,0)  # Create queue before signalling readiness.
        hook=install(13,callback,self.kernel.GetModuleHandleW(None),0)
        if not hook:
            self.error='keyboard_hook_unavailable';self._ready.set();return
        self.keyboard_ready=True;self._ready.set()
        try:
            while self.user.GetMessageW(ctypes.byref(message),None,0,0)>0:pass
        finally:
            self.user.UnhookWindowsHookEx(hook);self.keyboard_ready=False

    def sample(self, now=None):
        if not self.enabled:return InputState()
        now=time.monotonic() if now is None else now
        # Held-key fallback also covers an unavailable hook; do not use the
        # unreliable globally-consumed "pressed since last call" bit.
        if any(self.user.GetAsyncKeyState(key)&0x8000 for key in range(8,256)):
            self.last_key_at=now
        cursor=self.CursorInfo();cursor.cbSize=ctypes.sizeof(cursor)
        visible=bool(self.user.GetCursorInfo(ctypes.byref(cursor)) and cursor.flags&1 and not cursor.flags&2)
        fullscreen=False;window=self.user.GetForegroundWindow()
        if window:
            name=ctypes.create_unicode_buffer(128);self.user.GetClassNameW(window,name,len(name))
            if name.value not in ('Progman','WorkerW','Shell_TrayWnd','Shell_SecondaryTrayWnd'):
                rect=wintypes.RECT();monitor=self.MonitorInfo();monitor.cbSize=ctypes.sizeof(monitor)
                if self.user.GetClientRect(window,ctypes.byref(rect)) and self.user.GetMonitorInfoW(self.user.MonitorFromWindow(window,2),ctypes.byref(monitor)):
                    a=wintypes.POINT(rect.left,rect.top);b=wintypes.POINT(rect.right,rect.bottom)
                    if self.user.ClientToScreen(window,ctypes.byref(a)) and self.user.ClientToScreen(window,ctypes.byref(b)):
                        m=monitor.rcMonitor;fullscreen=covers_monitor((a.x,a.y,b.x,b.y),(m.left,m.top,m.right,m.bottom))
        return InputState(visible,fullscreen,self.last_key_at)

    def close(self):
        if self.thread and self.thread.is_alive() and self.thread_id:
            self.user.PostThreadMessageW(self.thread_id,0x12,0,0)
            self.thread.join(1)
