from const import MAX_SLOT_NUM
from data.ModelSlot import ModelSlots, loadAllSlotInfo, saveSlotInfo
import json
import os
import threading

import logging

logger = logging.getLogger(__name__)


class ModelSlotManager:
    _instance = None
    _init_lock = threading.Lock()

    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self._lock = threading.RLock()
        self._reserved: set[int] = set()
        self.modelSlots = loadAllSlotInfo(self.model_dir)

    @classmethod
    def get_instance(cls, model_dir: str):
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = cls(model_dir)
        return cls._instance

    def allocate_slot(self, preferred: int | None = None) -> int:
        """Atomically reserve an unused slot index for concurrent imports/finalizes."""
        with self._lock:
            self.modelSlots = loadAllSlotInfo(self.model_dir)
            used = {x.slotIndex for x in self.modelSlots if x.voiceChangerType is not None} | self._reserved
            if preferred is not None and 0 <= preferred < MAX_SLOT_NUM and preferred not in self._reserved:
                self._reserved.add(preferred)
                return preferred
            for i in range(MAX_SLOT_NUM):
                if i not in used:
                    self._reserved.add(i)
                    return i
            raise RuntimeError("声音库槽位已满（上限 500 个）")

    def release_slot(self, slotIndex: int) -> None:
        with self._lock:
            self._reserved.discard(slotIndex)

    def _save_model_slot(self, slotIndex: int, slotInfo: ModelSlots):
        with self._lock:
            saveSlotInfo(self.model_dir, slotIndex, slotInfo)
            self._reserved.discard(slotIndex)
            self.modelSlots = loadAllSlotInfo(self.model_dir)

    def _load_model_slot(self, slotIndex: int):
        with self._lock:
            if slotIndex < 0 or slotIndex >= len(self.modelSlots):
                return None
            return self.modelSlots[slotIndex]

    def getAllSlotInfo(self, reload: bool = False):
        with self._lock:
            if reload:
                self.modelSlots = loadAllSlotInfo(self.model_dir)
            return list(self.modelSlots)

    def get_slot_info(self, slotIndex: int):
        return self._load_model_slot(slotIndex)

    def save_model_slot(self, slotIndex: int, slotInfo: ModelSlots):
        self._save_model_slot(slotIndex, slotInfo)

    def update_model_info(self, slot_index: int, key: str, val):
        logger.info(f"UPDATE MODEL INFO: {key}={val}")
        with self._lock:
            slotInfo = self._load_model_slot(slot_index)
            if slotInfo is None:
                return
            if key == "speakers":
                setattr(slotInfo, key, json.loads(val))
            else:
                setattr(slotInfo, key, val)
            self._save_model_slot(slot_index, slotInfo)

