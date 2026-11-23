"""Voice-cloning job API, mounted at /vt/api/clone (voice-translater addition).

The heavy lifting runs in a separate process (``python -m trainer.run``, see /trainer);
this module only manages job folders, starts/cancels that process and reads the
progress.json it writes. One job may run at a time: it needs the GPU.
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from const import UPLOAD_DIR
from voice_changer.utils.LoadModelParams import LoadModelParamFile, LoadModelParams

import logging

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from trainer.audio import AUDIO_EXTS, VIDEO_EXTS  # noqa: E402
from trainer.common import JOBS, Job  # noqa: E402

JOB_ID = re.compile(r"^[a-z0-9]{6,32}$")
CLIP_ID = re.compile(r"^\d{5}$")
RUNNING_STATES = {"preparing", "training"}
MAX_UPLOAD_FILES = 50

# Shared with the panel's audio controls so starting conversion and starting a trainer are atomic.
audio_gpu_lock = threading.RLock()
_job_locks_guard = threading.Lock()
_job_locks: dict[str, threading.RLock] = {}
_queue: list[tuple[str, str, list[str]]] = []  # (job_id, stage, spawn_args)


def _jlock(job_id: str) -> threading.RLock:
    with _job_locks_guard:
        lk = _job_locks.get(job_id)
        if lk is None:
            lk = threading.RLock()
            _job_locks[job_id] = lk
        return lk


def _is_queued(job_id: str) -> bool:
    with audio_gpu_lock:
        return any(jid == job_id for jid, _, _ in _queue)


def _dequeue(job_id: str) -> bool:
    with audio_gpu_lock:
        before = len(_queue)
        _queue[:] = [item for item in _queue if item[0] != job_id]
        return len(_queue) < before


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def _alive(prog: dict) -> bool:
    """A job is running while its trainer has not cleared "running" and its process lives.
    The trainer runs detached (see trainer/launch.py), so liveness is judged by pid."""
    return bool(prog.get("running")) and _pid_alive(prog.get("pid"))


def _running_job() -> str | None:
    if not JOBS.exists():
        return None
    for d in JOBS.iterdir():
        if d.is_dir() and JOB_ID.match(d.name) and (d / "progress.json").exists():
            if _alive(Job(d.name).progress()):
                return d.name
    return None


def training_active() -> bool:
    """True while a trainer process holds (or is queued for) the GPU."""
    with audio_gpu_lock:
        return _running_job() is not None or bool(_queue)


class CreateBody(BaseModel):
    name: str


class FilePatch(BaseModel):
    separate: bool | None = None
    label: str | None = None


class ClipsPatch(BaseModel):
    ids: list[str]
    excluded: bool


class TrainBody(BaseModel):
    preset: str = "standard"
    fresh: bool = False
    consent: bool = False
    epochs: int | None = None  # overrides the preset (tests / advanced use)


class FinalizeBody(BaseModel):
    epoch: int
    name: str | None = None


class CloneApi:
    def __init__(self, vt, enable_dispatcher: bool = True):
        self.vt = vt  # VtApi: used to pause conversion and to reach the voice library
        self._dispatcher_started = False
        self._enable_dispatcher = enable_dispatcher
        self.router = APIRouter(prefix="/vt/api/clone")
        r = self.router
        r.add_api_route("/jobs", self.list_jobs, methods=["GET"])
        r.add_api_route("/jobs", self.create_job, methods=["POST"])
        r.add_api_route("/jobs/{job_id}", self.get_job, methods=["GET"])
        r.add_api_route("/jobs/{job_id}", self.delete_job, methods=["DELETE"])
        r.add_api_route("/jobs/{job_id}", self.rename_job, methods=["PATCH"])
        r.add_api_route("/jobs/{job_id}/files", self.upload_files, methods=["POST"])
        r.add_api_route("/jobs/{job_id}/files/{name}", self.patch_file, methods=["PATCH"])
        r.add_api_route("/jobs/{job_id}/files/{name}", self.delete_file, methods=["DELETE"])
        r.add_api_route("/jobs/{job_id}/preview-source", self.set_preview_source, methods=["POST"])
        r.add_api_route("/jobs/{job_id}/preview-source", self.clear_preview_source, methods=["DELETE"])
        r.add_api_route("/jobs/{job_id}/prepare", self.prepare, methods=["POST"])
        r.add_api_route("/jobs/{job_id}/clips", self.patch_clips, methods=["PATCH"])
        r.add_api_route("/jobs/{job_id}/clips/{clip_id}.wav", self.clip_audio, methods=["GET"])
        r.add_api_route("/jobs/{job_id}/train", self.train, methods=["POST"])
        r.add_api_route("/jobs/{job_id}/cancel", self.cancel, methods=["POST"])
        r.add_api_route("/jobs/{job_id}/preview/{fname}", self.preview_audio, methods=["GET"])
        r.add_api_route("/jobs/{job_id}/finalize", self.finalize, methods=["POST"])

    # ---------- helpers ----------
    def _job(self, job_id: str, must_exist: bool = True) -> Job:
        if not JOB_ID.match(job_id):
            raise HTTPException(404, "作业不存在")
        if must_exist and not (JOBS / job_id / "job.json").exists():
            raise HTTPException(404, "作业不存在")
        return Job(job_id)

    def _state(self, job: Job) -> dict:
        prog = job.progress()
        state = prog.get("state") or ("new" if not job.meta.get("files") else "draft")
        if _is_queued(job.id):
            return {**prog, "state": "queued", "running": None}
        running = prog["running"] if _alive(prog) else None
        if not running and state in RUNNING_STATES:
            state = "interrupted"
        elif state == "queued":
            state = "draft" if not (job.read("report.json") or {}).get("canTrain") else "prepared"
        return {**prog, "state": state, "running": running}

    def _pause_conversion_if_needed(self, job: Job) -> None:
        if self.vt and bool(self.vt.s.serverAudioStated):
            self.vt.stop()
            job.update_meta(conversionPaused=True)

    def _submit_gpu_stage(self, job: Job, stage: str, *args: str, init_pct: float = 0.0, init_msg: str = "") -> None:
        """Run immediately if the GPU is free; otherwise enqueue so multiple clone jobs
        can be submitted concurrently and executed back-to-back."""
        with audio_gpu_lock:
            active = _running_job()
            if active == job.id or _is_queued(job.id):
                raise HTTPException(409, "这个作业正在处理或排队中")
            if active is None and not _queue:
                self._pause_conversion_if_needed(job)
                state_name = "preparing" if stage == "prepare" else "training"
                job.report(state=state_name, stage="start", pct=init_pct, message=init_msg, error=None, running=stage, pid=None)
                self._spawn(job, *args)
                return
            pos = len(_queue) + 1
            _queue.append((job.id, stage, list(args)))
            job.path("cancel").unlink(missing_ok=True)
            job.report(
                state="queued",
                stage=stage,
                pct=init_pct,
                message=f"排队中（前方还有 {pos} 个作业），显卡空闲后自动开始",
                error=None,
                running=None,
                pid=None,
            )
            self._ensure_dispatcher()

    def _ensure_dispatcher(self) -> None:
        if not self._enable_dispatcher or self._dispatcher_started:
            return
        self._dispatcher_started = True
        threading.Thread(target=self._dispatch_loop, name="vt-clone-queue", daemon=True).start()

    def _dispatch_once(self) -> bool:
        with audio_gpu_lock:
            if not _queue or _running_job() is not None:
                return False
            job_id, stage, args = _queue.pop(0)
            if not (JOBS / job_id / "job.json").exists():
                return False
            job = Job(job_id)
            if job.path("cancel").exists():
                job.report(state="cancelled", message="已取消", running=None, pid=None)
                return False
            self._pause_conversion_if_needed(job)
            state_name = "preparing" if stage == "prepare" else "training"
            msg = "开始处理素材" if stage == "prepare" else "正在启动训练"
            job.report(state=state_name, stage="start", message=msg, error=None, running=stage, pid=None)
            self._spawn(job, *args)
            return True

    def _dispatch_loop(self) -> None:
        while True:
            time.sleep(1.0)
            try:
                self._dispatch_once()
            except Exception as e:
                logger.debug(f"clone queue tick error: {e}")

    def _spawn(self, job: Job, *args: str) -> None:
        """Start the trainer detached from this server's process tree (it survives the
        panel closing). ``running`` was already set by the caller; the launcher's pid
        stands in until the trainer writes its own."""
        job.path("cancel").unlink(missing_ok=True)
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        try:
            r = subprocess.run([sys.executable, "-m", "trainer.launch", str(job.path("log.txt")), *args, "--job", job.id],
                               cwd=str(PROJECT_ROOT), capture_output=True, text=True, creationflags=flags, env=env, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            job.report(state="failed", running=None, error=f"无法启动训练进程：{e}")
            raise HTTPException(500, "无法启动训练进程") from e
        if r.returncode != 0 or not r.stdout.strip().isdigit():
            job.report(state="failed", running=None, error=f"无法启动训练进程：{(r.stderr or r.stdout)[-300:]}")
            raise HTTPException(500, "无法启动训练进程")
        pid = int(r.stdout.strip())
        if not job.progress().get("pid"):
            job.report(pid=pid)
        logger.info(f"clone: started {' '.join(args)} for {job.id} (pid {pid})")

    def _summary(self, job: Job) -> dict:
        m = job.meta
        st = self._state(job)
        return {
            "id": job.id,
            "name": m.get("name", ""),
            "createdAt": m.get("createdAt", 0),
            "state": st["state"],
            "running": st.get("running"),
            "pct": st.get("pct"),
            "voiceSlot": m.get("voiceSlot"),
            "report": job.read("report.json"),
        }

    # ---------- jobs ----------
    def list_jobs(self):
        if not JOBS.exists():
            return {"jobs": [], "busy": None}
        jobs = [self._summary(Job(d.name)) for d in JOBS.iterdir() if d.is_dir() and (d / "job.json").exists() and JOB_ID.match(d.name)]
        jobs.sort(key=lambda j: j["createdAt"], reverse=True)
        return {"jobs": jobs, "busy": _running_job()}

    def create_job(self, body: CreateBody):
        name = body.name.strip()[:40]
        if not name:
            raise HTTPException(400, "请给声音起个名字")
        jid = time.strftime("%y%m%d%H%M%S") + uuid.uuid4().hex[:4]
        job = Job(jid)
        job.write("job.json", {"name": name, "createdAt": time.time(), "files": []})
        return self.get_job(jid)

    def get_job(self, job_id: str):
        job = self._job(job_id)
        st = self._state(job)
        log = ""
        try:
            log = "\n".join(job.path("log.txt").read_text(encoding="utf-8").splitlines()[-15:])
        except OSError:
            pass
        return {
            "id": job.id,
            "meta": job.meta,
            "progress": st,
            "report": job.read("report.json"),
            "clips": job.read("clips.json", []),
            "hasPreviewSource": job.path("preview_src.wav").exists() or bool(job.meta.get("previewFile")),
            "log": log,
            "busy": _running_job(),
        }

    def rename_job(self, job_id: str, body: CreateBody):
        with _jlock(job_id):
            job = self._job(job_id)
            job.update_meta(name=body.name.strip()[:40] or job.meta.get("name", ""))
        return self.get_job(job_id)

    def delete_job(self, job_id: str):
        with _jlock(job_id), audio_gpu_lock:
            job = self._job(job_id)
            if _running_job() == job.id:
                raise HTTPException(409, "正在处理中，请先取消")
            _dequeue(job.id)
            shutil.rmtree(job.dir, ignore_errors=True)
        return self.list_jobs()

    # ---------- material ----------
    def _save_upload(self, job: Job, up: UploadFile, prefix: str = "") -> tuple[str, str]:
        label = os.path.basename(up.filename or "audio")
        ext = os.path.splitext(label)[1].lower()
        if ext not in AUDIO_EXTS | VIDEO_EXTS:
            raise HTTPException(400, f"不支持的文件类型：{label}")
        name = f"{prefix}{uuid.uuid4().hex[:10]}{ext}"
        with open(job.path("raw", name), "wb") as f:
            shutil.copyfileobj(up.file, f)
        return name, label

    async def upload_files(self, job_id: str, files: list[UploadFile] = File(...), kind: str = Form("file")):
        job = self._job(job_id)
        if _running_job() == job.id or _is_queued(job.id):
            raise HTTPException(409, "正在处理或排队中，请稍后再添加素材")
        if len(files) > MAX_UPLOAD_FILES:
            raise HTTPException(400, f"一次最多添加 {MAX_UPLOAD_FILES} 个文件")
        added: list[dict] = []
        for up in files:
            name, label = await asyncio.to_thread(self._save_upload, job, up)
            is_video = os.path.splitext(label)[1].lower() in VIDEO_EXTS
            added.append({
                "name": name,
                "label": "麦克风录音 " + time.strftime("%H:%M:%S") if kind == "record" else label,
                "kind": "record" if kind == "record" else "video" if is_video else "audio",
                # video soundtracks usually carry music/effects; clean files and recordings do not
                "separate": is_video,
                "bytes": job.path("raw", name).stat().st_size,
            })
        with _jlock(job_id):
            meta = job.meta
            cur_files = list(meta.get("files", []))
            cur_files.extend(added)
            job.update_meta(files=cur_files)
            self._mark_stale(job)
        return self.get_job(job_id)

    def _mark_stale(self, job: Job) -> None:
        """Material changed: the clips need preparing again."""
        job.update_meta(materialsDirty=True)
        job.report(state="draft", message="素材有变动，需要重新处理", pct=0)

    def _file(self, job: Job, name: str) -> tuple[dict, list]:
        files = job.meta.get("files", [])
        for f in files:
            if f["name"] == name:
                return f, files
        raise HTTPException(404, "文件不存在")

    def patch_file(self, job_id: str, name: str, body: FilePatch):
        with _jlock(job_id):
            job = self._job(job_id)
            f, files = self._file(job, name)
            if body.separate is not None and body.separate != f.get("separate"):
                if _running_job() == job.id or _is_queued(job.id):
                    raise HTTPException(409, "正在处理或排队中")
                f["separate"] = body.separate
                self._mark_stale(job)
            if body.label:
                f["label"] = body.label.strip()[:80]
            job.update_meta(files=files)
        return self.get_job(job_id)

    def delete_file(self, job_id: str, name: str):
        with _jlock(job_id):
            job = self._job(job_id)
            if _running_job() == job.id or _is_queued(job.id):
                raise HTTPException(409, "正在处理或排队中")
            f, files = self._file(job, name)
            files.remove(f)
            job.path("raw", f["name"]).unlink(missing_ok=True)
            job.update_meta(files=files)
            self._mark_stale(job)
        return self.get_job(job_id)

    async def set_preview_source(self, job_id: str, file: UploadFile = File(...)):
        job = self._job(job_id)
        name, label = await asyncio.to_thread(self._save_upload, job, file, "preview_")
        with _jlock(job_id):
            old = job.meta.get("previewFile")
            if old:
                job.path("raw", old).unlink(missing_ok=True)
            job.path("preview_src.wav").unlink(missing_ok=True)
            job.update_meta(previewFile=name, previewLabel=label)
        return self.get_job(job_id)

    def clear_preview_source(self, job_id: str):
        with _jlock(job_id):
            job = self._job(job_id)
            old = job.meta.get("previewFile")
            if old:
                job.path("raw", old).unlink(missing_ok=True)
            job.path("preview_src.wav").unlink(missing_ok=True)
            job.update_meta(previewFile=None, previewLabel=None)
        return self.get_job(job_id)

    # ---------- stages ----------
    def prepare(self, job_id: str):
        with _jlock(job_id):
            job = self._job(job_id)
            if _running_job() == job.id or _is_queued(job.id):
                raise HTTPException(409, "这个作业正在处理或排队中")
            if not job.meta.get("files"):
                raise HTTPException(400, "请先添加素材")
            self._submit_gpu_stage(job, "prepare", "prepare", init_pct=0.0, init_msg="开始处理素材")
        return self.get_job(job_id)

    def patch_clips(self, job_id: str, body: ClipsPatch):
        from trainer.prepare import report

        with _jlock(job_id):
            job = self._job(job_id)
            if _running_job() == job.id or _is_queued(job.id):
                raise HTTPException(409, "正在处理或排队中")
            ids = set(body.ids)
            clips = job.read("clips.json", [])
            for c in clips:
                if c["id"] in ids:
                    c["excluded"] = body.excluded
                    if not body.excluded:
                        c["auto"] = None
            job.write("clips.json", clips)
            report(job)
        return self.get_job(job_id)

    def clip_audio(self, job_id: str, clip_id: str):
        job = self._job(job_id)
        if not CLIP_ID.match(clip_id):
            raise HTTPException(404)
        p = job.path("clips", "40k", f"{clip_id}.wav")
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="audio/wav")

    def preview_audio(self, job_id: str, fname: str):
        job = self._job(job_id)
        if fname == "source.wav":
            p = job.path("preview_src.wav")
        elif re.match(r"^e\d{1,5}\.wav$", fname):
            p = job.path("preview", fname)
        else:
            raise HTTPException(404)
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    def train(self, job_id: str, body: TrainBody):
        from trainer.train import PRESETS

        with _jlock(job_id):
            job = self._job(job_id)
            if _running_job() == job.id or _is_queued(job.id):
                raise HTTPException(409, "这个作业正在处理或排队中")
            if not body.consent:
                raise HTTPException(400, "请先确认你有权使用这个声音")
            if body.preset not in PRESETS:
                raise HTTPException(400, "未知的训练档位")
            st = self._state(job)["state"]
            rep = job.read("report.json") or {}
            if st in ("new", "draft") or job.meta.get("materialsDirty") or not rep:
                raise HTTPException(400, "素材还没处理，请先点「处理素材」")
            if not rep.get("canTrain"):
                raise HTTPException(400, "可用素材太少，至少需要 3 分钟人声")
            job.update_meta(consent=True, preset=body.preset)
            progress = job.progress()
            resume = (not body.fresh and progress.get("checkpoints") and progress.get("preset") == body.preset
                      and job.path("ckpt", "G_last.pth").exists() and job.path("ckpt", "D_last.pth").exists())
            extra = ["--epochs", str(max(1, min(2000, body.epochs)))] if body.epochs else []
            init_pct = 0.0 if not resume else float(job.progress().get("pct", 0) or 0.0)
            self._submit_gpu_stage(
                job,
                "train",
                "train", "--preset", body.preset, *extra, *([] if resume else ["--fresh"]),
                init_pct=init_pct,
                init_msg="正在启动训练",
            )
        return self.get_job(job_id)

    def cancel(self, job_id: str):
        with _jlock(job_id), audio_gpu_lock:
            job = self._job(job_id)
            if _dequeue(job.id):
                job.report(state="cancelled", message="已取消排队", running=None, pid=None)
                return self.get_job(job_id)
            job.path("cancel").touch()
            pid = job.progress().get("pid")

        def reaper():
            for _ in range(100):  # the trainer checks the flag every step; give it 10 s
                if not _pid_alive(pid):
                    return
                time.sleep(0.1)
            logger.warning(f"clone: killing stuck trainer {pid}")
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, creationflags=0x08000000)
            else:
                try:
                    os.kill(int(pid), 9)
                except OSError:
                    pass
            job.report(state="cancelled", message="已取消", running=None, pid=None)

        if pid:
            threading.Thread(target=reaper, daemon=True).start()
        return self.get_job(job_id)

    async def finalize(self, job_id: str, body: FinalizeBody):
        from trainer.export import export

        job = self._job(job_id)
        if _running_job() == job.id or _is_queued(job.id):
            raise HTTPException(409, "正在处理或排队中")
        if not any(c.get("epoch") == body.epoch for c in job.progress().get("checkpoints", [])):
            raise HTTPException(400, "请选择这个作业已有的训练版本")
        try:
            model, index = await asyncio.to_thread(export, job, body.epoch)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        name = (body.name or job.meta.get("name") or "克隆声音").strip()[:40]
        rep = job.read("report.json") or {}
        vt, m = self.vt, self.vt.m
        msm = m.modelSlotManager
        old = job.meta.get("voiceSlot")
        replace = old is not None and (info := msm.get_slot_info(old)) is not None and info.voiceChangerType \
            and job.meta.get("voiceSlotName") == info.name and old != vt.s.modelSlotIndex
        slot = msm.allocate_slot(preferred=old if replace else None)
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        files: list[LoadModelParamFile] = []
        clean_names: dict[str, str] = {}
        staged_paths: list[str] = []
        for path, kind in ((model, "rvcModel"), (index, "rvcIndex")):
            fname = os.path.basename(path)
            staged = f"{uuid.uuid4().hex[:8]}_{fname}"
            dst = os.path.join(UPLOAD_DIR, staged)
            await asyncio.to_thread(shutil.copyfile, path, dst)
            staged_paths.append(dst)
            clean_names[staged] = fname
            files.append(LoadModelParamFile(name=staged, kind=kind, dir=""))
        try:
            await m.load_model(LoadModelParams(voiceChangerType="RVC", slot=slot, files=files, params={"clean_names": clean_names}))
        except Exception as e:
            logger.exception(e)
            msm.release_slot(slot)
            for p in staged_paths:
                if os.path.exists(p):
                    os.unlink(p)
            msm.getAllSlotInfo(reload=True)
            raise HTTPException(500, f"加入声音库失败: {e}")
        msm.getAllSlotInfo(reload=True)
        msm.update_model_info(slot, "name", name)
        if rep.get("f0"):
            msm.update_model_info(slot, "targetPitchHz", float(rep["f0"]))
        msm.update_model_info(slot, "defaultIndexRatio", 0.5)
        msm.update_model_info(slot, "credit", "声音克隆（本机训练）")
        with _jlock(job_id):
            job.update_meta(voiceSlot=slot, voiceSlotName=name, exportedEpoch=body.epoch)
            job.report(state="done", message=f"已加入声音库：{name}")
        return {"slot": slot, "job": self.get_job(job_id), **vt.voices()}
