# syntax=docker/dockerfile:1.7
# CUDA 13.0-compatible backend image.
# PDF parsing is handled by a separate PaddleOCR-VL v1.6 service.
FROM vllm/vllm-openai:v0.21.0
# If you must use CUDA 12.9 instead, switch to:
# FROM vllm/vllm-openai:v0.21.0-cu129

# vllm/vllm-openai images define their own server entrypoint; reset it for FastAPI backend.
ENTRYPOINT []

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/backend/src \
    PIP_NO_CACHE_DIR=0 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    HF_HOME=/root/.cache/huggingface \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_ENABLE_HF_TRANSFER=0

# Embedding/rerank model defaults. docker-compose.yml can override these.
ENV EMBEDDING_MODEL=microsoft/harrier-oss-v1-0.6b \
    LOCAL_EMBEDDINGS_MODEL_PATH=microsoft/harrier-oss-v1-0.6b \
    LOCAL_EMBEDDINGS_DEVICE=cuda \
    LOCAL_EMBEDDINGS_MODEL_DTYPE=float16 \
    EMBEDDING_QUERY_PROMPT_ENABLED=1 \
    EMBEDDING_FORCE_QUERY_PROMPT=1 \
    RERANK_MODEL=Qwen/Qwen3-Reranker-4B \
    LOCAL_RERANKER_MODEL_PATH=Qwen/Qwen3-Reranker-4B \
    LOCAL_RERANKER_DEVICE=cuda \
    LOCAL_RERANKER_MODEL_DTYPE=float16 \
    LOCAL_RERANKER_TOP_K=98 \
    LOCAL_RERANKER_BATCH_SIZE=8 \
    LOCAL_RERANKER_MAX_CHARS_PER_PASSAGE=8192 \
    HIPPO_ENABLED=true \
    HIPPO_EMBEDDING_MODEL=facebook/contriever \
    HIPPO_WARM_INDEX_IN_BACKGROUND=true \
    HIPPO_MAX_CACHED_INDEXES=1

# Backend submits PaddleOCR-VL page batches through Redis.
ENV PARSER_BACKEND=paddleocr \
    PADDLEOCR_VL_TIMEOUT=10800

WORKDIR /workspace/backend

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    sed -i 's|http://archive.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g; s|http://security.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip python3-venv python-is-python3 \
       git curl ca-certificates \
       build-essential pkg-config \
       libgl1 libgomp1 libglib2.0-0 \
       libnuma1 libnuma-dev \
    && python3 -m pip --version

COPY requirements.docker.txt /workspace/backend/requirements.docker.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install --timeout 600 --prefer-binary -U pip "setuptools>=77,<81" wheel \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn \
    && python3 -m pip install --timeout 600 --prefer-binary -r /workspace/backend/requirements.docker.txt \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn \
    && python3 -m pip install --timeout 600 --prefer-binary hipporag==2.0.0a4 gritlm==1.0.2 --no-deps \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn

COPY . /workspace/backend

EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "esg_encoding.api:app", "--host", "0.0.0.0", "--port", "8000"]
