from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import parse_core
import worker


class _FakeResult:
    def save_to_markdown(self, save_path: str) -> None:
        Path(save_path, "result.md").write_text("parsed", encoding="utf-8")


class _FakePipeline:
    def __init__(self, result_count: int = 1) -> None:
        self.input_path = ""
        self.options = {}
        self.result_count = result_count

    def predict(self, input_path: str, **options):
        self.input_path = input_path
        self.options = options
        for _ in range(self.result_count):
            yield _FakeResult()


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes = {}
        self.queue_length = 0
        self.lists = {}
        self.sorted_sets = {}

    def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hdel(self, key, field):
        return int(self.hashes.get(key, {}).pop(field, None) is not None)

    def hincrby(self, key, field, amount):
        current = int(self.hashes.setdefault(key, {}).get(field, 0))
        self.hashes[key][field] = str(current + amount)

    def expire(self, key, ttl):  # noqa: ARG002
        return True

    def llen(self, key):
        if key in self.lists:
            return len(self.lists[key])
        return self.queue_length if key == "paddleocr:parse" else 0

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        if end == -1:
            end = len(values) - 1
        return list(values[start : end + 1])

    def lrem(self, key, count, value):
        values = self.lists.setdefault(key, [])
        removed = 0
        kept = []
        for item in values:
            if item == value and (count == 0 or removed < count):
                removed += 1
            else:
                kept.append(item)
        self.lists[key] = kept
        return removed

    def zadd(self, key, mapping, nx=False):
        values = self.sorted_sets.setdefault(key, {})
        added = 0
        for member, score in mapping.items():
            if nx and member in values:
                continue
            if member not in values:
                added += 1
            values[member] = float(score)
        return added

    def zscore(self, key, member):
        return self.sorted_sets.get(key, {}).get(member)

    def zrem(self, key, member):
        return int(self.sorted_sets.get(key, {}).pop(member, None) is not None)

    def zrangebyscore(self, key, minimum, maximum):
        lower = float("-inf") if minimum == "-inf" else float(minimum)
        upper = float("inf") if maximum == "+inf" else float(maximum)
        return [
            member
            for member, score in sorted(
                self.sorted_sets.get(key, {}).items(), key=lambda item: item[1]
            )
            if lower <= score <= upper
        ]

    def zcount(self, key, minimum, maximum):
        return len(self.zrangebyscore(key, minimum, maximum))

    def execute_command(self, command, source, destination, src, dest, timeout):  # noqa: ARG002
        self.assert_command = (command, src, dest)
        if command != "BLMOVE" or src != "LEFT" or dest != "RIGHT":
            raise AssertionError("unexpected queue command")
        source_values = self.lists.setdefault(source, [])
        if not source_values:
            return None
        value = source_values.pop(0)
        self.lists.setdefault(destination, []).append(value)
        return value

    def eval(self, script, number_of_keys, *values):
        keys = values[:number_of_keys]
        args = values[number_of_keys:]
        marker = script.strip().splitlines()[0]
        if marker == "-- ACK_PROCESSING_PAYLOAD":
            processing, leases, owners = keys
            payload = args[0]
            removed = self.lrem(processing, 1, payload)
            self.zrem(leases, payload)
            self.hdel(owners, payload)
            return removed
        if marker == "-- REQUEUE_PROCESSING_PAYLOAD":
            if "ARGV[3]" in script:
                raise AssertionError("model-error requeue script must be unconditional")
            processing, queue, leases, owners = keys
            payload = args[0]
            removed = self.lrem(processing, 1, payload)
            if removed:
                self.lpush(queue, payload)
            self.zrem(leases, payload)
            self.hdel(owners, payload)
            return removed
        if marker == "-- RECOVER_EXPIRED_PAYLOAD":
            if "ARGV[3] == 'requeue'" not in script:
                raise AssertionError("expired recovery script must honor terminal discard")
            processing, queue, leases, owners = keys
            payload, now, action = args
            score = self.zscore(leases, payload)
            if score is None or score > float(now):
                return 0
            removed = self.lrem(processing, 1, payload)
            if removed and action == "requeue":
                self.lpush(queue, payload)
            self.zrem(leases, payload)
            self.hdel(owners, payload)
            return removed
        raise AssertionError(f"unexpected script: {marker}")


