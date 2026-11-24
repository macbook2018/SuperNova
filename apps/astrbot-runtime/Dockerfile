# Official AstrBot source pinned to an audited release commit. Never use latest.
FROM python:3.12-slim

ARG ASTRBOT_COMMIT=61335414433db721b3f7ecc93f90deafe0a59d78
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ASTRBOT_ROOT=/var/lib/astrbot

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ffmpeg gcc g++ make libffi-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/AstrBotDevs/AstrBot.git /opt/astrbot \
    && cd /opt/astrbot \
    && git checkout --detach "${ASTRBOT_COMMIT}" \
    && test "$(git rev-parse HEAD)" = "${ASTRBOT_COMMIT}"

WORKDIR /opt/astrbot
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir --no-deps -e . \
    && python -c "import astrbot; assert astrbot.__version__ == '4.29.4'"

COPY apps/astrbot-runtime/bridge.py /opt/supernova/bridge.py

# AstrBot is restricted to a per-instance bind mount. No Docker socket or model keys.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin astrbot \
    && mkdir -p /var/lib/astrbot /opt/supernova \
    && chown -R astrbot:astrbot /var/lib/astrbot

USER astrbot
WORKDIR /opt/astrbot
CMD ["python", "/opt/supernova/bridge.py"]
