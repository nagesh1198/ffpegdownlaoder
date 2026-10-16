FROM python:3.11-slim

# Install system dependencies including FFmpeg and FFprobe
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy backend requirements and install Python dependencies
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy backend and frontend source files
COPY backend /app/backend
COPY frontend /app/frontend

# Environment variable for dynamic port binding on Render
ENV PORT=8000
EXPOSE 8000

# Start Uvicorn bound to 0.0.0.0 and the dynamic port provided by Render
CMD ["sh", "-c", "python -m uvicorn main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000}"]
