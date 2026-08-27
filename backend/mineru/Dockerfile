# syntax=docker/dockerfile:1.7
# MinerU GPU service image for CUDA 13.0-compatible environments.
# This follows MinerU's Docker guidance: vllm/vllm-openai:v0.21.0 is the CUDA 13.0-compatible base.
FROM vllm/vllm-openai:v0.21.0
# If your host driver only supports CUDA 12.9, switch to:
# FROM vllm/vllm-openai:v0.21.0-cu129

ENTRYPOINT []

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=0 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=600 \
    HF_HOME=/root/.cache/huggingface \
    TRANSFORMERS_CACHE=/root/.cache/huggingface \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_ENABLE_HF_TRANSFER=0 \
    MODELSCOPE_CACHE=/root/.cache/modelscope

WORKDIR /workspace/mineru

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    sed -i 's|http://archive.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g; s|http://security.ubuntu.com/ubuntu/|https://mirrors.aliyun.com/ubuntu/|g' /etc/apt/sources.list || true \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       python3 python3-pip python3-venv python-is-python3 \
       git curl ca-certificates \
       build-essential pkg-config \
       libgl1 libgomp1 libglib2.0-0 \
    && python3 -m pip --version

RUN --mount=type=cache,target=/root/.cache/pip \
    python3 -m pip install --timeout 600 --prefer-binary -U pip setuptools wheel \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn \
    && python3 -m pip install --timeout 600 --prefer-binary mineru \
      -i https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn

EXPOSE 30000 7860 8000 8002

CMD ["/bin/bash", "-lc", "mineru-openai-server --host ${MINERU_OPENAI_HOST:-0.0.0.0} --port ${MINERU_OPENAI_PORT:-30000} --gpu-memory-utilization ${MINERU_GPU_MEMORY_UTILIZATION:-0.45}; status=$?; echo '[mineru] mineru-openai-server exited with code' $status '; entering sleep infinity'; exec sleep infinity"]
