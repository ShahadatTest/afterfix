# syntax=docker/dockerfile:1

# ---------- Frontend production build ----------
FROM node:24.22.1-bookworm-slim AS frontend
WORKDIR /src/frontend
RUN test "$(node --version)" = "v24.22.1"
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --ignore-scripts
COPY frontend/ ./
# The three existing native checks resolve imports relative to this directory.
RUN set -eu; \
    for name in api-session case-state scenario-state; do \
      file="$(find scripts -maxdepth 1 -type f -name "*${name}*" -print -quit)"; \
      test -n "$file"; \
      node "$file"; \
    done
RUN npm run build

# ---------- Rust verifier (tests gate the binary) ----------
FROM rust:1.98.1-slim-bookworm AS rust-verifier
WORKDIR /src/rust-verifier
COPY rust-verifier/Cargo.toml rust-verifier/Cargo.lock ./
COPY rust-verifier/src ./src
COPY rust-verifier/tests ./tests
RUN cargo test --locked
RUN cargo build --release --locked

# ---------- Shared backend base ----------
FROM python:3.14.10-slim-bookworm AS backendbase
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp
WORKDIR /app
RUN groupadd --gid 10001 afterfix \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin afterfix
COPY requirements-bootstrap.txt requirements.txt ./
RUN python -m pip install --require-hashes --only-binary=:all: -r requirements-bootstrap.txt \
 && python -m pip install -r requirements.txt \
 && python -m pip check
COPY app ./app
COPY --from=rust-verifier /src/rust-verifier/target/release/afterfix-verifier /app/rust-verifier/target/release/afterfix-verifier
COPY --from=frontend /src/frontend/dist /opt/afterfix/frontend
RUN chmod 0555 /app/rust-verifier/target/release/afterfix-verifier \
 && test -x /app/rust-verifier/target/release/afterfix-verifier \
 && test -f /opt/afterfix/frontend/index.html

# ---------- QA target: Linux test results gate CI artifact creation ----------
FROM backendbase AS qa
COPY tests ./tests
COPY tools ./tools
COPY docs ./docs
COPY rust-verifier/Cargo.toml rust-verifier/Cargo.lock ./rust-verifier/
COPY rust-verifier/src ./rust-verifier/src
COPY rust-verifier/tests ./rust-verifier/tests
RUN python -m pytest tests -q

# ---------- Dedicated hardened web image ----------
FROM caddy:2.10.2-alpine AS web
# The upstream binary has a file capability which prevents exec under ALL-drop.
# Strip it only in this derived image; port8080 requires no capabilities.
RUN setcap -r /usr/bin/caddy && test -z "$(getcap /usr/bin/caddy)"
USER 10001:10001
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

# ---------- Final runtime (no tests, toolchains or development files) ----------
FROM backendbase AS runtime
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
