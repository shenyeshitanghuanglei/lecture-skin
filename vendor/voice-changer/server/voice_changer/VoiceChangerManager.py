import asyncio
import json
import os
import sys
import shutil
import tempfile
import threading
from pathlib import Path
import numpy as np
import logging
from voice_changer.Local.ServerAudio import ServerAudio, ServerAudioCallbacks
from voice_changer.ModelSlotManager import ModelSlotManager
from const import MAX_SLOT_NUM, STORED_SETTING_FILE, UPLOAD_DIR
from data.ModelSlot import saveSlotInfo
from restapi.mods.FileUploader import valid_filename
from voice_changer.VoiceChangerSettings import VoiceChangerSettings
from voice_changer.VoiceChangerV2 import VoiceChangerV2
from voice_changer.utils.LoadModelParams import LoadModelParamFile, LoadModelParams
from voice_changer.utils.VoiceChangerModel import AudioInOutFloat
from settings import get_settings
from voice_changer.common.deviceManager.DeviceManager import DeviceManager
from Exceptions import (
    PipelineNotInitializedException,
    VoiceChangerIsNotSelectedException,
)
from traceback import format_exc
from typing import Callable, Any

from voice_changer.RVC.RVCr2 import RVCr2
from voice_changer.RVC.RVCModelSlotGenerator import RVCModelSlotGenerator  # 起動時にインポートするとパラメータが取れない。

logger = logging.getLogger(__name__)


