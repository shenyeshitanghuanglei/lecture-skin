"""Read and switch Windows' default playback device (plain ctypes COM, no extra deps).

The panel routes every app through the virtual cable by making "CABLE Input" the
default output while it is open, and puts the real speakers/headphones back when it
closes — otherwise all system sound would vanish into the cable once the server stops.

Switching uses IPolicyConfig, the undocumented-but-stable interface the Windows sound
control panel itself uses (Windows 7 … 11).
"""
from __future__ import annotations

import ctypes
from ctypes import POINTER, byref, c_int, c_uint, c_void_p, c_wchar_p
from dataclasses import dataclass


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
CLSID_PolicyConfigClient = "870AF99C-171D-4F9E-AF0D-E63DF40C2BC9"
IID_IPolicyConfig = "F8679F50-850A-41CF-9C72-430F290290C8"
PKEY_Device_FriendlyName = ("A45C254E-DF1C-4EFD-8020-67D146A850E0", 14)
CLSCTX_ALL = 0x17
eRender = 0
eConsole, eMultimedia, eCommunications = 0, 1, 2
DEVICE_STATE_ACTIVE = 1
VT_LPWSTR = 31

# Endpoints that are not something a person can hear.
_NOT_AUDIBLE = ("CABLE", "VB-AUDIO", "VIRTUAL", "TODESK", "VOICEMEETER")


@dataclass
class Endpoint:
    id: str
    name: str

    @property
    def is_cable(self) -> bool:
        return "CABLE INPUT" in self.name.upper()

    @property
    def audible(self) -> bool:
        up = self.name.upper()
        return not any(k in up for k in _NOT_AUDIBLE)


def _call(obj: c_void_p, index: int, *args, argtypes=()):
    vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
    fn = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(vtbl[index])
    return fn(obj, *args)


def _release(obj: c_void_p) -> None:
    if obj:
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        ctypes.WINFUNCTYPE(c_uint, c_void_p)(vtbl[2])(obj)


def _com_init() -> None:
    # S_FALSE / RPC_E_CHANGED_MODE just mean COM is already set up on this thread.
    ctypes.windll.ole32.CoInitializeEx(None, 0x2)


def _create(clsid: str, iid: str) -> c_void_p:
    obj = c_void_p()
    ctypes.oledll.ole32.CoCreateInstance(byref(GUID.parse(clsid)), None, CLSCTX_ALL, byref(GUID.parse(iid)), byref(obj))
    return obj


def _endpoint(dev: c_void_p) -> Endpoint:
    pid, store = c_wchar_p(), c_void_p()
    try:
        _call(dev, 5, byref(pid), argtypes=(POINTER(c_wchar_p),))  # IMMDevice::GetId
        dev_id = pid.value or ""
        ctypes.windll.ole32.CoTaskMemFree(pid)
        _call(dev, 4, 0, byref(store), argtypes=(c_uint, POINTER(c_void_p)))  # OpenPropertyStore(STGM_READ)
        key = PROPERTYKEY(GUID.parse(PKEY_Device_FriendlyName[0]), PKEY_Device_FriendlyName[1])
        pv = PROPVARIANT()
        _call(store, 5, byref(key), byref(pv), argtypes=(POINTER(PROPERTYKEY), POINTER(PROPVARIANT)))
        name = ctypes.wstring_at(pv.val) if pv.vt == VT_LPWSTR and pv.val else ""
        ctypes.oledll.ole32.PropVariantClear(byref(pv))
        return Endpoint(dev_id, name)
    finally:
        _release(store)


def list_outputs() -> list[Endpoint]:
    """Active playback endpoints."""
    _com_init()
    enum, coll = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator), c_void_p()
    out: list[Endpoint] = []
    try:
        _call(enum, 3, eRender, DEVICE_STATE_ACTIVE, byref(coll), argtypes=(c_int, c_uint, POINTER(c_void_p)))
        n = c_uint()
        _call(coll, 3, byref(n), argtypes=(POINTER(c_uint),))
        for i in range(n.value):
            dev = c_void_p()
            _call(coll, 4, i, byref(dev), argtypes=(c_uint, POINTER(c_void_p)))
            try:
                out.append(_endpoint(dev))
            finally:
                _release(dev)
    finally:
        _release(coll)
        _release(enum)
    return out


def default_output() -> Endpoint | None:
    _com_init()
    enum, dev = _create(CLSID_MMDeviceEnumerator, IID_IMMDeviceEnumerator), c_void_p()
    try:
        _call(enum, 4, eRender, eMultimedia, byref(dev), argtypes=(c_int, c_int, POINTER(c_void_p)))
        return _endpoint(dev)
    except OSError:
        return None  # no playback device at all
    finally:
        _release(dev)
        _release(enum)


def set_default_output(endpoint_id: str) -> None:
    """Make this endpoint the default for all roles (what the Sound control panel does)."""
    _com_init()
    pc = _create(CLSID_PolicyConfigClient, IID_IPolicyConfig)
    try:
        for role in (eConsole, eMultimedia, eCommunications):
            _call(pc, 13, c_wchar_p(endpoint_id), role, argtypes=(c_wchar_p, c_int))  # SetDefaultEndpoint
    finally:
        _release(pc)
