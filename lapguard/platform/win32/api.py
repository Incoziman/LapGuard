"""ctypes declarations for the Win32 APIs LapGuard uses. Import only on Windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import uuid

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
powrprof = ctypes.WinDLL("powrprof")
wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)


class GUID(ctypes.Structure):
    _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)

    def __str__(self) -> str:
        return str(uuid.UUID(bytes_le=bytes(self)))


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON), ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON),
    ]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", w.DWORD), ("scanCode", w.DWORD), ("flags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [("PowerSetting", GUID), ("DataLength", w.DWORD), ("Data", ctypes.c_ubyte * 4)]


class DEV_BROADCAST_HDR(ctypes.Structure):
    _fields_ = [("dbch_size", w.DWORD), ("dbch_devicetype", w.DWORD), ("dbch_reserved", w.DWORD)]


class DEV_BROADCAST_DEVICEINTERFACE_W(ctypes.Structure):
    _fields_ = [("dbcc_size", w.DWORD), ("dbcc_devicetype", w.DWORD), ("dbcc_reserved", w.DWORD),
                ("dbcc_classguid", GUID), ("dbcc_name", w.WCHAR * 1)]


class DEV_BROADCAST_VOLUME(ctypes.Structure):
    _fields_ = [("dbcv_size", w.DWORD), ("dbcv_devicetype", w.DWORD), ("dbcv_reserved", w.DWORD),
                ("dbcv_unitmask", w.DWORD), ("dbcv_flags", w.WORD)]


class SYSTEM_POWER_STATUS(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                ("BatteryLifeTime", w.DWORD), ("BatteryFullLifeTime", w.DWORD)]


def _sig(fn, argtypes, restype):
    fn.argtypes = argtypes
    fn.restype = restype


PGUID = ctypes.POINTER(GUID)

_sig(user32.RegisterClassExW, [ctypes.POINTER(WNDCLASSEXW)], w.ATOM)
_sig(user32.UnregisterClassW, [w.LPCWSTR, w.HINSTANCE], w.BOOL)
_sig(user32.CreateWindowExW, [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID], w.HWND)
_sig(user32.DestroyWindow, [w.HWND], w.BOOL)
_sig(user32.DefWindowProcW, [w.HWND, w.UINT, w.WPARAM, w.LPARAM], LRESULT)
_sig(user32.GetMessageW, [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], w.BOOL)
_sig(user32.TranslateMessage, [ctypes.POINTER(w.MSG)], w.BOOL)
_sig(user32.DispatchMessageW, [ctypes.POINTER(w.MSG)], LRESULT)
_sig(user32.PostMessageW, [w.HWND, w.UINT, w.WPARAM, w.LPARAM], w.BOOL)
_sig(user32.PostThreadMessageW, [w.DWORD, w.UINT, w.WPARAM, w.LPARAM], w.BOOL)
_sig(user32.PostQuitMessage, [ctypes.c_int], None)
_sig(user32.SetWindowsHookExW, [ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD], w.HHOOK)
_sig(user32.UnhookWindowsHookEx, [w.HHOOK], w.BOOL)
_sig(user32.CallNextHookEx, [w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM], LRESULT)
_sig(user32.ToUnicodeEx, [w.UINT, w.UINT, ctypes.c_char_p, w.LPWSTR, ctypes.c_int, w.UINT, w.HKL], ctypes.c_int)
_sig(user32.GetKeyboardLayout, [w.DWORD], w.HKL)
_sig(user32.GetForegroundWindow, [], w.HWND)
_sig(user32.GetWindowThreadProcessId, [w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD)
_sig(user32.GetKeyState, [ctypes.c_int], w.SHORT)
_sig(user32.SetTimer, [w.HWND, ctypes.c_size_t, w.UINT, ctypes.c_void_p], ctypes.c_size_t)
_sig(user32.KillTimer, [w.HWND, ctypes.c_size_t], w.BOOL)
_sig(user32.RegisterPowerSettingNotification, [w.HANDLE, PGUID, w.DWORD], w.HANDLE)
_sig(user32.UnregisterPowerSettingNotification, [w.HANDLE], w.BOOL)
_sig(user32.RegisterDeviceNotificationW, [w.HANDLE, w.LPVOID, w.DWORD], w.HANDLE)
_sig(user32.UnregisterDeviceNotification, [w.HANDLE], w.BOOL)
_sig(user32.ShutdownBlockReasonCreate, [w.HWND, w.LPCWSTR], w.BOOL)
_sig(user32.ShutdownBlockReasonDestroy, [w.HWND], w.BOOL)

_sig(kernel32.GetModuleHandleW, [w.LPCWSTR], w.HMODULE)
_sig(kernel32.GetCurrentThreadId, [], w.DWORD)
_sig(kernel32.SetThreadExecutionState, [w.DWORD], w.DWORD)
_sig(kernel32.SetProcessShutdownParameters, [w.DWORD, w.DWORD], w.BOOL)
_sig(kernel32.GetSystemPowerStatus, [ctypes.POINTER(SYSTEM_POWER_STATUS)], w.BOOL)
_sig(kernel32.LocalFree, [w.HLOCAL], w.HLOCAL)

_sig(wtsapi32.WTSRegisterSessionNotification, [w.HWND, w.DWORD], w.BOOL)
_sig(wtsapi32.WTSUnRegisterSessionNotification, [w.HWND], w.BOOL)

_sig(powrprof.PowerGetActiveScheme, [w.HKEY, ctypes.POINTER(PGUID)], w.DWORD)
_sig(powrprof.PowerSetActiveScheme, [w.HKEY, PGUID], w.DWORD)
for _fn in (powrprof.PowerReadACValueIndex, powrprof.PowerReadDCValueIndex):
    _sig(_fn, [w.HKEY, PGUID, PGUID, PGUID, ctypes.POINTER(w.DWORD)], w.DWORD)
for _fn in (powrprof.PowerWriteACValueIndex, powrprof.PowerWriteDCValueIndex):
    _sig(_fn, [w.HKEY, PGUID, PGUID, PGUID, w.DWORD], w.DWORD)

# ---------------------------------------------------------------- constants
WM_CLOSE = 0x0010
WM_DESTROY = 0x0002
WM_QUERYENDSESSION = 0x0011
WM_ENDSESSION = 0x0016
WM_TIMER = 0x0113
WM_QUIT = 0x0012
WM_POWERBROADCAST = 0x0218
WM_DEVICECHANGE = 0x0219
WM_WTSSESSION_CHANGE = 0x02B1
WM_APP = 0x8000

WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WH_KEYBOARD_LL = 13

PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013

ENDSESSION_CRITICAL = 0x40000000
ENDSESSION_LOGOFF = 0x80000000

DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVTYP_VOLUME = 0x0002
DBT_DEVTYP_DEVICEINTERFACE = 0x0005
DEVICE_NOTIFY_WINDOW_HANDLE = 0x0000
DEVICE_NOTIFY_ALL_INTERFACE_CLASSES = 0x0004

WTS_SESSION_LOCK = 0x7
WTS_SESSION_UNLOCK = 0x8
NOTIFY_FOR_THIS_SESSION = 0

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002

WS_EX_TOOLWINDOW = 0x00000080

GUID_LIDSWITCH_STATE_CHANGE = "ba3e0f4d-b817-4094-a2d1-d56379e6a0f3"
GUID_ACDC_POWER_SOURCE = "5d3e9a59-e9d5-4b00-a6bd-ff34ff516548"


def win_error(code: int, what: str) -> OSError:
    return OSError(code, f"{what}: {ctypes.FormatError(code).strip()}")
