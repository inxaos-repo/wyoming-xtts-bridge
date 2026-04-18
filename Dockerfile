FROM python:3.12-slim

LABEL org.opencontainers.image.source="https://github.com/inxaos-repo/wyoming-xtts-bridge"
LABEL org.opencontainers.image.description="Wyoming protocol bridge for XTTS v2 TTS with voice cloning"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .

ENTRYPOINT ["python", "server.py"]
CMD ["--xtts-url", "http://localhost:8020", "--voice-wav", "/data/reference.wav", "--uri", "tcp://0.0.0.0:10400"]

EXPOSE 10400
