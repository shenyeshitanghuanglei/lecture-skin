from enum import Enum
import os
import sys
import tempfile
from typing import Literal, TypeAlias
import numpy as np


VoiceChangerType: TypeAlias = Literal[
    "RVC",
]

ROOT_PATH = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.realpath(sys.argv[0]))

LOG_FILE = os.path.join(ROOT_PATH, 'vcclient.log')
DOTENV_FILE = os.path.join(ROOT_PATH, '.env')
STORED_SETTING_FILE = os.path.join(ROOT_PATH, 'stored_setting.json')
ASSETS_FILE = os.path.join(ROOT_PATH, 'assets.json')

SERVER_DEVICE_SAMPLE_RATES = [16000, 32000, 44100, 48000, 96000, 192000]

if hasattr(sys, "_MEIPASS"):
    tmpdir = tempfile.TemporaryDirectory()
    SSL_KEY_DIR = os.path.join(tmpdir.name, "keys")
    UPLOAD_DIR = os.path.join(tmpdir.name, "upload_dir")
    TMP_DIR = os.path.join(tmpdir.name, "tmp_dir")
else:
    SSL_KEY_DIR = "keys"
    UPLOAD_DIR = "upload_dir"
    TMP_DIR = "tmp_dir"

EDITION_FILE = os.path.join(sys._MEIPASS, "edition.txt") if hasattr(sys, "_MEIPASS") else 'edition.txt'


VERSION_FILE = os.path.join(sys._MEIPASS, "version.txt") if hasattr(sys, "_MEIPASS") else 'version.txt'

EmbedderType: TypeAlias = Literal["hubert_base", "contentvec"]


class EnumInferenceTypes(Enum):
    pyTorchRVC = "pyTorchRVC"
    pyTorchRVCNono = "pyTorchRVCNono"
    pyTorchRVCv2 = "pyTorchRVCv2"
    pyTorchRVCv2Nono = "pyTorchRVCv2Nono"
    pyTorchWebUI = "pyTorchWebUI"
    pyTorchWebUINono = "pyTorchWebUINono"
    onnxRVC = "onnxRVC"
    onnxRVCNono = "onnxRVCNono"

F0_MIN = 50
F0_MAX = 1100
F0_MEL_MIN = 1127 * np.log(1 + F0_MIN / 700)
F0_MEL_MAX = 1127 * np.log(1 + F0_MAX / 700)

PitchExtractorType: TypeAlias = Literal[
    "crepe_full",
    "crepe_tiny",
    "crepe_full_onnx",
    "crepe_tiny_onnx",
    "rmvpe",
    "rmvpe_onnx",
    "fcpe",
    "fcpe_onnx",
]

ServerAudioDeviceType: TypeAlias = Literal["audioinput", "audiooutput"]

def get_edition():
    if not os.path.exists(EDITION_FILE):
        return '-'
    with open(EDITION_FILE, 'r') as f:
        return f.read()

def get_version():
    if not os.path.exists(VERSION_FILE):
        return 'Development'
    with open(VERSION_FILE, 'r') as f:
        return f.read()

MAX_SLOT_NUM = 500
