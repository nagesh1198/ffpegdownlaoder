import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
ASSETS_DIR = FRONTEND_DIR / "assets"
SAVED_MP4_PATH = Path(__file__).resolve().parent / "temp" / "talentsprint-e-content.mp4"
DOWNLOAD_DIR = Path(tempfile.gettempdir()) / "ffmpeg-downloader"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Auto-cleanup settings: files older than 1 hour (3600s) are automatically deleted
RETENTION_SECONDS = int(os.environ.get("DOWNLOAD_RETENTION_SECONDS", 3600))
CLEANUP_INTERVAL_SECONDS = 300  # Check every 5 minutes
MAX_JOBS = 25  # Limit job history to prevent storage exhaustion

jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


def cleanup_expired_jobs_and_files() -> None:
    now = time.time()

    # 1. Remove expired jobs from memory and delete their files
    with jobs_lock:
        expired_job_ids = []
        for job_id, job in jobs.items():
            completed_at = job.get("completed_at")
            created_at = job.get("created_at", now)
            # Remove completed/failed jobs past TTL, or stale processing jobs older than 4 hours
            if completed_at and (now - completed_at > RETENTION_SECONDS):
                expired_job_ids.append(job_id)
            elif job.get("status") == "processing" and (now - created_at > 14400):
                expired_job_ids.append(job_id)

        for job_id in expired_job_ids:
            job = jobs.pop(job_id, None)
            if job and job.get("path"):
                Path(job["path"]).unlink(missing_ok=True)

    # 2. Clean any orphaned download/log files in temp directory older than RETENTION_SECONDS
    try:
        for file_path in DOWNLOAD_DIR.iterdir():
            if file_path.is_file():
                try:
                    if now - file_path.stat().st_mtime > RETENTION_SECONDS:
                        file_path.unlink(missing_ok=True)
                except OSError:
                    pass
    except OSError:
        pass


def periodic_cleanup_worker(stop_event: threading.Event) -> None:
    while not stop_event.wait(CLEANUP_INTERVAL_SECONDS):
        cleanup_expired_jobs_and_files()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: clean any leftover files from previous server runs
    cleanup_expired_jobs_and_files()
    stop_event = threading.Event()
    worker = threading.Thread(target=periodic_cleanup_worker, args=(stop_event,), daemon=True)
    worker.start()
    try:
        yield
    finally:
        stop_event.set()


app = FastAPI(title="FFmpeg Downloader", version="1.0.0", lifespan=lifespan)


class ProcessRequest(BaseModel):
    url: HttpUrl
    format: Literal["mp3", "mp4"] = "mp3"


def get_duration(url: str) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                "-protocol_whitelist",
                "file,http,https,tcp,tls,crypto",
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=60,
        )
        return float(result.stdout.strip())
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0.0


def update_job(job_id: str, **changes: object) -> None:
    with jobs_lock:
        jobs[job_id].update(changes)


def run_ffmpeg_process(url: str, job_id: str, output_format: str) -> None:
    output_path = DOWNLOAD_DIR / f"{job_id}.{output_format}"
    log_path = DOWNLOAD_DIR / f"{job_id}.log"
    duration = get_duration(url)
    update_job(job_id, duration=duration)

    try:
        attempts = 2 if output_format == "mp4" else 1
        for attempt in range(attempts):
            output_path.unlink(missing_ok=True)
            command = [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-protocol_whitelist",
                "http,https,tcp,tls,crypto",
                "-i",
                url,
            ]
            if output_format == "mp3":
                command.extend(["-vn", "-codec:a", "libmp3lame", "-b:a", "192k"])
            elif attempt == 0:
                command.extend(["-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart"])
            else:
                command.extend(
                    [
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        "-c:a",
                        "aac",
                        "-movflags",
                        "+faststart",
                    ]
                )
            command.extend(["-progress", "pipe:1", "-nostats", str(output_path)])

            with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=log_file,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if process.stdout is None:
                    raise RuntimeError("Could not read FFmpeg progress output.")

                try:
                    for line in process.stdout:
                        key, separator, value = line.strip().partition("=")
                        if not separator:
                            continue
                        if key == "out_time_us" and duration > 0:
                            try:
                                elapsed = int(value) / 1_000_000
                            except ValueError:
                                continue
                            percent = min(99, max(0, int(elapsed / duration * 100)))
                            update_job(job_id, percent=percent)
                        elif key == "progress" and value == "end":
                            update_job(job_id, percent=99)
                    return_code = process.wait()
                except BaseException:
                    process.kill()
                    process.wait()
                    raise

            details = log_path.read_text(encoding="utf-8", errors="replace").strip()
            if return_code != 0:
                message = details[-1200:] or "FFmpeg could not process this media URL."
            elif not output_path.is_file() or output_path.stat().st_size == 0:
                message = "FFmpeg finished without creating an output file."
            else:
                output_duration = get_duration(str(output_path))
                tolerance = max(5.0, duration * 0.01)
                if output_duration > 0 and (duration <= 0 or output_duration >= duration - tolerance):
                    update_job(
                        job_id,
                        status="complete",
                        percent=100,
                        path=str(output_path),
                        completed_at=time.time(),
                    )
                    return
                if output_duration <= 0:
                    message = "FFprobe could not verify the output file duration."
                else:
                    message = (
                        f"Output is incomplete: {output_duration:.0f} seconds of "
                        f"{duration:.0f} seconds."
                    )

            if attempt + 1 < attempts:
                continue
            raise RuntimeError(message)
    except Exception as error:
        output_path.unlink(missing_ok=True)
        update_job(job_id, status="failed", error=str(error), completed_at=time.time())
    finally:
        log_path.unlink(missing_ok=True)


