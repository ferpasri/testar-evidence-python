import hashlib
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from testar_evidence import EvidenceError, EvidenceReader, JsonlStore, Manifest
from testar_evidence.codec import dumps


def event(identity, kind, data):
    return {"schema_version": 2, "id": identity, "event": {"kind": kind, "data": data}}


def started():
    return event("start", "run_started", {
        "id": "run", "producer": "example", "source_run": "1", "properties": {},
    })


def observed(artifacts=None, properties=None):
    return event("visit", "observed", {
        "id": "visit", "run": "run", "sequence": 1, "timestamp_micros": 0,
        "state": {"id": "state", "properties": properties or {}, "widgets": [], "capture": "filtered"},
        "candidates": [], "candidate_scope": "unknown", "artifacts": artifacts or [],
    })


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "model"

    def test_record_reopen_exact_numbers_and_idempotent_retry(self):
        reading = {"source.number": Decimal("0.1234567890123456789012345"), "source.integer": 2 ** 100}
        with JsonlStore.create(self.root, Manifest("example")) as store:
            self.assertTrue(store.record(started()))
            self.assertTrue(store.record(observed(properties=reading)))
            before = (self.root / "evidence.jsonl").read_bytes()
            self.assertFalse(store.record(observed(properties=reading)))
            self.assertEqual(before, (self.root / "evidence.jsonl").read_bytes())
        with JsonlStore.open(self.root) as reopened:
            self.assertEqual(reopened.model.observations["visit"]["state"]["properties"], reading)
            self.assertEqual(reopened.model.event_count, 2)
            detached = reopened.model
            detached.apply(event("finish", "run_finished", {"run": "run", "reason": "completed"}))
            self.assertNotEqual(detached.summary(), reopened.model.summary())

    def test_invalid_event_does_not_change_model_or_log(self):
        with JsonlStore.create(self.root, Manifest("example")) as store:
            before = (self.root / "evidence.jsonl").read_bytes()
            with self.assertRaises(EvidenceError):
                store.record(observed())
            self.assertEqual(store.model.event_count, 0)
            self.assertEqual(before, (self.root / "evidence.jsonl").read_bytes())

    def test_artifacts_deduplicate_verify_and_preserve_bytes(self):
        data = b"captured artifact bytes"
        with JsonlStore.create(self.root, Manifest("example")) as store:
            reference = store.put_artifact(data, "application/octet-stream")
            self.assertEqual(reference["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(reference, store.put_artifact(data, "application/octet-stream"))
            self.assertEqual(data, store.read_artifact(reference))
            store.record(started())
            store.record(observed([reference]))
        self.assertEqual(EvidenceReader.read(self.root).event_count, 2)
        (self.root / reference["path"]).write_bytes(b"changed")
        with self.assertRaises(EvidenceError):
            EvidenceReader.read(self.root)

    def test_missing_artifact_is_rejected_before_append(self):
        reference = {"id": "missing", "path": "artifacts/missing.bin", "media_type": "application/octet-stream",
                     "sha256": "a" * 64, "width": None, "height": None}
        with JsonlStore.create(self.root, Manifest("example")) as store:
            store.record(started())
            with self.assertRaises(EvidenceError):
                store.record(observed([reference]))
            self.assertEqual(store.model.event_count, 1)

    def test_writer_lock_excludes_open_create_and_recovery(self):
        with JsonlStore.create(self.root, Manifest("example")):
            for operation in (lambda: JsonlStore.open(self.root),
                              lambda: JsonlStore.create(self.root, Manifest("other")),
                              lambda: JsonlStore.recover_tail(self.root)):
                with self.assertRaises(EvidenceError) as caught:
                    operation()
                self.assertEqual(caught.exception.code, "locked")
        with JsonlStore.open(self.root):
            pass

    def test_existing_evidence_is_preserved_and_closed_store_rejects_writes(self):
        store = JsonlStore.create(self.root, Manifest("example"))
        store.close()
        before = (self.root / "manifest.json").read_bytes()
        with self.assertRaises(EvidenceError):
            JsonlStore.create(self.root, Manifest("other"))
        self.assertEqual(before, (self.root / "manifest.json").read_bytes())
        with self.assertRaises(EvidenceError):
            store.record(started())

    def test_tail_recovery_is_explicit_and_preserves_completed_events(self):
        with JsonlStore.create(self.root, Manifest("example")) as store:
            store.record(started())
        log = self.root / "evidence.jsonl"
        prefix = log.read_bytes()
        with log.open("ab") as output:
            output.write(b'{"unfinished":')
        with self.assertRaises(EvidenceError):
            JsonlStore.open(self.root)
        self.assertEqual(log.read_bytes(), prefix + b'{"unfinished":')
        self.assertEqual(JsonlStore.recover_tail(self.root), len(b'{"unfinished":'))
        self.assertEqual(prefix, log.read_bytes())
        self.assertEqual(EvidenceReader.read(self.root).event_count, 1)

    def test_recovery_does_not_remove_completed_corrupt_records(self):
        with JsonlStore.create(self.root, Manifest("example")):
            pass
        log = self.root / "evidence.jsonl"
        log.write_bytes(b'{"invalid":true}\nunterminated')
        before = log.read_bytes()
        with self.assertRaises(EvidenceError):
            JsonlStore.recover_tail(self.root)
        self.assertEqual(before, log.read_bytes())

    def test_partial_write_invalidates_writer_until_explicit_recovery(self):
        with JsonlStore.create(self.root, Manifest("example")) as store:
            def fail_write(output, data):
                output.write(data[:5])
                raise OSError("simulated interrupted write")

            with patch("testar_evidence.storage._write_all", side_effect=fail_write):
                with self.assertRaises(OSError):
                    store.record(started())
            self.assertEqual(store.model.event_count, 0)
            with self.assertRaises(EvidenceError) as caught:
                store.record(started())
            self.assertEqual(caught.exception.code, "writer_failed")
        self.assertEqual(JsonlStore.recover_tail(self.root), 5)
        with JsonlStore.open(self.root) as store:
            self.assertTrue(store.record(started()))

    def test_symlinked_log_is_rejected(self):
        with JsonlStore.create(self.root, Manifest("example")):
            pass
        log = self.root / "evidence.jsonl"
        external = Path(self.temporary.name) / "external.jsonl"
        external.write_text(dumps(started()) + "\n", encoding="utf-8")
        log.unlink()
        try:
            log.symlink_to(external)
        except OSError:
            self.skipTest("This environment does not permit creating symbolic links")
        with self.assertRaises(EvidenceError):
            EvidenceReader.read(self.root)

    def test_interrupted_artifact_write_can_be_retried(self):
        with JsonlStore.create(self.root, Manifest("example")) as store:
            def interrupted(output, data):
                output.write(data[:3])
                raise OSError("interrupted artifact")

            with patch("testar_evidence.storage._write_all", side_effect=interrupted):
                with self.assertRaises(OSError):
                    store.put_artifact(b"complete artifact", "application/octet-stream")
            self.assertEqual(list((self.root / "artifacts").iterdir()), [])
            reference = store.put_artifact(b"complete artifact", "application/octet-stream")
            self.assertEqual(store.read_artifact(reference), b"complete artifact")

    def test_artifact_reader_verifies_the_exact_bytes_it_returns(self):
        with JsonlStore.create(self.root, Manifest("example")) as store:
            reference = store.put_artifact(b"verified", "application/octet-stream")
            with patch.object(Path, "read_bytes", side_effect=[b"verified", b"different"]) as reading:
                self.assertEqual(store.read_artifact(reference), b"verified")
                self.assertEqual(reading.call_count, 1)

    def test_interrupted_creation_preserves_a_retryable_directory(self):
        with patch("testar_evidence.storage._write_all", side_effect=OSError("creation interrupted")):
            with self.assertRaises(OSError):
                JsonlStore.create(self.root, Manifest("example"))
        self.assertFalse((self.root / "manifest.json").exists())
        with JsonlStore.create(self.root, Manifest("example")) as store:
            self.assertEqual(store.model.event_count, 0)

    def test_keyboard_interrupt_invalidates_the_writer(self):
        with JsonlStore.create(self.root, Manifest("example")) as store:
            def interrupted(output, data):
                output.write(data[:2])
                raise KeyboardInterrupt()

            with patch("testar_evidence.storage._write_all", side_effect=interrupted):
                with self.assertRaises(KeyboardInterrupt):
                    store.record(started())
            with self.assertRaises(EvidenceError) as caught:
                store.record(started())
            self.assertEqual(caught.exception.code, "writer_failed")
        self.assertEqual(JsonlStore.recover_tail(self.root), 2)

    def test_invalid_utf8_is_reported_without_changing_the_file(self):
        with JsonlStore.create(self.root, Manifest("example")):
            pass
        log = self.root / "evidence.jsonl"
        log.write_bytes(b"\xff\n")
        with self.assertRaises(EvidenceError):
            EvidenceReader.read(self.root)
        self.assertEqual(log.read_bytes(), b"\xff\n")


if __name__ == "__main__":
    unittest.main()
