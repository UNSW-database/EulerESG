# PaddleOCR-VL v1.6 Redis worker.
FROM ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-vl:latest-nvidia-gpu

WORKDIR /workspace/paddleocr_vl_service

RUN python -m pip install --no-cache-dir loguru redis

COPY logging_config.py parse_core.py worker.py preflight.py /workspace/paddleocr_vl_service/

CMD ["python", "/workspace/paddleocr_vl_service/worker.py"]
