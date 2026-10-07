# ClipForge deployment package

This package is a single-service FastAPI + FFmpeg + faster-whisper starter.

## GitHub upload
Upload these files to the root of the `ClipForge` repository:
- `main.py`
- `requirements.txt`
- `Dockerfile`
- `render.yaml`
- `README.md`

Do not upload the ZIP itself. Extract it first.

## Render
Create a Web Service from the GitHub repo. Render can use the included Dockerfile. The service starts on Render's `$PORT`.

## Important
The service is a starter, not a full production SaaS. Video processing consumes CPU, RAM and disk and may be slow or unsuitable for free hosting. Add a queue, persistent/object storage, cleanup, authentication and rate limiting before public scale. Process only videos you are authorized to use and comply with the source platform's terms.
