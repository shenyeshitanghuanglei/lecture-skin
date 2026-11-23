from typing import TypeAlias, Union
from const import MAX_SLOT_NUM, EnumInferenceTypes, EmbedderType, VoiceChangerType

from dataclasses import dataclass, asdict, field

import os
import json
import logging
logger = logging.getLogger(__name__)

@dataclass
class ModelSlot:
    slotIndex: int = -1
    voiceChangerType: VoiceChangerType | None = None
    name: str = ""
    description: str = ""
    credit: str = ""
    termsOfUseUrl: str = ""
    iconFile: str = ""
    speakers: dict = field(default_factory=lambda: {})


@dataclass
class RVCModelSlot(ModelSlot):
    voiceChangerType: VoiceChangerType = "RVC"
    modelFile: str = ""
    modelFileOnnx: str = ""
    indexFile: str = ""
    defaultTune: int = 0
    defaultFormantShift: float = 0
    defaultIndexRatio: float = 0
    # voice-translater: 0.5 disables protection (upstream default); 0.33 keeps consonants and
    # breaths from being pulled toward the index (fewer boundary clicks in our sweep).
    defaultProtect: float = 0.33
    isONNX: bool = False
    modelType: str = EnumInferenceTypes.pyTorchRVC.value
    modelTypeOnnx: str = EnumInferenceTypes.onnxRVC.value
    samplingRate: int = -1
    f0: bool = True
    embChannels: int = 256
    embOutputLayer: int = 9
    useFinalProj: bool = True
    deprecated: bool = False
    embedder: EmbedderType = "hubert_base"

    sampleId: str = ""
    speakers: dict = field(default_factory=lambda: {0: "target"})

    version: str = "v2"

    # voice-translater: typical pitch of this voice, used as the autoPitch target (0 = unknown)
    targetPitchHz: float = 0.0


ModelSlots: TypeAlias = Union[
    ModelSlot,
    RVCModelSlot,
]


def loadSlotInfo(model_dir: str, slotIndex: int) -> ModelSlots:
    slotDir = os.path.join(model_dir, str(slotIndex))
    jsonFile = os.path.join(slotDir, "params.json")
    if not os.path.exists(jsonFile):
        slot = ModelSlot()
        slot.slotIndex = slotIndex
        return slot
    try:
        with open(jsonFile, encoding="utf-8") as f:
            jsonDict = json.load(f)
        slotInfoKey = list(ModelSlot.__annotations__.keys())
        slotInfo = ModelSlot(**{k: v for k, v in jsonDict.items() if k in slotInfoKey})
        if slotInfo.voiceChangerType == "RVC":
            slotInfoKey.extend(list(RVCModelSlot.__annotations__.keys()))
            res = RVCModelSlot(**{k: v for k, v in jsonDict.items() if k in slotInfoKey})
            res.slotIndex = slotIndex
            return res
    except (OSError, ValueError, TypeError) as e:
        logger.warning(f"Corrupt or unreadable slot params at {jsonFile}: {e}")
    slot = ModelSlot()
    slot.slotIndex = slotIndex
    return slot


def loadAllSlotInfo(model_dir: str):
    slotInfos: list[ModelSlots] = [ModelSlot(slotIndex=i) for i in range(MAX_SLOT_NUM)]
    if not os.path.isdir(model_dir):
        return slotInfos
    try:
        with os.scandir(model_dir) as it:
            for entry in it:
                if entry.is_dir() and entry.name.isdigit():
                    idx = int(entry.name)
                    if 0 <= idx < MAX_SLOT_NUM:
                        slotInfos[idx] = loadSlotInfo(model_dir, idx)
    except OSError as e:
        logger.warning(f"Could not scan model_dir {model_dir}: {e}")
    return slotInfos


def saveSlotInfo(model_dir: str, slotIndex: int, slotInfo: ModelSlots):
    slotDir = os.path.join(model_dir, str(slotIndex))
    os.makedirs(slotDir, exist_ok=True)
    logger.info(f"SlotInfo::: {slotInfo}")
    slotInfoDict = asdict(slotInfo)
    slotInfoDict["slotIndex"] = slotIndex
    dst = os.path.join(slotDir, "params.json")
    tmp = dst + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(slotInfoDict, f, ensure_ascii=False, indent=4)
    os.replace(tmp, dst)
