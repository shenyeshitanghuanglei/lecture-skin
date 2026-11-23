import wave
import os
import logging
from const import TMP_DIR

logger = logging.getLogger(__name__)

STREAM_INPUT_FILE = os.path.join(TMP_DIR, "in.wav")
STREAM_OUTPUT_FILE = os.path.join(TMP_DIR, "out.wav")


class IORecorder:
    """Lazy audio IO recorder: only opens files on disk when recording is actually used,
    avoiding permanent Windows file locks on tmp_dir/in.wav & out.wav."""

    def __init__(self, input_sampling_rate: int, output_sampling_rate: int):
        self.fi = None
        self.fo = None
        self.input_sr = input_sampling_rate
        self.output_sr = output_sampling_rate

    def _clear(self):
        self.close()
        self._clearFile(STREAM_INPUT_FILE)
        self._clearFile(STREAM_OUTPUT_FILE)

    def _clearFile(self, filename: str):
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except OSError as e:
                logger.warning(f"Could not remove old recording file {filename}: {e}")

    def open(self, input_sampling_rate: int, output_sampling_rate: int):
        self.input_sr = input_sampling_rate
        self.output_sr = output_sampling_rate
        if self.fi is not None or self.fo is not None:
            self._ensure_open()

    def _ensure_open(self):
        self._clear()
        os.makedirs(TMP_DIR, exist_ok=True)
        self.fi = wave.open(STREAM_INPUT_FILE, "wb")
        self.fi.setnchannels(1)
        self.fi.setsampwidth(2)
        self.fi.setframerate(self.input_sr)

        self.fo = wave.open(STREAM_OUTPUT_FILE, "wb")
        self.fo.setnchannels(1)
        self.fo.setsampwidth(2)
        self.fo.setframerate(self.output_sr)
        logger.info(f"Recording IO -> {STREAM_INPUT_FILE}, {STREAM_OUTPUT_FILE}")

    def write_input(self, wav):
        if self.fi is None:
            self._ensure_open()
        self.fi.writeframes(wav)

    def write_output(self, wav):
        if self.fo is None:
            self._ensure_open()
        self.fo.writeframes(wav)

    def close(self):
        if self.fi is not None:
            self.fi.close()
            self.fi = None
        if self.fo is not None:
            self.fo.close()
            self.fo = None