class VoiceChangerManager(ServerAudioCallbacks):
    _instance = None

    ############################
    # ServerDeviceCallbacks
    ############################
    def on_audio(self, unpackedData: AudioInOutFloat):
        return self.change_voice(unpackedData)

    def emit_to(self, volume: float, performance: list[float], err):
        self.emitToFunc(volume, performance, err)

    ############################
    # VoiceChangerManager
    ############################
    def __init__(self):
        logger.info("Initializing...")
        self.emitToFunc: Callable[..., None] = lambda *args: None
        # Serialises model imports (panel import and clone export can run concurrently).
        self._load_lock = threading.Lock()
        self.params = get_settings()

        self.modelSlotManager = ModelSlotManager.get_instance(self.params.model_dir)
        # スタティックな情報を収集

        self.settings = VoiceChangerSettings()
        try:
            with open(STORED_SETTING_FILE, "r", encoding="utf-8") as f:
                settings = json.load(f)
            self.settings.set_properties(settings)
        except:
            pass

        self.device_manager = DeviceManager.get_instance()
        self.devices = self.device_manager.list_devices()
        self.device_manager.initialize(self.settings.gpu, self.settings.forceFp32, self.settings.disableJit)

        self.vc = VoiceChangerV2(self.settings)
        self.server_audio = ServerAudio(self, self.settings)

        logger.info("Initialized.")

        # Initialize the voice changer
        self.initialize(self.settings.modelSlotIndex)

    def store_setting(self):
        # Write to a temp file then rename, so a crash mid-write can't corrupt the settings.
        tmp = STORED_SETTING_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.settings.to_dict_stateless(), f, ensure_ascii=False)
        os.replace(tmp, STORED_SETTING_FILE)

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_model_sync(self, params: LoadModelParams):
        if type(params.slot) is not int or not 0 <= params.slot < MAX_SLOT_NUM or params.voiceChangerType != "RVC":
            raise ValueError("无效的模型槽位或类型")
        clean_names = params.params.get("clean_names", {})
        kinds = [file.kind for file in params.files]
        if kinds.count("rvcModel") != 1 or kinds.count("rvcIndex") != len(kinds) - 1 or len(kinds) > 2:
            raise ValueError("请选择一个 RVC 模型和至多一个索引")
        for file in params.files:
            dest = clean_names.get(file.name, file.name)
            extensions = (".pth", ".onnx", ".safetensors") if file.kind == "rvcModel" else (".index",)
            if file.dir or not valid_filename(file.name) or not valid_filename(dest) or not dest.lower().endswith(extensions):
                raise ValueError("模型和索引必须使用有效的文件名")
        root = Path(self.params.model_dir)
        root.mkdir(parents=True, exist_ok=True)
        with self._load_lock, tempfile.TemporaryDirectory(prefix=".import-", dir=root) as temp:
            slot = root / str(params.slot)
            staged = Path(temp) / slot.name
            staged.mkdir()
            for file in params.files:
                clean_name = clean_names.get(file.name, file.name)
                shutil.move(Path(UPLOAD_DIR) / file.name, staged / clean_name)
                file.name = clean_name
            info = RVCModelSlotGenerator.load_model(params, staged)
            saveSlotInfo(temp, params.slot, info)

            if slot.exists():
                if params.slot == self.settings.modelSlotIndex:
                    raise ValueError("无法替换正在使用的声音")
                backup = slot.rename(root / f"{Path(temp).name}-previous")
                try:
                    staged.rename(slot)
                except OSError:
                    backup.rename(slot)
                    raise
                shutil.rmtree(backup, ignore_errors=True)
            else:
                staged.rename(slot)
            self.modelSlotManager.getAllSlotInfo(reload=True)
            self.modelSlotManager.release_slot(params.slot)

    async def load_model(self, params: LoadModelParams):
        await asyncio.to_thread(self._load_model_sync, params)

    def get_info(self):
        data = self.settings.to_dict()
        data["gpus"] = self.devices
        data["modelSlots"] = self.modelSlotManager.getAllSlotInfo(reload=True)
        data["python"] = sys.version
        data["voiceChangerParams"] = self.params

        data["status"] = "OK"

        info = self.server_audio.get_info()
        data.update(info)

        info = self.vc.get_info()
        data.update(info)

        return data

    def initialize(self, val: int):
        slotInfo = self.modelSlotManager.get_slot_info(val)
        if slotInfo is None or slotInfo.voiceChangerType is None:
            logger.warning(f"Model slot is not found {val}")
            return

        self.settings.set_properties({
            'tran': slotInfo.defaultTune,
            'formantShift': slotInfo.defaultFormantShift,
            'indexRatio': slotInfo.defaultIndexRatio,
            'protect': slotInfo.defaultProtect
        })
        # voice-translater: each voice carries its own autoPitch target
        target_hz = getattr(slotInfo, 'targetPitchHz', 0) or 0
        if target_hz > 0:
            self.settings.autoPitchTargetHz = target_hz

        if slotInfo.voiceChangerType == self.vc.get_type():
            self.vc.set_slot_info(slotInfo)
        elif slotInfo.voiceChangerType == "RVC":
            logger.info("Loading RVC...")
            self.vc.initialize(RVCr2(slotInfo, self.settings))
        else:
            logger.error(f"Unknown voice changer model: {slotInfo.voiceChangerType}")

    def update_settings(self, key: str, val: Any):
        logger.info(f"update configuration {key}: {val}")
        error, old_value = self.settings.set_property(key, val)
        if error:
            return self.get_info()
        # TODO: This is required to get type-casted setting. But maybe this should be done prior to setting.
        val = self.settings.get_property(key)
        if old_value == val:
            return self.get_info()
        # TODO: Storing settings on each change is suboptimal. Maybe timed autosave?
        self.store_setting()

        if key == "modelSlotIndex":
            logger.info(f"Model slot is changed {old_value} -> {val}")
            self.initialize(val)
        elif key == 'gpu':
            self.device_manager.set_device(val)
        elif key == 'forceFp32':
            self.device_manager.set_force_fp32(val)
        elif key == 'disableJit':
            self.device_manager.set_disable_jit(val)
        # FIXME: This is a very counter-intuitive handling of audio modes...
        # Map "serverAudioSampleRate" to "inputSampleRate" and "outputSampleRate"
        # since server audio can have its sample rate configured.
        # Revert change in case we switched back to client audio mode.
        elif key == 'enableServerAudio':
            if val:
                self.update_settings('inputSampleRate', self.settings.serverAudioSampleRate)
                self.update_settings('outputSampleRate', self.settings.serverAudioSampleRate)
            else:
                self.update_settings('inputSampleRate', 48000)
                self.update_settings('outputSampleRate', 48000)
        elif key == 'serverAudioSampleRate':
            self.update_settings('inputSampleRate', self.settings.serverAudioSampleRate)
            self.update_settings('outputSampleRate', self.settings.serverAudioSampleRate)

        self.server_audio.update_settings(key, val, old_value)
        self.vc.update_settings(key, val, old_value)

        return self.get_info()

    def change_voice(self, receivedData: AudioInOutFloat) -> tuple[AudioInOutFloat, tuple, tuple | None]:
        if self.settings.passThrough:  # パススルー
            vol = float(np.sqrt(
                np.square(receivedData).mean(dtype=np.float32)
            ))
            return receivedData, vol, [0, 0, 0], None

        try:
            with self.device_manager.lock:
                audio, vol, perf = self.vc.on_request(receivedData)
            return audio, vol, perf, None
        except VoiceChangerIsNotSelectedException as e:
            logger.exception(e)
            return np.zeros(1, dtype=np.float32), 0, [0, 0, 0], ('VoiceChangerIsNotSelectedException', format_exc())
        except PipelineNotInitializedException as e:
            logger.exception(e)
            return np.zeros(1, dtype=np.float32), 0, [0, 0, 0], ('PipelineNotInitializedException', format_exc())
        except Exception as e:
            logger.exception(e)
            return np.zeros(1, dtype=np.float32), 0, [0, 0, 0], ('Exception', format_exc())

    def setEmitTo(self, emitTo: Callable[[Any], None]):
        self.emitToFunc = emitTo
