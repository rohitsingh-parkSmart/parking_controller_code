# ParkSmart controller — Raspberry Pi / any Linux host with network
# access to the UHF readers, Modbus relay, and LED panel(s).
#
# python:3.11-slim is a multi-arch image (amd64, arm64, armv7), so
# this builds natively on a Raspberry Pi (arm64/armv7) as well as a
# regular x86_64 dev machine. When built via buildx for a foreign
# platform, BuildKit picks the right base automatically — see
# build.sh / Makefile.
#
# Both dependencies (pymodbus, paho-mqtt) are pure Python, so there
# is no compiler toolchain to install and no reason for a multi-stage
# build here.

FROM python:3.11-slim

# Logs are print()/logging-based and depend on being flushed
# immediately, not buffered inside the container — critical for
# `docker logs` / `docker compose logs -f` to show output live.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first, separately from app code, so Docker's
# layer cache skips this step on every rebuild unless requirements
# actually changed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code. core/ is deliberately not copied: nothing in the
# running app imports it (main.py only imports devices.*), so it
# would just be dead weight in the image.
COPY main.py .
COPY devices/ ./devices/

# config/ and data/ are intentionally NOT copied into the image —
# they're mounted as volumes at runtime (see docker-compose.yml)
# so config.json, tags.json, occupancy state, vehicle session
# history, and logs all persist across image rebuilds and don't
# get baked into the image (which would also risk shipping real
# credentials inside the image).
RUN mkdir -p config data/occupancy data/logs

# Run as a non-root user inside the container. Note: this does NOT
# require any special hardware/device passthrough, since this app
# only makes outbound TCP/Modbus-TCP connections over the network
# to the readers/relay/LED panels — no direct GPIO or serial device
# access on the host is needed.
#
# The uid/gid matter: ./data is bind-mounted from the host and the
# app must be able to write occupancy files, vehicle_sessions.json
# and data/logs/parksmart.log. The log handler is created at import
# time, so an unwritable data/ dir means the container dies on
# startup rather than degrading. 1000:1000 is the default first
# user on Raspberry Pi OS, which owns the checkout in the normal
# case; override these if your host user differs
# (`id -u` / `id -g` on the host).
ARG APP_UID=1000
ARG APP_GID=1000

RUN groupadd --gid ${APP_GID} parksmart 2>/dev/null || true \
    && useradd --uid ${APP_UID} --gid ${APP_GID} \
        --create-home --shell /bin/bash parksmart 2>/dev/null \
    || true
RUN chown -R ${APP_UID}:${APP_GID} /app
USER ${APP_UID}:${APP_GID}

# Provenance, stamped by build.sh / CI. Kept last so changing the
# version does not invalidate any of the layers above it.
ARG VERSION=dev
ARG VCS_REF=unknown
ARG BUILD_DATE=unknown

LABEL org.opencontainers.image.title="ParkSmart controller" \
      org.opencontainers.image.description="UHF tag -> Modbus relay + LED panel parking controller" \
      org.opencontainers.image.source="https://github.com/rohitsingh-parkSmart/parking_controller_code" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}"

CMD ["python3", "main.py"]
