FROM python:3.12-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    TELEX_RADIO_MODELS=/models/kokoro

ARG KOKORO_RELEASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1
RUN mkdir -p /models/kokoro && cd /models/kokoro \
    && curl -fsSLO "$KOKORO_RELEASE/kokoro-v1.0.onnx" \
    && curl -fsSLO "$KOKORO_RELEASE/voices-v1.0.bin" \
    && echo "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a  kokoro-v1.0.onnx" | sha256sum -c - \
    && echo "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d  voices-v1.0.bin" | sha256sum -c -

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra radio --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --extra radio \
    && python -c "from telex.radio.engine import from_env; from_env().synth('Warm-up sentence.')"
CMD ["python", "-m", "telex.radio.watch"]
