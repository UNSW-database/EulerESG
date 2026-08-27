# EulerESG PaddleOCR-VL v1.6 + vLLM 连续批处理

本版本保留：

- PaddleOCR-VL v1.6，本地版面检测 + 独立 vLLM 识别服务
- compose 自动模型预检
- 默认两个 PaddleOCR worker，每个 worker 同时处理一个 7 页任务
- SSE 前端进度
- 前端不显示后端日志/PaddleOCR 内部细节
- `PADDLEOCR_PAGE_BATCH_SIZE: '7'`
- `PADDLEOCR_BATCH_TIMEOUT_SECONDS: '1200'`
- `PADDLEOCR_VL_REC_MAX_CONCURRENCY: '16'`
- `PADDLEOCR_PREFLIGHT_ON_START: 'true'`
- vLLM `gpu-memory-utilization: 0.40`
- Paddle 空闲 30 分钟后释放，最多处理 500 个任务后才重启

## GPU 推理批处理

`PADDLEOCR_PAGE_BATCH_SIZE` 只是每个 Redis 任务包含的 PDF 页数，不是 GPU batch。
当前 worker 会把版面检测得到的文本、表格、公式区域并发发送到
`paddleocr-vlm-server`，vLLM 在 GPU 上执行真正的连续批处理：

- 客户端并发：`PADDLEOCR_VL_REC_MAX_CONCURRENCY`，RTX 3090 初始值 16。
- 服务端序列上限：`max-num-seqs: 32`。
- 单次调度 token 上限：`max-num-batched-tokens: 32768`。
- 显存目标：`gpu-memory-utilization: 0.40`，为同卡版面模型和 4B reranker 留空间。
- 页级并发：两个 worker 各处理一个 7 页任务，因此最多 14 页同时处于解析流程。

backend 启动前由 `backend-model-init` 检查 embedding 与 reranker 缓存。模型保存在
`hf_cache` volume，报告运行期间只使用本地缓存，不会在 OCR 完成后临时联网下载。

先观察稳定性和吞吐。如 GPU 峰值仍低且确认不会与 4B reranker 同时驻留，可将
`backend/paddleocr_vl/vllm_config.yaml` 中的显存利用率依次调为 0.45、0.50，
每次修改后重建 `paddleocr-vlm-server`。共享 GPU 不建议超过 0.55。

## 本次修复

日志中出现：

```text
Input batch does not exist: /workspace/uploads/paddleocr_vl_jobs/.../pages_0001_0007.pdf
```

原因是 Redis 入队速度可能快于 Docker bind mount 文件可见性。backend 已经生成 batch 文件并入队，但 worker 立即消费时，文件在 worker 容器里还没完全可见。

本版本新增：

1. backend 原子写入 batch PDF：先写 `.tmp`，fsync 后再 rename。
2. backend 为每个 batch PDF 写入 `.ready` 标记。
3. worker 必须等待 PDF 文件和 `.ready` 标记都可见才开始解析。
4. worker 等待时间改为 `PADDLEOCR_INPUT_WAIT_SECONDS: '120'`。
5. backend 入队前会校验 batch 文件可见性，配置为 `PADDLEOCR_SPLIT_VISIBILITY_WAIT_SECONDS: '60'`。

## 启动

```bash
docker compose down --remove-orphans
docker compose up -d redis
docker compose exec redis redis-cli FLUSHDB
docker compose stop redis
docker compose up --build
```

## 期望日志

对于 116 页 PDF，应该看到：

```text
PADDLEOCR_PAGE_BATCH_SIZE='7', effective=7
pages=116, units=17, batch_size=7
开始 page-batch ... pages=1-7
```

前端页面只显示用户友好的进度，不显示后端日志、worker id、文件系统路径或 PaddleOCR batch 明细。
