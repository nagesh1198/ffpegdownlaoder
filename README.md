# Relay Media Downloader

A small FastAPI app with a browser-based interface for converting a direct media
URL to MP3 audio or MP4 video with FFmpeg.

## Requirements

- Python 3.10 or newer
- FFmpeg and FFprobe available on `PATH`

## Run on Windows

From the project root, install the backend dependencies and start the app:

```powershell
python -m pip install -r backend\requirements.txt
python -m uvicorn main:app --app-dir backend --reload
```

Open <http://127.0.0.1:8000>. The frontend and API are served from the same
FastAPI process.

## API

- `GET /api/health` — check that the API and FFmpeg tools are available.
- `POST /api/jobs` — create a conversion job with `{"url":"https://…","format":"mp3"}`.
  Set `format` to `mp4` for video output.
- `GET /api/jobs/{job_id}` — check job status and conversion progress.
- `GET /api/jobs/{job_id}/download` — download the completed file.
- `GET /downloads/talentsprint-e-content.mp4` — download the verified saved MP4.

Downloads are written to the operating system's temporary directory. Use direct
HTTP or HTTPS media links that the server can access.

MP4 conversion first copies compatible video/audio streams without re-encoding,
then falls back to H.264/AAC encoding when needed. The backend checks the output
duration before marking a job complete.
