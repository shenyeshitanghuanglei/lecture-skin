"""Control-panel REST API, mounted at /vt/api (voice-translater addition).

Thin, JSON-only layer over VoiceChangerManager so the panel never has to know
about the upstream settings quirks (form-encoded values, chunk units, etc).
"""
from __future__ import annotations

import asyncio
import math
import os
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, FiniteFloat, TypeAdapter, ValidationError

from const import PitchExtractorType, UPLOAD_DIR
from restapi.mods.FileUploader import sanitize_filename
from voice_changer.Local.AudioDeviceList import list_audio_device
from voice_changer.utils.LoadModelParams import LoadModelParamFile, LoadModelParams
from vt import devices as devprefs
from vt import onboarding
from vt.clone_api import audio_gpu_lock, training_active

import sounddevice as sd

import logging

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
PANEL_DIST = Path(os.environ.get("VT_PANEL_DIST", PROJECT_ROOT / "panel" / "dist"))

# serverReadChunkSize is in units of 128 samples
LATENCY_PRESETS: dict[str, dict[str, Any]] = {
    # crossfade 0.1 s + 1 s context measured clearly closer to offline quality than 0.05 s / 0.5 s
    "low": {"label": "低延迟", "serverReadChunkSize": 24, "crossFadeOverlapSize": 0.08, "extraConvertSize": 1.0},  # 64 ms @48k
    "balanced": {"label": "均衡", "serverReadChunkSize": 32, "crossFadeOverlapSize": 0.1, "extraConvertSize": 1.0},  # 85 ms
    "stable": {"label": "稳定", "serverReadChunkSize": 64, "crossFadeOverlapSize": 0.1, "extraConvertSize": 1.0},   # 171 ms
}

PitchHz = Annotated[FiniteFloat, Field(ge=0, le=600)]
Gain = Annotated[FiniteFloat, Field(ge=0, le=4)]
SETTINGS = {key: TypeAdapter(value_type) for key, value_type in {
    "tran": Annotated[int, Field(ge=-24, le=24)],
    "autoPitch": bool,
    "autoPitchTargetHz": PitchHz,
    "passThrough": bool,
    "serverInputAudioGain": Gain,
    "serverOutputAudioGain": Gain,
    "silentThreshold": Annotated[int, Field(ge=-140, le=0)],
    "indexRatio": Annotated[FiniteFloat, Field(ge=0, le=1)],
    "protect": Annotated[FiniteFloat, Field(ge=0, le=0.5)],
    "formantShift": Annotated[FiniteFloat, Field(ge=-24, le=24)],
    "f0Detector": PitchExtractorType,
    "gpu": int,
}.items()}


class SettingsBody(BaseModel):
    values: dict[str, Any]


class SelectBody(BaseModel):
    slot: int


class VoicePatch(BaseModel):
    name: str | None = None
    targetPitchHz: PitchHz | None = None
    indexRatio: FiniteFloat | None = None  # how strongly the .index pulls timbre toward the target (0-1)
    protect: FiniteFloat | None = None  # <0.5 shields consonants/breaths from the index; 0.5 = off


class DevicesBody(BaseModel):
    input: int
    output: int


class PresetBody(BaseModel):
    preset: str


class OpenUrlBody(BaseModel):
    target: str


def _db(rms: float) -> float:
    return round(20 * math.log10(rms), 1) if rms > 1e-7 else -140.0


JS_BOOL_SETTINGS = {"passThrough"}  # upstream setters that parse "true"/"false"


def _to_form(key: str, val: Any) -> Any:
    if isinstance(val, bool):
        if key in JS_BOOL_SETTINGS:
            return "true" if val else "false"
        return int(val)
    return val