def public_job(job_id: str, job: dict) -> dict:
    return {
        "job_id": job_id,
        "status": job["status"],
        "percent": job["percent"],
        "format": job["format"],
        "duration": job["duration"],
        "error": job["error"],
    }


@app.get("/api/health")
def health() -> dict:
    with jobs_lock:
        active_jobs = sum(1 for j in jobs.values() if j.get("status") == "processing")
        total_jobs = len(jobs)
    return {
        "status": "ok",
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "ffprobe_available": shutil.which("ffprobe") is not None,
        "active_jobs": active_jobs,
        "total_jobs": total_jobs,
        "retention_seconds": RETENTION_SECONDS,
    }


@app.post("/api/jobs", status_code=202)
def create_job(request: ProcessRequest) -> dict:
    if not shutil.which("ffmpeg"):
        raise HTTPException(status_code=503, detail="FFmpeg is not installed or is not on PATH.")

    with jobs_lock:
        if len(jobs) >= MAX_JOBS:
            finished = [job_id for job_id, job in jobs.items() if job["status"] != "processing"]
            # Evict oldest finished jobs first
            finished.sort(key=lambda jid: jobs[jid].get("completed_at") or 0)
            for old_id in finished[: len(jobs) - MAX_JOBS + 1]:
                old_job = jobs.pop(old_id)
                if old_job.get("path"):
                    Path(old_job["path"]).unlink(missing_ok=True)
            if len(jobs) >= MAX_JOBS:
                raise HTTPException(
                    status_code=503,
                    detail="The download queue is full. Please try again shortly.",
                )

        job_id = uuid.uuid4().hex
        jobs[job_id] = {
            "status": "processing",
            "percent": 0,
            "format": request.format,
            "path": None,
            "error": None,
            "duration": 0.0,
            "created_at": time.time(),
            "completed_at": None,
        }

    worker = threading.Thread(
        target=run_ffmpeg_process,
        args=(str(request.url), job_id, request.format),
        daemon=True,
    )
    worker.start()
    return {"job_id": job_id, "status": "processing"}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Download job not found.")
        return public_job(job_id, job)


@app.get("/api/jobs/{job_id}/download")
def download_job(job_id: str) -> FileResponse:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Download job not found.")
        if job["status"] != "complete" or not job.get("path"):
            raise HTTPException(status_code=409, detail="This download is not ready yet.")
        output_path = Path(job["path"])
        output_format = job["format"]

    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="The downloaded file is no longer available.")
    return FileResponse(
        output_path,
        media_type="audio/mpeg" if output_format == "mp3" else "video/mp4",
        filename=f"download.{output_format}",
    )


@app.get("/downloads/talentsprint-e-content.mp4", include_in_schema=False)
def download_saved_mp4() -> FileResponse:
    if not SAVED_MP4_PATH.is_file():
        raise HTTPException(status_code=404, detail="The saved MP4 is no longer available.")
    return FileResponse(
        SAVED_MP4_PATH,
        media_type="video/mp4",
        filename="talentsprint-e-content.mp4",
    )


@app.get("/", include_in_schema=False)
def frontend() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
