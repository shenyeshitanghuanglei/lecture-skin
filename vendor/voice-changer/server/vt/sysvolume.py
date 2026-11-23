"""Mirror the Windows volume keys onto our output (voice-translater addition, read-only).

Setup that "just works" on Windows: the default output is CABLE Input, so the browser
(and its per-app routing quirks) needs no configuration, and we play the converted voice
on the speakers. But then the volume keys/taskbar slider change CABLE's volume, which
never reaches the speakers. So we *read* the default endpoint's master volume and mute
(Core Audio IAudioEndpointVolume, via plain ctypes COM) and apply them ourselves.

Only reads system state; never changes any Windows setting.
"""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import POINTER, byref, c_float, c_int, c_uint, c_void_p, c_wchar_p

import logging

logger = logging.getLogger(__name__)


class GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, s: str) -> "GUID":
        g = cls()
        ctypes.oledll.ole32.CLSIDFromString(c_wchar_p("{" + s + "}"), byref(g))
        return g


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_uint32)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                ("val", c_void_p), ("pad", c_void_p)]


CLSID_MMDeviceEnumerator = "BCDE0395-E52F-467C-8E3D-C4579291692E"
IID_IMMDeviceEnumerator = "A95664D2-9614-4F35-A746-DE8DB63617E6"
IID_IAudioEndpointVolume = "5CDF2C82-841E-4546-9722-0CF74078229A"
PKEY_Device_FriendlyName = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)
CLSCTX_ALL = 0x17
eRender, eMultimedia = 0, 1
VT_LPWSTR = 31


def _call(obj: c_void_p, index: int, *args, argtypes=()):
    """Call method #index of a COM interface pointer."""
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def _release(obj: c_void_p) -> None:
    if obj:
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        ctypes.WINFUNCTYPE(c_uint, c_void_p)(vtbl[2])(obj)


def read_default_output() -> tuple[str, float, bool]:
    """(friendly name, master volume 0..1, muted) of Windows' default playback device.
    The calling thread must have COM initialised."""
    enum = c_void_p()
    ctypes.oledll.ole32.CoCreateInstance(
        byref(GUID.parse(CLSID_MMDeviceEnumerator)), None, CLSCTX_ALL,
        byref(GUID.parse(IID_IMMDeviceEnumerator)), byref(enum))
    dev, store, vol = c_void_p(), c_void_p(), c_void_p()
    try:
        _call(enum, 4, eRender, eMultimedia, byref(dev), argtypes=(c_int, c_int, POINTER(c_void_p)))
        # name: IMMDevice::OpenPropertyStore(STGM_READ) -> IPropertyStore::GetValue
        _call(dev, 4, 0, byref(store), argtypes=(c_uint, POINTER(c_void_p)))
        key = PROPERTYKEY(GUID.parse(PKEY_Device_FriendlyName[0]), PKEY_Device_FriendlyName[1])
        pv = PROPVARIANT()
        _call(store, 5, byref(key), byref(pv), argtypes=(POINTER(PROPERTYKEY), POINTER(PROPVARIANT)))
        name = ctypes.wstring_at(pv.val) if pv.vt == VT_LPWSTR and pv.val else ""
        ctypes.oledll.ole32.PropVariantClear(byref(pv))
        # volume: IMMDevice::Activate(IAudioEndpointVolume)
        _call(dev, 3, byref(GUID.parse(IID_IAudioEndpointVolume)), CLSCTX_ALL, None, byref(vol),
              argtypes=(POINTER(GUID), c_uint, c_void_p, POINTER(c_void_p)))
        level, mute = c_float(), c_int()
        _call(vol, 9, byref(level), argtypes=(POINTER(c_float),))   # GetMasterVolumeLevelScalar
        _call(vol, 15, byref(mute), argtypes=(POINTER(c_int),))     # GetMute
        return name, float(level.value), bool(mute.value)
    finally:
        for o in (vol, store, dev, enum):
            _release(o)


class SystemVolume:
    """Background poller; `factor()` is the gain to apply to our output."""

    def __init__(self, interval: float = 0.25):
        self.interval = interval
        self.name = ""
        self.level = 1.0
        self.muted = False
        self.ok = False
        if sys.platform == "win32":
            threading.Thread(target=self._run, name="vt-sysvolume", daemon=True).start()

    def _run(self) -> None:
        ctypes.oledll.ole32.CoInitializeEx(None, 0x2)  # apartment-threaded, this thread only
        warned = False
        while True:
            try:
                self.name, self.level, self.muted = read_default_output()
                self.ok = True
                warned = False
            except Exception as e:
                self.ok = False
                if not warned:
                    logger.warning(f"[sysvolume] cannot read system volume: {e}")
                    warned = True
            time.sleep(self.interval)

    @property
    def default_is_cable(self) -> bool:
        return "CABLE" in self.name.upper()

    def factor(self) -> float:
        """Only when the volume keys control the virtual cable do we need to apply them
        ourselves; if the default device is the speakers Windows already does it."""
        if not self.ok or not self.default_is_cable:
            return 1.0
        if self.muted:
            return 0.0
        # Windows' slider is roughly perceptual; square it so 50% sounds about half as loud.
        return self.level ** 2


_instance: SystemVolume | None = None
_init_lock = threading.Lock()


def get() -> SystemVolume:
    global _instance
    if _instance is None:
        with _init_lock:
            if _instance is None:
                _instance = SystemVolume()
    return _instance
