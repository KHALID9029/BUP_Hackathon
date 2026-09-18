# --- Stage 1: build the web UI (static files only; Node is not in the final image) ---
FROM node:24-slim AS ui
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci
COPY frontend ./frontend
COPY Problem_doc/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json ./Problem_doc/
RUN cd frontend && npm run build

# --- Stage 2: API service ---
FROM python:3.12-slim
WORKDIR /srv
# Install dependencies against a stub package first, so this large layer is cached (and not
# re-uploaded) when only the code changes; the real code is copied afterwards.
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install --no-cache-dir . && rm -rf app
COPY app ./app
COPY --from=ui /build/frontend/dist ./frontend/dist
ENV PORT=8000 \
    FRONTEND_DIST=/srv/frontend/dist
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
