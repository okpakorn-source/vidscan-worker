# railway-worker/Dockerfile
# Deploy บน Railway.app — รัน yt-dlp ดึงวิดีโอจาก xhslink

FROM python:3.11-slim

# install yt-dlp + ffmpeg
RUN apt-get update && apt-get install -y \
    ffmpeg curl wget \
    && pip install yt-dlp --break-system-packages \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt --break-system-packages

COPY worker.py .

ENV PORT=8000
EXPOSE 8000

CMD ["python", "worker.py"]