class PaddleRuntimeTests(unittest.TestCase):
    def test_prediction_overrides_are_allowlisted_and_type_checked(self) -> None:
        allowed = {
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": True,
            "use_layout_detection": False,
            "use_chart_recognition": True,
            "use_seal_recognition": True,
            "use_ocr_for_image_block": True,
            "min_pixels": 200704,
            "max_pixels": 1605632,
            "max_new_tokens": 3072,
        }

        options = parse_core._prediction_options(allowed)

        for key, expected in allowed.items():
            self.assertEqual(options[key], expected)

        with self.assertRaisesRegex(ValueError, "unsupported prediction option"):
            parse_core._prediction_options({"use_queues": True})
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            parse_core._prediction_options({"use_doc_unwarping": "true"})
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            parse_core._prediction_options({"max_pixels": True})
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            parse_core._prediction_options({"max_new_tokens": "many"})

    def test_prediction_override_numeric_values_are_clamped_to_safe_bounds(self) -> None:
        lower = parse_core._prediction_options(
            {
                "min_pixels": -1,
                "max_pixels": -1,
                "max_new_tokens": -1,
            }
        )
        upper = parse_core._prediction_options(
            {
                "min_pixels": 40_000_000,
                "max_pixels": 50_000_000,
                "max_new_tokens": 50_000,
            }
        )
        inverted = parse_core._prediction_options(
            {
                "min_pixels": 200704,
                "max_pixels": 1000,
            }
        )

        self.assertEqual(lower["min_pixels"], 784)
        self.assertEqual(lower["max_pixels"], 784)
        self.assertEqual(lower["max_new_tokens"], 128)
        self.assertEqual(upper["min_pixels"], 4_014_080)
        self.assertEqual(upper["max_pixels"], 4_014_080)
        self.assertEqual(upper["max_new_tokens"], 8192)
        self.assertEqual(inverted["max_pixels"], inverted["min_pixels"])

    def test_document_release_request_unloads_and_acknowledges_when_queue_is_idle(self) -> None:
        redis = _FakeRedis()
        redis.hashes["paddleocr:control:release"] = {
            "request_id": "release-1",
            "job_id": "job-1",
        }

        with patch.object(worker, "release_pipeline") as release:
            request_id = worker._maybe_release_requested(
                redis,
                worker_id="worker-1",
                queue_name="paddleocr:parse",
                last_request_id="",
            )

        self.assertEqual(request_id, "release-1")
        release.assert_called_once()
        self.assertEqual(
            redis.hashes["paddleocr:control:release"]["ack:worker-1"],
            "release-1",
        )

    def test_document_release_waits_while_another_batch_is_queued(self) -> None:
        redis = _FakeRedis()
        redis.queue_length = 1
        redis.hashes["paddleocr:control:release"] = {
            "request_id": "release-2",
        }

        with patch.object(worker, "release_pipeline") as release:
            request_id = worker._maybe_release_requested(
                redis,
                worker_id="worker-1",
                queue_name="paddleocr:parse",
                last_request_id="",
            )

        self.assertEqual(request_id, "")
        release.assert_not_called()

    def test_fifo_claim_moves_payload_to_durable_processing_list(self) -> None:
        redis = _FakeRedis()
        redis.rpush("paddleocr:parse", "first")
        redis.rpush("paddleocr:parse", "second")

        claimed = worker._claim_payload(
            redis,
            "paddleocr:parse",
            "paddleocr:parse:processing",
            timeout=1,
        )

        self.assertEqual(claimed, "first")
        self.assertEqual(redis.lists["paddleocr:parse"], ["second"])
        self.assertEqual(redis.lists["paddleocr:parse:processing"], ["first"])
        self.assertEqual(redis.assert_command, ("BLMOVE", "LEFT", "RIGHT"))

    def test_ack_removes_processing_payload_and_lease_metadata(self) -> None:
        redis = _FakeRedis()
        processing = "paddleocr:parse:processing"
        payload = '{"job_id":"job-ack","unit_index":1}'
        redis.rpush(processing, payload)

        with patch.dict(os.environ, {"PADDLEOCR_PROCESSING_LEASE_SECONDS": "120"}):
            worker._renew_processing_lease(
                redis,
                worker_id="worker-ack",
                queue_name="paddleocr:parse",
                processing_queue_name=processing,
                payload_raw=payload,
                payload={"job_id": "job-ack", "unit_index": 1},
            )
        acknowledged = worker._ack_claimed_payload(
            redis,
            processing_queue_name=processing,
            payload_raw=payload,
        )

        self.assertTrue(acknowledged)
        self.assertEqual(redis.lists[processing], [])
        self.assertIsNone(redis.zscore(f"{processing}:leases", payload))
        self.assertNotIn(payload, redis.hashes[f"{processing}:owners"])
        heartbeat = redis.hashes["paddleocr:worker:worker-ack"]
        self.assertEqual(heartbeat["status"], "processing")
        self.assertEqual(heartbeat["job_id"], "job-ack")

    def test_expired_processing_payload_is_requeued_but_active_lease_is_not(self) -> None:
        redis = _FakeRedis()
        queue = "paddleocr:parse"
        processing = f"{queue}:processing"
        expired = '{"job_id":"expired"}'
        active = '{"job_id":"active"}'
        redis.rpush(processing, expired)
        redis.rpush(processing, active)
        redis.zadd(f"{processing}:leases", {expired: 99.0, active: 201.0})
        redis.hset(f"{processing}:owners", mapping={expired: "old", active: "live"})

        recovered = worker._recover_expired_processing(
            redis,
            queue_name=queue,
            processing_queue_name=processing,
            now=100.0,
        )

        self.assertEqual(recovered, 1)
        self.assertEqual(redis.lists[queue], [expired])
        self.assertEqual(redis.lists[processing], [active])
        self.assertIsNone(redis.zscore(f"{processing}:leases", expired))
        self.assertEqual(redis.zscore(f"{processing}:leases", active), 201.0)

    def test_unleased_processing_payload_gets_grace_before_recovery(self) -> None:
        redis = _FakeRedis()
        queue = "paddleocr:parse"
        processing = f"{queue}:processing"
        orphan = '{"job_id":"claim-window"}'
        redis.rpush(processing, orphan)

        with patch.dict(os.environ, {"PADDLEOCR_PROCESSING_LEASE_SECONDS": "120"}):
            recovered = worker._recover_expired_processing(
                redis,
                queue_name=queue,
                processing_queue_name=processing,
                now=100.0,
            )

        self.assertEqual(recovered, 0)
        self.assertEqual(redis.lists[processing], [orphan])
        self.assertEqual(redis.zscore(f"{processing}:leases", orphan), 220.0)

        recovered_after_grace = worker._recover_expired_processing(
            redis,
            queue_name=queue,
            processing_queue_name=processing,
            now=221.0,
        )
        self.assertEqual(recovered_after_grace, 1)
        self.assertEqual(redis.lists[processing], [])
        self.assertEqual(redis.lists[queue], [orphan])

    def test_expired_terminal_batch_is_acked_without_reexecution(self) -> None:
        redis = _FakeRedis()
        queue = "paddleocr:parse"
        processing = f"{queue}:processing"
        payload = json.dumps(
            {
                "task_type": "page_batch",
                "job_id": "job-result-written",
                "unit_index": 2,
            },
            separators=(",", ":"),
        )
        redis.rpush(processing, payload)
        redis.zadd(f"{processing}:leases", {payload: 99.0})
        redis.hset(
            "paddleocr:task:job-result-written:batch:0002",
            mapping={"status": "success"},
        )

        recovered = worker._recover_expired_processing(
            redis,
            queue_name=queue,
            processing_queue_name=processing,
            now=100.0,
        )

        self.assertEqual(recovered, 1)
        self.assertEqual(redis.lists[processing], [])
        self.assertEqual(redis.lists.get(queue, []), [])
        self.assertIsNone(redis.zscore(f"{processing}:leases", payload))

    def test_model_error_requeue_is_atomic_and_prioritized(self) -> None:
        redis = _FakeRedis()
        queue = "paddleocr:parse"
        processing = f"{queue}:processing"
        payload = '{"job_id":"retry"}'
        redis.rpush(queue, "later")
        redis.rpush(processing, payload)
        redis.zadd(f"{processing}:leases", {payload: 200.0})
        redis.hset(f"{processing}:owners", mapping={payload: "worker"})

        requeued = worker._requeue_claimed_payload(
            redis,
            queue_name=queue,
            processing_queue_name=processing,
            payload_raw=payload,
        )

        self.assertTrue(requeued)
        self.assertEqual(redis.lists[queue], [payload, "later"])
        self.assertEqual(redis.lists[processing], [])
        self.assertIsNone(redis.zscore(f"{processing}:leases", payload))

    def test_document_release_waits_for_processing_payload_without_lease(self) -> None:
        redis = _FakeRedis()
        processing = "paddleocr:parse:processing"
        redis.hashes["paddleocr:control:release"] = {"request_id": "release-3"}
        redis.rpush(processing, "active-payload")

        with patch.object(worker, "release_pipeline") as release:
            request_id = worker._maybe_release_requested(
                redis,
                worker_id="worker-1",
                queue_name="paddleocr:parse",
                processing_queue_name=processing,
                last_request_id="",
            )

        self.assertEqual(request_id, "")
        release.assert_not_called()

    def test_document_release_waits_for_active_lease_without_list_item(self) -> None:
        redis = _FakeRedis()
        processing = "paddleocr:parse:processing"
        redis.hashes["paddleocr:control:release"] = {"request_id": "release-4"}
        redis.zadd(f"{processing}:leases", {"claim-window": 9_999_999_999.0})

        with patch.object(worker, "release_pipeline") as release:
            request_id = worker._maybe_release_requested(
                redis,
                worker_id="worker-1",
                queue_name="paddleocr:parse",
                processing_queue_name=processing,
                last_request_id="",
            )

        self.assertEqual(request_id, "")
        release.assert_not_called()

    def test_remote_vllm_options_enable_bounded_continuous_batching(self) -> None:
        env = {
            "PADDLEOCR_VL_REC_BACKEND": "vllm-server",
            "PADDLEOCR_VL_REC_SERVER_URL": "http://paddleocr-vlm-server:8118/v1",
            "PADDLEOCR_VL_REC_MAX_CONCURRENCY": "16",
        }
        with patch.dict(os.environ, env, clear=False):
            options = parse_core._pipeline_init_options()

        self.assertEqual(options["vl_rec_backend"], "vllm-server")
        self.assertEqual(options["vl_rec_server_url"], "http://paddleocr-vlm-server:8118/v1")
        self.assertEqual(options["vl_rec_max_concurrency"], 16)
        self.assertFalse(options["use_queues"])

    def test_remote_vllm_preflight_uses_marker_not_local_vlm_weights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "PADDLEOCR_VL_REC_BACKEND": "vllm-server",
                "PADDLEOCR_REQUIRE_PREFLIGHT_MARKER": "true",
            },
            clear=False,
        ), patch.object(parse_core, "MODEL_CACHE_ROOT", Path(tmp)):
            marker = parse_core._model_ready_marker_path()
            self.assertFalse(parse_core._model_cache_ready_for_worker())
            marker.write_text("{}", encoding="utf-8")
            self.assertTrue(parse_core._model_cache_ready_for_worker())

    def test_single_page_pdf_disables_internal_queue_and_bounds_vlm(self) -> None:
        fake_pipeline = _FakePipeline()
        env = {
            "PADDLEOCR_USE_INTERNAL_QUEUES": "false",
            "PADDLEOCR_LAYOUT_SHAPE_MODE": "rect",
            "PADDLEOCR_VLM_MIN_PIXELS": "112896",
            "PADDLEOCR_VLM_MAX_PIXELS": "1003520",
            "PADDLEOCR_VLM_MAX_NEW_TOKENS": "2048",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, env), patch.object(
            parse_core, "get_pipeline", return_value=fake_pipeline
        ):
            root = Path(tmp)
            source = root / "page.pdf"
            marker = root / "page.pdf.ready"
            source.write_bytes(b"single-page-pdf")
            marker.write_text("ready", encoding="utf-8")

            result = parse_core.parse_page_batch(
                source,
                job_id="job-1",
                batch_id="batch-1",
                output_root=root / "output",
                ready_path=marker,
            )

        self.assertEqual(result["status"], "success")
        self.assertFalse(fake_pipeline.options["use_queues"])
        self.assertEqual(fake_pipeline.options["max_pixels"], 1003520)
        self.assertEqual(fake_pipeline.options["max_new_tokens"], 2048)
        self.assertEqual(fake_pipeline.options["layout_shape_mode"], "rect")

    def test_page_batch_passes_bounded_prediction_overrides_to_pipeline(self) -> None:
        fake_pipeline = _FakePipeline()
        overrides = {
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": True,
            "use_chart_recognition": True,
            "use_ocr_for_image_block": True,
            "min_pixels": 200704,
            "max_pixels": 1605632,
            "max_new_tokens": 3072,
        }
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            parse_core, "get_pipeline", return_value=fake_pipeline
        ):
            root = Path(tmp)
            source = root / "page.pdf"
            marker = root / "page.pdf.ready"
            source.write_bytes(b"single-page-pdf")
            marker.write_text("ready", encoding="utf-8")

            parse_core.parse_page_batch(
                source,
                job_id="job-options",
                batch_id="batch-options",
                output_root=root / "output",
                ready_path=marker,
                prediction_options=overrides,
            )

        for key, expected in overrides.items():
            self.assertEqual(fake_pipeline.options[key], expected)

    def test_worker_forwards_payload_prediction_options_to_page_parser(self) -> None:
        redis = _FakeRedis()
        prediction_options = {
            "use_doc_orientation_classify": True,
            "use_doc_unwarping": True,
            "max_pixels": 1605632,
        }
        payload = {
            "task_type": "page_batch",
            "job_id": "job-options",
            "batch_id": "batch-options",
            "unit_index": 2,
            "total_units": 4,
            "start_page": 3,
            "end_page": 4,
            "total_pages": 8,
            "input_path": "/tmp/pages.pdf",
            "ready_path": "/tmp/pages.pdf.ready",
            "filename": "report.pdf",
            "prediction_options": prediction_options,
            "parse_pass": 2,
            "render_zoom": 2.0,
            "requested_render_zoom": 2.5,
        }

        with patch.object(
            worker,
            "parse_page_batch",
            return_value={"status": "success"},
        ) as parse_batch, patch.object(worker, "_env_int", return_value=0):
            worker._handle_page_batch(redis, "worker-options", payload)

        parse_batch.assert_called_once_with(
            "/tmp/pages.pdf",
            filename="report.pdf",
            job_id="job-options",
            batch_id="batch-options",
            unit_index=2,
            total_units=4,
            start_page=3,
            end_page=4,
            total_pages=8,
            ready_path="/tmp/pages.pdf.ready",
            prediction_options=prediction_options,
        )
        state = redis.hashes["paddleocr:task:job-options:batch:0002"]
        self.assertEqual(state["status"], "success")
        result_metadata = json.loads(state["result_json"])
        self.assertEqual(result_metadata["parse_pass"], 2)
        self.assertEqual(result_metadata["render_zoom"], 2.0)
        self.assertEqual(result_metadata["requested_render_zoom"], 2.5)

    def test_eight_page_batch_preserves_all_page_markers(self) -> None:
        fake_pipeline = _FakePipeline(result_count=8)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            parse_core, "get_pipeline", return_value=fake_pipeline
        ):
            root = Path(tmp)
            source = root / "pages_0001_0008.pdf"
            marker = root / "pages_0001_0008.pdf.ready"
            source.write_bytes(b"eight-page-pdf")
            marker.write_text("ready", encoding="utf-8")

            result = parse_core.parse_page_batch(
                source,
                job_id="job-8",
                batch_id="batch-1",
                start_page=1,
                end_page=8,
                total_pages=8,
                output_root=root / "output",
                ready_path=marker,
            )
            markdown = Path(result["batch_markdown_path"]).read_text(encoding="utf-8")

        self.assertEqual(result["result_count"], 8)
        for page in range(1, 9):
            self.assertIn(f"<!-- Page {page} |", markdown)

    def test_seven_page_batch_fails_when_pipeline_returns_only_six_pages(self) -> None:
        fake_pipeline = _FakePipeline(result_count=6)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            parse_core, "get_pipeline", return_value=fake_pipeline
        ):
            root = Path(tmp)
            source = root / "pages_0001_0007.pdf"
            marker = root / "pages_0001_0007.pdf.ready"
            source.write_bytes(b"seven-page-pdf")
            marker.write_text("ready", encoding="utf-8")

            with self.assertRaises(parse_core.PageBatchIncompleteError):
                parse_core.parse_page_batch(
                    source,
                    job_id="job-7",
                    batch_id="batch-1",
                    start_page=1,
                    end_page=7,
                    total_pages=7,
                    output_root=root / "output",
                    ready_path=marker,
                )
            self.assertFalse(any(root.glob("output/**/batch.md")))

    def test_page_batch_fails_when_one_result_has_empty_markdown(self) -> None:
        fake_pipeline = _FakePipeline(result_count=7)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            parse_core, "get_pipeline", return_value=fake_pipeline
        ), patch.object(
            parse_core,
            "_save_result_markdown",
            side_effect=["parsed"] * 6 + [""],
        ):
            root = Path(tmp)
            source = root / "pages_0001_0007.pdf"
            marker = root / "pages_0001_0007.pdf.ready"
            source.write_bytes(b"seven-page-pdf")
            marker.write_text("ready", encoding="utf-8")

            with self.assertRaises(parse_core.PageBatchIncompleteError):
                parse_core.parse_page_batch(
                    source,
                    job_id="job-empty",
                    batch_id="batch-1",
                    start_page=1,
                    end_page=7,
                    total_pages=7,
                    output_root=root / "output",
                    ready_path=marker,
                )
            self.assertFalse(any(root.glob("output/**/batch.md")))

    def test_timeout_writes_structured_batch_metadata(self) -> None:
        redis = _FakeRedis()
        payload = {
            "task_type": "page_batch",
            "job_id": "job-timeout",
            "unit_index": 3,
            "total_units": 10,
            "start_page": 3,
            "end_page": 3,
            "total_pages": 10,
            "input_path": "/tmp/page.pdf",
        }
        with patch.object(
            worker,
            "parse_page_batch",
            side_effect=worker.PageBatchTimeoutError("timed out"),
        ), patch.object(worker, "_env_int", return_value=1200):
            with self.assertRaises(worker.PageBatchTimeoutError):
                worker._handle_page_batch(redis, "worker-1", payload)

        state = redis.hashes["paddleocr:task:job-timeout:batch:0003"]
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["stage"], "timeout")
        self.assertEqual(state["error_type"], "batch_timeout")
        self.assertEqual(state["timeout_seconds"], "1200")
        self.assertEqual(state["start_page"], "3")
        self.assertEqual(state["end_page"], "3")


if __name__ == "__main__":
    unittest.main()
