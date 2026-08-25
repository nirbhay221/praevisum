# The desk, as one image.
#
# Python 3.11 to match the VM this actually runs on, because a container that
# reproduces a different interpreter than production reproduces the wrong
# thing.
FROM python:3.11-slim

# Unbuffered, so the reasoning trace and the call transcript reach the log as
# they happen rather than when a buffer fills. On a live call that difference
# is the whole value of the log.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first and alone, so editing source does not reinstall the world.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY assets/hold.wav ./assets/hold.wav

# The database is built from the schema files at first run rather than baked
# in. A committed database would ship one dealer's customers inside the image,
# and every schema file here is idempotent so this is safe on a mounted volume
# that already has one.
RUN python -c "import sys; sys.path.insert(0, '.'); from src import db; db.init()"

# Reference data is NOT baked in. It is public federal data that
# scripts/load_reference.py fetches from EnergyStar and the CPSC, and pinning a
# snapshot inside an image is how a catalogue silently goes stale.

EXPOSE 8080

# One worker on purpose. A call holds a websocket for its whole duration and
# the in-process session and index are not shared between workers, so a second
# worker would answer half the calls without the corpus the first one loaded.
CMD ["python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
