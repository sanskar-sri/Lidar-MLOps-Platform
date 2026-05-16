# ── Base image ────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── System dependencies ────────────────────────────────────────────────────────
# open3d (headless) needs libGL, libgomp, and X11 stubs.
# laspy[lazrs] needs a Rust-compiled lazrs wheel — no extra libs needed.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libgomp1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Tell open3d (and any Mesa fallback) to use software rendering — no GPU needed.
ENV LIBGL_ALWAYS_SOFTWARE=1

# ── Working directory ──────────────────────────────────────────────────────────
WORKDIR /app

# ── Python dependencies ────────────────────────────────────────────────────────
# Copy requirements first so Docker can cache this layer independently.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ── Application code ───────────────────────────────────────────────────────────
COPY . .

# ── Runtime defaults ───────────────────────────────────────────────────────────
# Override with -e / docker-compose environment: section as needed.
ENV DASH_HOST=0.0.0.0
ENV DASH_PORT=8051
ENV DASH_DEBUG=0
# B2 credentials — must be supplied at runtime via --env-file or environment.
# ENV B2_KEY_ID=
# ENV B2_APPLICATION_KEY=
# ENV B2_BUCKET_NAME=Building-Identification-MLS
ENV LOCAL_STAGING_DIR=/app/data/local_staging

# ── Port ───────────────────────────────────────────────────────────────────────
EXPOSE 8051

# ── Entrypoint ─────────────────────────────────────────────────────────────────
CMD ["python", "app.py"]
