from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from const import get_edition, get_version
from voice_changer.VoiceChangerManager import VoiceChangerManager

import logging
logger = logging.getLogger(__name__)


class MMVC_Rest_VoiceChanger:
    def __init__(self, voiceChangerManager: VoiceChangerManager):
        self.voiceChangerManager = voiceChangerManager
        self.router = APIRouter()
        self.router.add_api_route("/edition", self.edition, methods=["GET"])
        self.router.add_api_route("/version", self.version, methods=["GET"])


    def edition(self):
        return PlainTextResponse(get_edition())


    def version(self):
        return PlainTextResponse(get_version())
