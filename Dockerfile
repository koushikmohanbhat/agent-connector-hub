# One image for every target (local, ECS Fargate, Cloud Run, Kubernetes).
#   docker build -t connector-hub --build-arg EXTRAS=postgres,otel .
# All settings come from the environment at runtime (see .env.example).
FROM python:3.12-slim AS build
ARG EXTRAS=postgres,otel
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY packs ./packs
COPY profiles ./profiles
COPY templates ./templates
COPY hub.yaml ./
COPY deploy ./deploy
RUN pip install --no-cache-dir --prefix=/install ".[${EXTRAS}]"

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 hub
COPY --from=build /install /usr/local
COPY --chown=hub:hub hub.yaml /app/hub.yaml
COPY --chown=hub:hub packs /app/packs
COPY --chown=hub:hub profiles /app/profiles
USER hub
WORKDIR /app
ENV PORT=8080 PYTHONUNBUFFERED=1 HUB_STORE=sqlite:////tmp/hub.db
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz')"
CMD ["sh", "-c", "hub serve /app --port ${PORT}"]
