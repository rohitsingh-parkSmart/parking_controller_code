# ParkSmart controller — Raspberry Pi / any Linux host with network
# access to the UHF readers, Modbus relay, and LED panel(s).
#
# python:3.11-slim is a multi-arch image (amd64, arm64, armv7), so
# this builds natively on a Raspberry Pi (arm64/armv7) as well as a
# regular x86_64 dev machine — no cross-compilation needed.

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

# Application code.
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
RUN useradd --create-home --shell /bin/bash parksmart \
    && chown -R parksmart:parksmart /app
USER parksmart

CMD ["python3", "main.py"]