import threading
import time
import numpy as np
from const import SERVER_DEVICE_SAMPLE_RATES

from queue import Queue
import logging
from voice_changer.VoiceChangerSettings import VoiceChangerSettings
from voice_changer.Local.AudioDeviceList import checkSamplingRate, list_audio_device
import sounddevice as sd
import librosa

from voice_changer.utils.VoiceChangerModel import AudioInOutFloat
from typing import Union, Protocol

logger = logging.getLogger(__name__)

ERR_SAMPLE_RATE_NOT_SUPPORTED = """Specified sample rate is not supported by all selected audio devices.
Available sample rates:
  [Input]: %s
  [Output]: %s
  [Monitor]: %s"""
ERR_GENERIC_SERVER_AUDIO_ERROR = "A server audio error occurred."

class ServerAudioCallbacks(Protocol):
    def on_audio(self, unpackedData: AudioInOutFloat) -> tuple[AudioInOutFloat, list[Union[int, float]]]:
        ...

    def emit_to(self, volume: float, performance: list[float], err: tuple[str, str] | None):
        ...

class ServerAudio:
    def __init__(self, callbacks: ServerAudioCallbacks, settings: VoiceChangerSettings):
        self.settings = settings
        self.callbacks = callbacks
        self.mon_wav = None
        self.serverAudioInputDevices = None
        self.serverAudioOutputDevices = None
        self.monQueue = Queue()
        self.performance = [0, 0, 0]

        self.stream = None
        self.monitor = None

        self.running = False
        # voice-translater: while conversion is stopped the stream stays open and copies the
        # input (system audio arriving on the virtual cable) straight to the speakers.
        # Otherwise everything played to the default output (= CABLE) goes silent.
        self.bypass = False

        # voice-translater: rolling timing stats, logged every ~5 s
        self._timing: list[float] = []
        self._timing_frames = 0
        self._timing_last_log = time.perf_counter()
        try:
            from vt import sysvolume
            self._sysvol = sysvolume.get()
        except Exception as e:  # never let this break audio
            logger.warning(f"system volume mirroring unavailable: {e}")
            self._sysvol = None
        # voice-translater: live stats for the control panel (updated every block)
        self.live = {"blockMs": 0.0, "procMs": 0.0, "load": 0.0, "peakLoad": 0.0, "inVol": 0.0, "updated": 0.0}

    def _update_live(self, elapsed: float, frames: int, vol: float):
        sr = self.settings.serverInputAudioSampleRate or 48000
        block_ms = frames / sr * 1000
        proc_ms = elapsed * 1000
        a = 0.1  # EMA factor
        live = self.live
        live["procMs"] = proc_ms if live["procMs"] == 0 else live["procMs"] * (1 - a) + proc_ms * a
        live["blockMs"] = block_ms
        live["load"] = live["procMs"] / block_ms if block_ms else 0.0
        # The first blocks after (re)start include model warm-up; don't let them pin the peak.
        self._live_blocks = getattr(self, "_live_blocks", 0) + 1
        if self._live_blocks <= 20:
            live["procMs"] = proc_ms if self._live_blocks == 20 else 0.0
            live["peakLoad"] = 0.0
        else:
            live["peakLoad"] = max(live["peakLoad"] * 0.995, proc_ms / block_ms if block_ms else 0.0)
        live["inVol"] = float(vol)
        live["updated"] = time.time()

    def _log_timing(self, elapsed: float, frames: int):
        self._timing.append(elapsed)
        self._timing_frames = frames
        now = time.perf_counter()
        if now - self._timing_last_log < 5.0 or not self._timing:
            return
        sr = self.settings.serverInputAudioSampleRate or 48000
        block_ms = self._timing_frames / sr * 1000
        avg_ms = sum(self._timing) / len(self._timing) * 1000
        max_ms = max(self._timing) * 1000
        logger.info(
            f"[timing] block={block_ms:.0f}ms avg={avg_ms:.1f}ms max={max_ms:.1f}ms "
            f"load={avg_ms / block_ms:.0%} peak={max_ms / block_ms:.0%} n={len(self._timing)} "
            f"xruns(in/out)={self.live.get('inOverflows', 0)}/{self.live.get('outUnderflows', 0)}"
        )
        self._timing.clear()
        self._timing_last_log = now

    def getServerInputAudioDevice(self, index: int):
        audioinput, _ = list_audio_device()
        serverAudioDevice = [x for x in audioinput if x.index == index]
        if len(serverAudioDevice) > 0:
            return serverAudioDevice[0]
        else:
            return None

    def getServerOutputAudioDevice(self, index: int):
        _, audiooutput = list_audio_device()
        serverAudioDevice = [x for x in audiooutput if x.index == index]
        if len(serverAudioDevice) > 0:
            return serverAudioDevice[0]
        else:
            return None

    ###########################################
    # Callback Section
    ###########################################

    def _processData(self, indata: np.ndarray):
        indata = indata * self.settings.serverInputAudioGain
        unpackedData = librosa.to_mono(indata.T)
        return self.callbacks.on_audio(unpackedData)

    def _processDataWithTime(self, indata: np.ndarray):
        t0 = time.perf_counter()
        out_wav, vol, perf, err = self._processData(indata)
        elapsed = time.perf_counter() - t0
        self._log_timing(elapsed, len(indata))
        self._update_live(elapsed, len(indata), vol)
        self.performance = perf
        self.callbacks.emit_to(vol, self.performance, err)
        return self._match_loudness(indata, out_wav)

    # voice-translater: the converted voice came out ~10 dB quieter than the original
    # (upstream scales by sqrt(input RMS)). Track both levels over voiced blocks and
    # make the output as loud as the input; the output-gain slider applies on top.
    def _match_loudness(self, indata: np.ndarray, out_wav: np.ndarray) -> np.ndarray:
        if self.settings.passThrough or out_wav is None or len(out_wav) == 0:
            return out_wav
        in_rms = float(np.sqrt(np.mean(np.square(indata * self.settings.serverInputAudioGain))))
        out_rms = float(np.sqrt(np.mean(np.square(out_wav))))
        if in_rms > 1e-3 and out_rms > 1e-4:  # only learn from blocks with speech
            a = 0.05
            self._lm_in = in_rms if not hasattr(self, "_lm_in") else self._lm_in * (1 - a) + in_rms * a
            self._lm_out = out_rms if not hasattr(self, "_lm_out") else self._lm_out * (1 - a) + out_rms * a
        if hasattr(self, "_lm_in"):
            target = float(np.clip(self._lm_in / max(self._lm_out, 1e-6), 0.5, 8.0))
            prev = getattr(self, "_lm_gain", target)
            self._lm_gain = prev * 0.9 + target * 0.1  # glide, no pumping
            self.live["matchGain"] = self._lm_gain
            return out_wav * self._lm_gain
        return out_wav

    @staticmethod
    def _soft_limit(y: np.ndarray) -> np.ndarray:
        """Transparent below 0.8 FS, smooth knee above: no hard clipping crackle."""
        a = np.abs(y)
        over = a > 0.8
        if over.any():
            y = y.copy()
            y[over] = np.sign(y[over]) * (0.8 + 0.2 * np.tanh((a[over] - 0.8) / 0.2))
        return y

    def _count_xruns(self, status) -> None:
        # voice-translater: dropouts sound like crackle/rasp; upstream ignored these flags
        if status:
            if status.input_overflow:
                self.live["inOverflows"] = self.live.get("inOverflows", 0) + 1
            if status.output_underflow:
                self.live["outUnderflows"] = self.live.get("outUnderflows", 0) + 1

    def _bypass_copy(self, indata: np.ndarray, outdata: np.ndarray) -> None:
        sys_gain = self._sysvol.factor() if self._sysvol is not None else 1.0
        ic, oc = indata.shape[1], outdata.shape[1]
        if ic == oc:
            outdata[:] = indata * sys_gain
        elif ic >= 2 and oc >= 2:
            outdata.fill(0)
            outdata[:, :2] = indata[:, :2] * sys_gain
        else:
            mono = indata.mean(axis=1, keepdims=True) * sys_gain
            outdata[:] = np.repeat(mono, oc, axis=1)

    def audio_stream_callback(self, indata: np.ndarray, outdata: np.ndarray, frames, times, status):
        self._count_xruns(status)
        if self.bypass:
            try:
                self._bypass_copy(indata, outdata)
            except Exception as e:
                outdata.fill(0)
                logger.exception(e)
            return
        try:
            out_wav = self._processDataWithTime(indata)
            outputChannels = outdata.shape[1]
            # voice-translater: follow the Windows volume keys when they control the virtual cable
            sys_gain = self._sysvol.factor() if self._sysvol is not None else 1.0
            y = self._soft_limit(out_wav * self.settings.serverOutputAudioGain * sys_gain)
            outdata[:] = np.repeat(y, outputChannels).reshape(-1, outputChannels)
        except Exception as e:
            self.callbacks.emit_to(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            logger.exception(e)

    def audio_stream_callback_mon_queue(self, indata: np.ndarray, outdata: np.ndarray, frames, times, status):
        try:
            out_wav = self._processDataWithTime(indata)
            self.monQueue.put(out_wav)
            outputChannels = outdata.shape[1]
            outdata[:] = (np.repeat(out_wav, outputChannels).reshape(-1, outputChannels) * self.settings.serverOutputAudioGain)
        except Exception as e:
            self.callbacks.emit_to(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            logger.exception(e)

    def audio_monitor_callback(self, outdata: np.ndarray, frames, times, status):
        try:
            mon_wav = self.monQueue.get()
            while self.monQueue.qsize() > 0:
                self.monQueue.get()
            outputChannels = outdata.shape[1]
            outdata[:] = (np.repeat(mon_wav, outputChannels).reshape(-1, outputChannels) * self.settings.serverMonitorAudioGain)
        except Exception as e:
            self.callbacks.emit_to(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            logger.exception(e)

    ###########################################
    # Main Loop Section
    ###########################################
    def run_no_monitor(self, block_frame: int, inputMaxChannel: int, outputMaxChannel: int, inputExtraSetting, outputExtraSetting):
        self.stream = sd.Stream(
            callback=self.audio_stream_callback,
            latency='low',
            dtype="float32",
            device=(self.settings.serverInputDeviceId, self.settings.serverOutputDeviceId),
            blocksize=block_frame,
            samplerate=self.settings.serverInputAudioSampleRate,
            channels=(inputMaxChannel, outputMaxChannel),
            extra_settings=(inputExtraSetting, outputExtraSetting)
        )
        self.stream.start()

    def run_with_monitor(self, block_frame: int, inputMaxChannel: int, outputMaxChannel: int, monitorMaxChannel: int, inputExtraSetting, outputExtraSetting, monitorExtraSetting):
        self.stream = sd.Stream(
            callback=self.audio_stream_callback_mon_queue,
            latency='low',
            dtype="float32",
            device=(self.settings.serverInputDeviceId, self.settings.serverOutputDeviceId),
            blocksize=block_frame,
            samplerate=self.settings.serverInputAudioSampleRate,
            channels=(inputMaxChannel, outputMaxChannel),
            extra_settings=(inputExtraSetting, outputExtraSetting)
        )
        self.monitor = sd.OutputStream(
            callback=self.audio_monitor_callback,
            dtype="float32",
            device=self.settings.serverMonitorDeviceId,
            blocksize=block_frame,
            samplerate=self.settings.serverMonitorAudioSampleRate,
            channels=monitorMaxChannel,
            extra_settings=monitorExtraSetting
        )
        self.stream.start()
        self.monitor.start()

    def stop(self):
        # voice-translater: closing a WASAPI stream can hang inside the driver. Close in a
        # helper thread with a deadline so a stuck driver can't freeze stop/start for good.
        self.running = False
        streams = [x for x in (self.stream, self.monitor) if x is not None]
        self.stream = None
        self.monitor = None
        if not streams:
            return

        def _close():
            for x in streams:
                try:
                    x.close()
                except Exception as e:
                    logger.warning(f"closing audio stream failed: {e}")

        t = threading.Thread(target=_close, name="vt-stream-close", daemon=True)
        t.start()
        t.join(timeout=3.0)
        if t.is_alive():
            self._stuck_close = t
            logger.error("audio stream did not close within 3 s (driver hang); continuing without it")

    def portaudio_busy(self) -> bool:
        """True while an abandoned stream close is still stuck: re-initialising PortAudio
        then would block on it too."""
        t = getattr(self, "_stuck_close", None)
        return t is not None and t.is_alive()

    def reinit_portaudio(self) -> None:
        if self.portaudio_busy():
            logger.warning("skipping PortAudio re-scan: a previous stream close is still stuck")
            return
        sd._terminate()
        sd._initialize()

    ###########################################
    # Start Section
    ###########################################
    def start(self, bypass: bool | None = None):
        self.stop()
        if bypass is not None:
            self.bypass = bypass
        self._live_blocks = 0
        self.live.update(procMs=0.0, load=0.0, peakLoad=0.0, updated=0.0, inOverflows=0, outUnderflows=0)

        self.reinit_portaudio()
        if self.portaudio_busy():
            return

        # Device 特定
        serverInputAudioDevice = self.getServerInputAudioDevice(self.settings.serverInputDeviceId)
        serverOutputAudioDevice = self.getServerOutputAudioDevice(self.settings.serverOutputDeviceId)
        serverMonitorAudioDevice = self.getServerOutputAudioDevice(self.settings.serverMonitorDeviceId)

        if serverInputAudioDevice is None or serverOutputAudioDevice is None:
            logger.error("Input or output device is not selected.")
            self.callbacks.emit_to(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            return

        # Generate ExtraSetting
        wasapiExclusiveMode = bool(self.settings.exclusiveMode)

        inputChannels = serverInputAudioDevice.maxInputChannels
        inputExtraSetting = None
        if serverInputAudioDevice and "WASAPI" in serverInputAudioDevice.hostAPI:
            inputExtraSetting = sd.WasapiSettings(exclusive=wasapiExclusiveMode, auto_convert=not wasapiExclusiveMode)
        elif serverInputAudioDevice and "ASIO" in serverInputAudioDevice.hostAPI and self.settings.asioInputChannel != -1:
            inputExtraSetting = sd.AsioSettings(channel_selectors=[self.settings.asioInputChannel])
            inputChannels = 1

        outputChannels = serverOutputAudioDevice.maxOutputChannels
        outputExtraSetting = None
        if serverOutputAudioDevice and "WASAPI" in serverOutputAudioDevice.hostAPI:
            outputExtraSetting = sd.WasapiSettings(exclusive=wasapiExclusiveMode, auto_convert=not wasapiExclusiveMode)
        elif serverInputAudioDevice and "ASIO" in serverInputAudioDevice.hostAPI and self.settings.asioOutputChannel != -1:
            outputExtraSetting = sd.AsioSettings(channel_selectors=[self.settings.asioOutputChannel])
            outputChannels = 1

        monitorExtraSetting = None
        if serverMonitorAudioDevice and "WASAPI" in serverMonitorAudioDevice.hostAPI:
            monitorExtraSetting = sd.WasapiSettings(exclusive=wasapiExclusiveMode, auto_convert=not wasapiExclusiveMode)

        logger.info("Devices:")
        logger.info(f"  [Input]: {serverInputAudioDevice} {inputExtraSetting}")
        logger.info(f"  [Output]: {serverOutputAudioDevice}, {outputExtraSetting}")
        logger.info(f"  [Monitor]: {serverMonitorAudioDevice}, {monitorExtraSetting}")

        # サンプリングレート
        # 同一サンプリングレートに統一（変換時にサンプルが不足する場合があるため。パディング方法が明らかになれば、それぞれ設定できるかも）
        self.settings.serverInputAudioSampleRate = self.settings.serverAudioSampleRate
        self.settings.serverOutputAudioSampleRate = self.settings.serverAudioSampleRate
        self.settings.serverMonitorAudioSampleRate = self.settings.serverAudioSampleRate

        # Sample Rate Check
        if "WASAPI" not in serverInputAudioDevice.hostAPI and not wasapiExclusiveMode:
            inputAudioSampleRateAvailable = checkSamplingRate(self.settings.serverInputDeviceId, self.settings.serverInputAudioSampleRate, "input")
            outputAudioSampleRateAvailable = checkSamplingRate(self.settings.serverOutputDeviceId, self.settings.serverOutputAudioSampleRate, "output")
            monitorAudioSampleRateAvailable = checkSamplingRate(self.settings.serverMonitorDeviceId, self.settings.serverMonitorAudioSampleRate, "output") if serverMonitorAudioDevice else True

            logger.info("Sample Rate:")
            logger.info(f"  [Input]: {self.settings.serverInputAudioSampleRate} -> {inputAudioSampleRateAvailable}")
            logger.info(f"  [Output]: {self.settings.serverOutputAudioSampleRate} -> {outputAudioSampleRateAvailable}")
            if serverMonitorAudioDevice is not None:
                logger.info(f"  [Monitor]: {self.settings.serverMonitorAudioSampleRate} -> {monitorAudioSampleRateAvailable}")

            # FIXME: Ideally, there are two options:
            # 1. UI must be provided with all sample rates and select only valid combinations of sample rates.
            # 2. Server must pick the default device sample rate automatically so UI doesn't have to bother.
            # This must be removed once it's done.
            if not inputAudioSampleRateAvailable or not outputAudioSampleRateAvailable or not monitorAudioSampleRateAvailable:
                logger.info("Checking Available Sample Rate:")
                availableInputSampleRate = []
                availableOutputSampleRate = []
                availableMonitorSampleRate = []
                for sr in SERVER_DEVICE_SAMPLE_RATES:
                    if checkSamplingRate(self.settings.serverInputDeviceId, sr, "input"):
                        availableInputSampleRate.append(sr)
                    if checkSamplingRate(self.settings.serverOutputDeviceId, sr, "output"):
                        availableOutputSampleRate.append(sr)
                    if serverMonitorAudioDevice is not None:
                        if checkSamplingRate(self.settings.serverMonitorDeviceId, sr, "output"):
                            availableMonitorSampleRate.append(sr)
                err = ERR_SAMPLE_RATE_NOT_SUPPORTED % (availableInputSampleRate, availableOutputSampleRate, availableMonitorSampleRate)
                self.callbacks.emit_to(
                    0,
                    self.performance,
                    ('ERR_SAMPLE_RATE_NOT_SUPPORTED', err)
                )
                logger.error(err)
                return

        # FIXME: In UI, block size is calculated based on 48kHz so we convert from 48kHz to input device sample rate.
        block_frame = int((self.settings.serverReadChunkSize * 128 / 48000) * self.settings.serverInputAudioSampleRate)

        try:
            if serverMonitorAudioDevice is None or self.bypass:
                self.run_no_monitor(block_frame, inputChannels, outputChannels, inputExtraSetting, outputExtraSetting)
            else:
                self.run_with_monitor(block_frame, inputChannels, outputChannels, serverMonitorAudioDevice.maxOutputChannels, inputExtraSetting, outputExtraSetting, monitorExtraSetting)
            self.running = True
        except Exception as e:
            self.callbacks.emit_to(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            logger.exception(e)

    ###########################################
    # Info Section
    ###########################################
    def get_info(self):
        data = {}
        try:
            audioinput, audiooutput = list_audio_device()
            self.serverAudioInputDevices = audioinput
            self.serverAudioOutputDevices = audiooutput
        except Exception as e:
            self.callbacks.emit_to(0, self.performance, ('ERR_GENERIC_SERVER_AUDIO_ERROR', ERR_GENERIC_SERVER_AUDIO_ERROR))
            logger.exception(e)

        data["serverAudioInputDevices"] = self.serverAudioInputDevices
        data["serverAudioOutputDevices"] = self.serverAudioOutputDevices
        return data

    def update_settings(self, key: str, val, old_val):
        if key == 'serverAudioStated':
            if val:
                self.start(bypass=False)
            else:
                self.stop()
        if self.running and key in { 'serverInputDeviceId', 'serverOutputDeviceId', 'serverMonitorDeviceId', 'serverReadChunkSize', 'serverAudioSampleRate', 'asioInputChannel', 'asioOutputChannel' }:
            self.start()
