import numpy as np
import socketio
from time import time
from voice_changer.VoiceChangerManager import VoiceChangerManager

import asyncio

import logging
logger = logging.getLogger(__name__)

class MMVC_Namespace(socketio.AsyncNamespace):
    def __init__(self, namespace: str, voiceChangerManager: VoiceChangerManager):
        super().__init__(namespace)
        self.voiceChangerManager = voiceChangerManager
        self.sids: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.voiceChangerManager.setEmitTo(self.emit_coroutine)

    async def emitTo(self, vol, perf, err):
        for sid in list(self.sids):
            if err is not None:
                error_code, error_message = err
                await self.emit("error", [error_code, error_message], to=sid)
            else:
                await self.emit("server_stats", [vol, perf], to=sid)

    def emit_coroutine(self, vol, perf, err):
        loop = self._loop
        if self.sids and loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.emitTo(vol, perf, err), loop)

    @classmethod
    def get_instance(cls, voiceChangerManager: VoiceChangerManager):
        if not hasattr(cls, "_instance"):
            cls._instance = cls("/test", voiceChangerManager)
        return cls._instance

    def on_connect(self, sid, environ, ext):
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        self.sids.add(sid)
        logger.info(f"Connected SID: {sid}")

    async def on_request_message(self, sid, msg):
        recv_timestamp = round(time() * 1000)

        ts, data = msg
        # Receive and send int16 instead of float32 to reduce bandwidth requirement over websocket
        input_audio = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768

        out_audio, vol, perf, err = await asyncio.to_thread(self.voiceChangerManager.change_voice, input_audio)
        if err is not None:
            error_code, error_message = err
            await self.emit("error", [error_code, error_message], to=sid)
        else:
            ping = recv_timestamp - ts
            out_audio = (out_audio * 32767).astype(np.int16).tobytes()
            send_timestamp = round(time() * 1000)
            await self.emit("response", [send_timestamp, out_audio, ping, vol, perf], to=sid)

    def on_disconnect(self, sid):
        self.sids.discard(sid)
        logger.info(f"Disconnected SID: {sid}")
