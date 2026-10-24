# syntax=docker/dockerfile:1
# The optional speech-to-text service: faster-whisper on CPU (int8) behind
# POST /transcribe. Kept out of the api image so the api stays lean. It holds
# no secrets and is reachable only on the compose `internal` network.
FROM python:3.12-slim-bookworm

ARG FASTER_WHISPER_VERSION=1.2.1
# PyAV 19 dropped an argument faster-whisper's decoder still passes.
ARG AV_VERSION=18.1.0
RUN pip install --no-cache-dir "faster-whisper==${FASTER_WHISPER_VERSION}" "av==${AV_VERSION}"

# /models is the model cache volume, owned by the service so a fresh volume is writable.
RUN useradd --system --uid 10002 --no-create-home stt \
    && mkdir -p /models && chown stt:stt /models

COPY ringkeeper_stt /opt/ringkeeper_stt

ENV PYTHONPATH=/opt \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/models \
    HF_HUB_DISABLE_TELEMETRY=1

USER stt
EXPOSE 9000
HEALTHCHECK --interval=10s --timeout=3s --start-period=600s --retries=3 \
    CMD ["python", "-c", "import urllib.request, sys; sys.exit(urllib.request.urlopen('http://127.0.0.1:9000/health').status != 200)"]
ENTRYPOINT ["python", "-m", "ringkeeper_stt"]