class VtApi:
    def __init__(self, manager, *, clone_enabled: bool = False):
        self.m = manager
        self.clone_enabled = clone_enabled
        self.warnings: list[str] = []
        self.system_default_out: str | None = None
        # FastAPI runs sync routes on a thread pool; PortAudio (re)init is not thread-safe.
        self._audio_lock = audio_gpu_lock
        self._env_installer = onboarding.EnvInstaller()
        self._check_system_default()
        self._apply_quality_defaults()
        self._bypass()  # system audio must reach the speakers even before conversion starts
        self.router = APIRouter(prefix="/vt/api")
        r = self.router
        r.add_api_route("/status", self.status, methods=["GET"])
        r.add_api_route("/start", self.start, methods=["POST"])
        r.add_api_route("/stop", self.stop, methods=["POST"])
        r.add_api_route("/settings", self.set_settings, methods=["POST"])
        r.add_api_route("/latency", self.set_latency, methods=["POST"])
        r.add_api_route("/voices", self.voices, methods=["GET"])
        r.add_api_route("/voices/select", self.select_voice, methods=["POST"])
        r.add_api_route("/voices/import", self.import_voice, methods=["POST"])
        r.add_api_route("/voices/{slot}", self.patch_voice, methods=["PATCH"])
        r.add_api_route("/voices/{slot}", self.delete_voice, methods=["DELETE"])
        r.add_api_route("/voices/{slot}/icon", self.voice_icon, methods=["GET"])
        r.add_api_route("/devices", self.devices, methods=["GET"])
        r.add_api_route("/devices", self.set_devices, methods=["POST"])
        r.add_api_route("/open-sound-settings", self.open_sound_settings, methods=["POST"])
        r.add_api_route("/onboarding/env", self.onboarding_env, methods=["GET"])
        r.add_api_route("/onboarding/ensure-env", self.onboarding_ensure_env, methods=["POST"])
        r.add_api_route("/onboarding/open-url", self.onboarding_open_url, methods=["POST"])
        r.add_api_route("/debug/stacks", self.debug_stacks, methods=["GET"])
        r.add_api_route("/diagnostics", self.export_diagnostics, methods=["POST"])

    # ---------- helpers ----------
    PROTECT_DEFAULT = 0.33
    PROTECT_UPSTREAM = 0.5  # = protection off; every slot imported before 2026-11-22 carries it
    PROTECT_MIGRATED = ".protect-default-migrated"

    def _apply_quality_defaults(self) -> None:
        """One-time upgrade of settings saved by older versions (stored_setting.json and
        each voice's params.json) to the current quality defaults."""
        try:
            if not self.s.forceFp32:
                logger.info("[quality] switching inference to fp32")
                self._update("forceFp32", 1)
            marker = Path(self.m.params.model_dir) / self.PROTECT_MIGRATED
            if not marker.exists():
                # Runs once, so a voice the user later sets back to 0.5 (protection off) keeps it.
                msm = self.m.modelSlotManager
                for slot in self._slots():
                    if getattr(slot, "defaultProtect", None) == self.PROTECT_UPSTREAM:
                        msm.update_model_info(slot.slotIndex, "defaultProtect", self.PROTECT_DEFAULT)
                        if slot.slotIndex == self.s.modelSlotIndex:
                            self._update("protect", self.PROTECT_DEFAULT)
                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.touch()
        except Exception as e:  # never block startup on this
            logger.warning(f"[quality] could not apply quality defaults: {e}")

    @property
    def s(self):
        return self.m.settings

    def _update(self, key: str, val: Any):
        self.m.update_settings(key, _to_form(key, val))

    def _current_preset(self) -> str | None:
        for k, p in LATENCY_PRESETS.items():
            if (p["serverReadChunkSize"] == self.s.serverReadChunkSize
                    and abs(p["crossFadeOverlapSize"] - self.s.crossFadeOverlapSize) < 1e-6
                    and abs(p["extraConvertSize"] - self.s.extraConvertSize) < 1e-6):
                return k
        return None

    def _voice(self, slot) -> dict:
        return {
            "slot": slot.slotIndex,
            "name": slot.name,
            "sampleRate": getattr(slot, "samplingRate", None),
            "hasIndex": bool(getattr(slot, "indexFile", "")),
            "targetPitchHz": getattr(slot, "targetPitchHz", 0.0),
            "indexRatio": getattr(slot, "defaultIndexRatio", 0.0),
            "protect": getattr(slot, "defaultProtect", 0.5),
            "version": getattr(slot, "version", ""),
            "credit": slot.credit,
            "termsOfUseUrl": slot.termsOfUseUrl,
            "hasIcon": bool(slot.iconFile),
        }

    def _slots(self):
        return [x for x in self.m.modelSlotManager.getAllSlotInfo(reload=True) if x.voiceChangerType is not None]

    # ---------- status ----------
    def status(self):
        s = self.s
        live = dict(getattr(self.m.server_audio, "live", {}))
        fresh = time.time() - live.get("updated", 0) < 1.5
        vcmodel = getattr(self.m.vc, "vcmodel", None)
        ap = getattr(vcmodel, "auto_pitch", None)
        src_hz = ap.source_median_hz() if ap is not None else None
        slot = self.m.modelSlotManager.get_slot_info(s.modelSlotIndex)
        block_ms = s.serverReadChunkSize * 128 / (s.serverInputAudioSampleRate or 48000) * 1000
        return {
            "cloneEnabled": self.clone_enabled,
            "running": bool(s.serverAudioStated and self.m.server_audio.running),
            "passThrough": bool(s.passThrough),
            # conversion off but original audio still flowing CABLE -> speakers
            "originalAudio": bool(not s.serverAudioStated and self.m.server_audio.running and self.m.server_audio.bypass),
            "voice": self._voice(slot) if slot is not None and slot.voiceChangerType else None,
            "pitch": {
                "auto": bool(s.autoPitch),
                "tran": s.tran,
                "targetHz": s.autoPitchTargetHz,
                "sourceHz": round(src_hz, 1) if src_hz else None,
            },
            "audio": {
                "inputDevice": s.serverInputDeviceId,
                "outputDevice": s.serverOutputDeviceId,
                "sampleRate": s.serverAudioSampleRate,
                "inDb": _db(live.get("inVol", 0.0)) if fresh else None,
                "inputGain": s.serverInputAudioGain,
                "outputGain": s.serverOutputAudioGain,
                "matchGain": round(live.get("matchGain", 1.0), 2),
                "xruns": live.get("inOverflows", 0) + live.get("outUnderflows", 0),
            },
            "perf": {
                "blockMs": round(block_ms, 1),
                "procMs": round(live.get("procMs", 0.0), 1) if fresh else None,
                "load": round(live.get("load", 0.0), 3) if fresh else None,
                "peakLoad": round(live.get("peakLoad", 0.0), 3) if fresh else None,
                # algorithmic estimate: one block of buffering + processing + crossfade
                "latencyMs": round(block_ms + live.get("procMs", 0.0) + s.crossFadeOverlapSize * 1000, 0) if fresh else None,
                "preset": self._current_preset(),
                "presets": {k: v["label"] for k, v in LATENCY_PRESETS.items()},
            },
            "gpu": {"selected": s.gpu, "devices": self.m.devices},
            "warnings": self.warnings,
            "system": self._system_info(),
        }

    def _system_info(self) -> dict:
        from vt import sysvolume
        sv = sysvolume.get()
        return {
            "defaultOutput": sv.name or self.system_default_out,
            "defaultIsCable": sv.default_is_cable if sv.ok else bool(self.system_default_out and "CABLE" in self.system_default_out.upper()),
            "volume": round(sv.level, 2) if sv.ok else None,
            "muted": sv.muted,
            "mirrored": sv.ok and sv.default_is_cable,  # volume keys applied by us
        }

    # ---------- device resolution ----------
    def _bypass(self) -> None:
        """Conversion off: keep CABLE -> speakers flowing as untouched original audio.
        Windows' default output is the cable, so without this every app goes silent."""
        if self.s.serverAudioStated:
            return
        try:
            self._resolve_devices()
            self.m.server_audio.start(bypass=True)
        except Exception as e:
            logger.warning(f"original-audio passthrough unavailable: {e}")

    def _refresh_portaudio(self):
        """Re-scan devices (hot-plug). Closes the passthrough stream; callers reopen it."""
        if not self.s.serverAudioStated:
            self.m.server_audio.stop()
            self.m.server_audio.reinit_portaudio()

    def _check_system_default(self) -> None:
        """Windows volume keys control the *default* output. If that is the virtual cable
        (Windows switches to it when a headset is unplugged), they can't reach what we play."""
        try:
            h = next(x for x in sd.query_hostapis() if "WASAPI" in x["name"])
            name = sd.query_devices(h["default_output_device"])["name"]
            self.system_default_out = name
        except Exception:
            self.system_default_out = None

    def _resolve_devices(self) -> tuple[int, int]:
        self._refresh_portaudio()
        self._check_system_default()
        inputs, outputs = list_audio_device()
        prefs = devprefs.load_prefs()
        if not prefs:
            # First run (or upgraded from index-only settings): trust current ids only if they still
            # point at something sensible, otherwise resolve() falls back to CABLE + system default.
            for key, devs, idx in (("input", inputs, self.s.serverInputDeviceId), ("output", outputs, self.s.serverOutputDeviceId)):
                ref = devprefs.ref_for(devs, idx)
                ok = ref is not None and (
                    "CABLE Output" in ref.name if key == "input" else "CABLE" not in ref.name.upper()
                )
                if ok:
                    prefs[key] = ref
        res = devprefs.resolve(inputs, outputs, prefs)
        if res.input_id is None:
            raise HTTPException(400, "找不到虚拟声卡「CABLE Output」：请确认 VB-CABLE 已安装，或在设置里选择输入设备")
        if res.output_id is None:
            raise HTTPException(400, "找不到可用的输出设备：请插上耳机/音箱，或在设置里选择输出设备")
        if not devprefs.load_prefs():
            devprefs.save_prefs({"input": devprefs.ref_for(inputs, res.input_id), "output": devprefs.ref_for(outputs, res.output_id)})
        self.warnings = res.warnings
        if res.input_id != self.s.serverInputDeviceId:
            self._update("serverInputDeviceId", res.input_id)
        if res.output_id != self.s.serverOutputDeviceId:
            self._update("serverOutputDeviceId", res.output_id)
        if self.s.serverAudioSampleRate != 48000:
            self._update("serverAudioSampleRate", 48000)
        return res.input_id, res.output_id

    def start(self):
        with self._audio_lock:
            return self._start()

    def _start(self):
        if self.s.serverAudioStated and self.m.server_audio.running:
            return self.status()
        if training_active():
            raise HTTPException(409, "声音克隆正在使用显卡（处理素材或训练中），完成或取消后才能开始变声")
        slot = self.m.modelSlotManager.get_slot_info(self.s.modelSlotIndex)
        if slot is None or slot.voiceChangerType is None:
            raise HTTPException(400, "请先在声音库里选择一个声音")
        if self.s.serverAudioStated:
            self._update("serverAudioStated", 0)
        try:
            self._resolve_devices()
            if not self.s.enableServerAudio:
                self._update("enableServerAudio", 1)
            self._update("serverAudioStated", 1)
        except Exception:
            self._stop()
            raise
        if not self.m.server_audio.running:
            self._stop()
            raise HTTPException(503, "无法打开音频设备，请检查输入与输出设备后重试")
        return self.status()

    def stop(self):
        with self._audio_lock:
            return self._stop()

    def _stop(self):
        self._update("serverAudioStated", 0)
        self._bypass()
        return self.status()

    def set_settings(self, body: SettingsBody):
        bad = [k for k in body.values if k not in SETTINGS]
        if bad:
            raise HTTPException(400, f"不支持的设置项: {', '.join(bad)}")
        values = {}
        for key, value in body.values.items():
            try:
                values[key] = SETTINGS[key].validate_python(value)
            except ValidationError as e:
                raise HTTPException(400, f"设置项 {key} 的值无效") from e
        if "gpu" in values and values["gpu"] not in {d["id"] for d in self.m.devices}:
            raise HTTPException(400, "显卡不存在")
        with self._audio_lock:
            for key, value in values.items():
                self._update(key, value)
        return self.status()

    def set_latency(self, body: PresetBody):
        p = LATENCY_PRESETS.get(body.preset)
        if p is None:
            raise HTTPException(400, f"未知档位: {body.preset}")
        with self._audio_lock:
            self._update("crossFadeOverlapSize", p["crossFadeOverlapSize"])
            self._update("extraConvertSize", p["extraConvertSize"])
            self._update("serverReadChunkSize", p["serverReadChunkSize"])
        return self.status()

    # ---------- voices ----------
    def voices(self):
        return {"current": self.s.modelSlotIndex, "voices": [self._voice(x) for x in self._slots()]}

    def select_voice(self, body: SelectBody):
        slot = self.m.modelSlotManager.get_slot_info(body.slot)
        if slot is None or slot.voiceChangerType is None:
            raise HTTPException(404, "声音不存在")
        self._update("modelSlotIndex", body.slot)
        return self.voices()

    async def import_voice(
        self,
        model: UploadFile = File(...),
        index: UploadFile | None = File(None),
        name: str = Form(""),
        targetPitchHz: float = Form(0.0),
    ):
        msm = self.m.modelSlotManager
        try:
            targetPitchHz = TypeAdapter(PitchHz).validate_python(targetPitchHz)
        except ValidationError as e:
            raise HTTPException(400, "目标音高必须是 0 到 600 Hz") from e
        slot = msm.allocate_slot()
        files: list[LoadModelParamFile] = []
        clean_names: dict[str, str] = {}
        staged_paths: list[str] = []

        def _stage(up: UploadFile, fname: str) -> str:
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            staged = f"{uuid.uuid4().hex[:8]}_{fname}"
            dst = os.path.join(UPLOAD_DIR, staged)
            with open(dst, "wb") as f:
                shutil.copyfileobj(up.file, f)
            return staged

        try:
            for up, kind, exts in ((model, "rvcModel", (".pth", ".safetensors", ".onnx")), (index, "rvcIndex", (".index",))):
                if up is None:
                    continue
                fname = sanitize_filename(os.path.basename(up.filename or ""))
                if not fname.lower().endswith(exts):
                    raise HTTPException(400, f"文件类型不对: {up.filename}（需要 {'/'.join(exts)}）")
                staged = await asyncio.to_thread(_stage, up, fname)
                staged_paths.append(os.path.join(UPLOAD_DIR, staged))
                clean_names[staged] = fname
                files.append(LoadModelParamFile(name=staged, kind=kind, dir=""))
            params = LoadModelParams(voiceChangerType="RVC", slot=slot, files=files, params={"clean_names": clean_names})
            # torch>=2.6 loads .pth with weights_only=True: a checkpoint carrying code is rejected.
            await self.m.load_model(params)
        except HTTPException:
            msm.release_slot(slot)
            for p in staged_paths:
                if os.path.exists(p):
                    os.unlink(p)
            raise
        except Exception as e:
            logger.exception(e)
            msm.release_slot(slot)
            for p in staged_paths:
                if os.path.exists(p):
                    os.unlink(p)
            msm.getAllSlotInfo(reload=True)
            raise HTTPException(400, f"模型导入失败（文件损坏或不是 RVC 模型，或包含不安全内容）: {e}")
        msm.getAllSlotInfo(reload=True)
        if name:
            msm.update_model_info(slot, "name", name)
        if targetPitchHz:
            msm.update_model_info(slot, "targetPitchHz", float(targetPitchHz))
        return {"slot": slot, **self.voices()}

    def patch_voice(self, slot: int, body: VoicePatch):
        info = self.m.modelSlotManager.get_slot_info(slot)
        if info is None or info.voiceChangerType is None:
            raise HTTPException(404, "声音不存在")
        if body.name is not None:
            self.m.modelSlotManager.update_model_info(slot, "name", body.name.strip() or info.name)
        if body.targetPitchHz is not None:
            self.m.modelSlotManager.update_model_info(slot, "targetPitchHz", float(body.targetPitchHz))
            if slot == self.s.modelSlotIndex and body.targetPitchHz > 0:
                self._update("autoPitchTargetHz", float(body.targetPitchHz))
        if body.indexRatio is not None:
            r = min(1.0, max(0.0, float(body.indexRatio)))
            self.m.modelSlotManager.update_model_info(slot, "defaultIndexRatio", r)
            if slot == self.s.modelSlotIndex:
                self._update("indexRatio", r)
        if body.protect is not None:
            p = min(0.5, max(0.0, float(body.protect)))
            self.m.modelSlotManager.update_model_info(slot, "defaultProtect", p)
            if slot == self.s.modelSlotIndex:
                self._update("protect", p)
        return self.voices()

    def delete_voice(self, slot: int):
        if slot == self.s.modelSlotIndex:
            raise HTTPException(400, "正在使用的声音不能删除，请先切换到别的声音")
        info = self.m.modelSlotManager.get_slot_info(slot)
        if info is None or info.voiceChangerType is None:
            raise HTTPException(404, "声音不存在")
        shutil.rmtree(os.path.join(self.m.params.model_dir, str(slot)), ignore_errors=True)
        self.m.modelSlotManager.getAllSlotInfo(reload=True)
        return self.voices()

    def voice_icon(self, slot: int):
        info = self.m.modelSlotManager.get_slot_info(slot)
        if info is None or not info.iconFile:
            raise HTTPException(404)
        path = os.path.join(self.m.params.model_dir, str(slot), os.path.basename(info.iconFile))
        if not os.path.isfile(path):
            raise HTTPException(404)
        return FileResponse(path)

    # ---------- devices ----------
    def devices(self, refresh: bool = False):
        with self._audio_lock:
            if refresh:
                self._bypass()
            return self._devices()

    def _devices(self):
        if not self.s.serverAudioStated:
            self._check_system_default()
        inputs, outputs = list_audio_device()
        prefs = devprefs.load_prefs()
        res = devprefs.resolve(inputs, outputs, prefs)

        def pack(devs):
            # WASAPI first: lowest latency in shared mode; keep others for odd hardware.
            out = [
                {"id": d.index, "name": d.name, "hostApi": d.hostAPI, "isCable": "CABLE" in d.name.upper()}
                for d in devs
            ]
            out.sort(key=lambda d: (0 if "WASAPI" in d["hostApi"] else 1, d["name"]))
            return out

        return {
            "inputs": pack(inputs),
            "outputs": pack(outputs),
            "selected": {
                "input": res.input_id if res.input_id is not None else -1,
                "output": -2 if (prefs.get("output") or devprefs.DevRef("", "")).name == devprefs.FOLLOW_DEFAULT
                else (res.output_id if res.output_id is not None else -1),
            },
            "systemDefaultOutput": self.system_default_out,
            "warnings": res.warnings,
        }

    def set_devices(self, body: DevicesBody):
        with self._audio_lock:
            return self._set_devices(body)

    def _set_devices(self, body: DevicesBody):
        was_running = bool(self.s.serverAudioStated)
        inputs, outputs = list_audio_device()
        inp = devprefs.ref_for(inputs, body.input)
        if body.output == -2:  # follow Windows' default output
            out = devprefs.DevRef(devprefs.FOLLOW_DEFAULT, "")
            res = devprefs.resolve(inputs, outputs, {"output": out})
            body.output = res.output_id if res.output_id is not None else -1
        else:
            out = devprefs.ref_for(outputs, body.output)
        if inp is None or out is None or body.output < 0:
            raise HTTPException(400, "设备不存在，可能刚被拔掉了，请刷新后重选")
        if "CABLE" in out.name.upper():
            # Browser -> CABLE -> us -> CABLE would feed our own output back into the input.
            raise HTTPException(400, "输出不能选虚拟声卡（会形成回声循环），请选耳机或音箱")
        if was_running:
            self._update("serverAudioStated", 0)
        else:
            self.m.server_audio.stop()
        devprefs.save_prefs({"input": inp, "output": out})
        self._update("serverInputDeviceId", body.input)
        self._update("serverOutputDeviceId", body.output)
        self._update("serverAudioSampleRate", 48000)
        self.warnings = []
        if was_running:
            self.start()
        else:
            self._bypass()
        return self.devices()


    def debug_stacks(self):
        """Where every thread is right now (diagnosing hangs)."""
        import threading, traceback
        names = {t.ident: t.name for t in threading.enumerate()}
        return {f"{names.get(i, '?')} ({i})": "".join(traceback.format_stack(f)) for i, f in sys._current_frames().items()}

    def export_diagnostics(self, reveal: bool = True):
        """Zip logs, settings and live state for a bug report; show it in Explorer."""
        app_dir = str(PROJECT_ROOT / "app")
        if app_dir not in sys.path:
            sys.path.append(app_dir)
        import diagnostics

        def devices_if_free():
            # Don't let a stuck audio thread block the very export meant to diagnose it.
            if not self._audio_lock.acquire(timeout=3):
                return "<audio lock busy>"
            try:
                return self._devices()
            finally:
                self._audio_lock.release()

        extra: dict[str, Any] = {}
        for name, fn in (("status", self.status), ("devices", devices_if_free), ("stacks", self.debug_stacks)):
            try:
                extra[name] = fn()
            except Exception as e:
                extra[name] = f"<{type(e).__name__}: {e}>"
        try:
            path = diagnostics.build_bundle(extra)
        except Exception as e:
            logger.exception("diagnostics export failed")
            raise HTTPException(500, f"导出诊断信息失败：{e}") from e
        if reveal:
            diagnostics.reveal(path)
        return {"ok": True, "path": str(path), "fileName": path.name}

    # ---------- desktop helpers ----------
    def open_sound_settings(self, page: str = "apps-volume"):
        """Open Windows sound settings: per-app routing (default) or the main page (?page=sound)."""
        if sys.platform == "win32" and page in ("apps-volume", "sound"):
            os.startfile(f"ms-settings:{page}")  # type: ignore[attr-defined]
        return {"ok": True}

    # ---------- onboarding ----------
    def _build_onboarding_env(self) -> dict[str, Any]:
        inputs, _ = list_audio_device()
        slots = self._slots()
        return {
            "python": onboarding.check_python(),
            "uv": onboarding.check_uv(),
            "vbCable": onboarding.check_vb_cable(inputs),
            "voiceCount": len(slots),
            "hasActiveVoice": self.s.modelSlotIndex >= 0 and any(x.slotIndex == self.s.modelSlotIndex for x in slots),
            "installTask": self._env_installer.status(),
        }

    def onboarding_env(self, refresh: bool = False):
        if refresh and not self.s.serverAudioStated:
            with self._audio_lock:
                self._bypass()
        return self._build_onboarding_env()

    def onboarding_ensure_env(self, request: Request):
        if request.url.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise HTTPException(403, "请通过本机地址打开面板后再运行安装")
        self._env_installer.ensure_async()
        return self._build_onboarding_env()

    def onboarding_open_url(self, body: OpenUrlBody):
        try:
            url = onboarding.open_allowed_url(body.target)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "url": url}



def mount_panel(app) -> None:
    """Serve the built control panel at /panel (if it has been built)."""
    @app.get("/panel", include_in_schema=False)
    def _panel_redirect():
        return RedirectResponse("/panel/")

    if PANEL_DIST.is_dir():
        app.mount("/panel", StaticFiles(directory=str(PANEL_DIST), html=True), name="vt-panel")
        logger.info(f"Control panel served from {PANEL_DIST}")
    else:
        logger.warning(f"Control panel not built ({PANEL_DIST} missing); run npm run build in panel/")
