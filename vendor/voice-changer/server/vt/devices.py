"""Remember audio devices by name (voice-translater addition).

PortAudio device indices shift whenever a device is plugged/unplugged or Windows
re-enumerates (e.g. after a reboot), so a stored index can silently point at a
different device. We persist (name, hostApi) and resolve to an index right before
audio starts.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

import sounddevice as sd

PREFS_FILE = "vt_devices.json"
FOLLOW_DEFAULT = "__system_default__"  # output pref: always use Windows' default output


@dataclass
class DevRef:
    name: str
    hostApi: str

    def matches(self, d) -> bool:
        return d.name == self.name and d.hostAPI == self.hostApi


def load_prefs() -> dict[str, DevRef]:
    try:
        with open(PREFS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        return {k: DevRef(**v) for k, v in raw.items() if k in ("input", "output")}
    except (OSError, ValueError, TypeError):
        return {}


def save_prefs(prefs: dict[str, DevRef]) -> None:
    tmp = PREFS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: v.__dict__ for k, v in prefs.items()}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PREFS_FILE)


def _wasapi_default_output() -> int | None:
    for h in sd.query_hostapis():
        if "WASAPI" in h["name"] and h["default_output_device"] >= 0:
            return int(h["default_output_device"])
    return None


@dataclass
class Resolution:
    input_id: int | None
    output_id: int | None
    warnings: list[str]


def resolve(inputs, outputs, prefs: dict[str, DevRef]) -> Resolution:
    """Map remembered devices to current indices, with sensible fallbacks."""
    warnings: list[str] = []

    inp = prefs.get("input")
    input_id = next((d.index for d in inputs if inp and inp.matches(d)), None)
    if input_id is None:
        cable = next((d for d in inputs if "CABLE Output" in d.name and "WASAPI" in d.hostAPI), None)
        if cable is not None:
            input_id = cable.index
            if inp is not None:
                warnings.append(f"找不到输入设备「{inp.name}」，已改用「{cable.name}」")

    out = prefs.get("output")
    follow = out is not None and out.name == FOLLOW_DEFAULT
    output_id = None if follow else next((d.index for d in outputs if out and out.matches(d)), None)
    if output_id is None:
        default_id = _wasapi_default_output()
        fallback = next((d for d in outputs if d.index == default_id and _is_real_output(d)), None)
        if fallback is None:
            # Windows often makes CABLE Input the default once a headset is unplugged;
            # never output there (feedback loop) — pick a physical WASAPI device instead.
            real = [d for d in outputs if "WASAPI" in d.hostAPI and _is_real_output(d)]
            real.sort(key=lambda d: "VIRTUAL" in d.name.upper())
            fallback = real[0] if real else None
        if fallback is not None:
            output_id = fallback.index
            if out is not None and not follow:
                warnings.append(f"输出设备「{out.name}」未连接，已改用「{fallback.name}」")
    return Resolution(input_id, output_id, warnings)


def _is_real_output(d) -> bool:
    return "CABLE" not in d.name.upper()


def ref_for(devs, index: int) -> DevRef | None:
    d = next((d for d in devs if d.index == index), None)
    return DevRef(d.name, d.hostAPI) if d is not None else None
