import json
from typing import Union
from fastapi import APIRouter, HTTPException, UploadFile, Form

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from restapi.mods.FileUploader import upload_file
from voice_changer.VoiceChangerManager import VoiceChangerManager

from const import UPLOAD_DIR
from voice_changer.utils.LoadModelParams import LoadModelParamFile, LoadModelParams
import logging
logger = logging.getLogger(__name__)

class MMVC_Rest_Fileuploader:
    def __init__(self, voiceChangerManager: VoiceChangerManager):
        self.voiceChangerManager = voiceChangerManager
        self.router = APIRouter()
        self.router.add_api_route("/info", self.get_info, methods=["GET"])
        self.router.add_api_route("/upload_file", self.post_upload_file, methods=["POST"])
        self.router.add_api_route("/update_settings", self.post_update_settings, methods=["POST"])
        self.router.add_api_route("/load_model", self.post_load_model, methods=["POST"])

    def post_upload_file(self, file: UploadFile, filename: str = Form(...)):
        try:
            res = upload_file(UPLOAD_DIR, file, filename)
            return JSONResponse(content=jsonable_encoder(res))
        except HTTPException:
            raise
        except Exception as e:
            logger.exception(e)
            raise HTTPException(500, str(e))

    def get_info(self):
        try:
            info = self.voiceChangerManager.get_info()
            return JSONResponse(content=jsonable_encoder(info))
        except Exception as e:
            logger.exception(e)
            raise HTTPException(500, str(e))

    def post_update_settings(self, key: str = Form(...), val: Union[int, str, float] = Form(...)):
        try:
            info = self.voiceChangerManager.update_settings(key, val)
            return JSONResponse(content=jsonable_encoder(info))
        except Exception as e:
            logger.exception(e)
            raise HTTPException(500, str(e))

    async def post_load_model(
        self,
        slot: int = Form(...),
        isHalf: bool = Form(...),
        params: str = Form(...),
    ):
        try:
            paramDict = json.loads(params)
            logger.info("paramDict: %s", paramDict)
            loadModelparams = LoadModelParams(**paramDict)
            loadModelparams.files = [LoadModelParamFile(**x) for x in paramDict["files"]]

            info = await self.voiceChangerManager.load_model(loadModelparams)
            return JSONResponse(content=jsonable_encoder(info))
        except (TypeError, ValueError, KeyError) as e:
            raise HTTPException(400, "无效的模型导入参数") from e
        except Exception as e:
            logger.exception(e)
            raise HTTPException(500, str(e))
