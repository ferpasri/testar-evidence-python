"""Portable JSONL evidence storage and verified artifact bytes."""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from uuid import uuid4

from . import codec
from .errors import EvidenceError
from .model import EvidenceModel
from .records import validate_artifact, validate_manifest, validate_path

MAX_RECORD_BYTES = 16 * 1024 * 1024


def _safe_path(root: Path, relative: str) -> Path:
    validate_path(relative)
    current = root
    for part in relative.split("/"):
        current = current / part
        junction = getattr(current, "is_junction", lambda: False)
        if current.is_symlink() or junction():
            raise EvidenceError("unsafe_path", "Evidence paths cannot contain symbolic links or junctions")
        if not current.resolve().is_relative_to(root):
            raise EvidenceError("unsafe_path", "Evidence path leaves its directory")
    return current


def _regular(root: Path, relative: str) -> Path:
    path = _safe_path(root, relative)
    if not path.is_file():
        raise EvidenceError("missing_file", "Missing evidence file: " + relative)
    return path


def _write_all(output, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = output.write(remaining)
        if written is None or written <= 0:
            raise OSError("Evidence write made no progress")
        remaining = remaining[written:]
    output.flush()
    os.fsync(output.fileno())


def _artifact(root: Path, reference) -> bytes:
    reference = validate_artifact(codec.to_data(reference))
    path = _regular(root, reference["path"])
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != reference["sha256"].lower():
        raise EvidenceError("artifact_integrity", "Artifact SHA-256 mismatch: " + reference["path"])
    return data


def _decode(content: bytes):
    try:
        return codec.loads(content.decode("utf-8"))
    except UnicodeError as error:
        raise EvidenceError("invalid_record", "Evidence JSON must use valid UTF-8") from error


def _verify_artifacts(root: Path, envelope: dict) -> None:
    event = envelope["event"]
    if event["kind"] == "observed":
        for reference in event["data"]["artifacts"]:
            _artifact(root, reference)


class _WriterLock:
    def __init__(self, root: Path):
        self._file = _safe_path(root, ".writer.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self._file.seek(0, os.SEEK_END) == 0:
                    self._file.write(b"\0")
                    self._file.flush()
                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            self._file.close()
            raise EvidenceError("locked", "Another writer has this evidence directory open") from error

    def close(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            self._file.close()


class EvidenceReader:
    """Read an evidence directory without changing any of its files."""

    @staticmethod
    def _manifest(root: Path) -> dict:
        with _regular(root, "manifest.json").open("rb") as source:
            content = source.read(MAX_RECORD_BYTES + 1)
        if len(content) > MAX_RECORD_BYTES:
            raise EvidenceError("size_limit", "Manifest exceeds the size limit")
        return validate_manifest(_decode(content))

    @staticmethod
    def _replay(root: Path, model: EvidenceModel, allow_incomplete: bool = False) -> int:
        completed = 0
        with _regular(root, "evidence.jsonl").open("rb") as source:
            while True:
                line = source.readline(MAX_RECORD_BYTES + 2)
                if not line:
                    return completed
                if len(line.removesuffix(b"\n").removesuffix(b"\r")) > MAX_RECORD_BYTES:
                    raise EvidenceError("size_limit", "Evidence record exceeds the size limit")
                if not line.endswith(b"\n"):
                    if allow_incomplete:
                        return completed
                    raise EvidenceError("incomplete_tail", "Evidence ends with an incomplete line; recover it explicitly")
                envelope = _decode(line)
                model.validate(envelope)
                _verify_artifacts(root, envelope)
                model.apply(envelope)
                completed += len(line)

    @staticmethod
    def read(directory: str | Path) -> EvidenceModel:
        root = Path(directory).resolve(strict=True)
        model = EvidenceModel(EvidenceReader._manifest(root))
        EvidenceReader._replay(root, model)
        return model


class JsonlStore:
    """Append validated events while holding an exclusive writer lock."""

    def __init__(self, root: Path, lock: _WriterLock, output, model: EvidenceModel):
        self.directory = root
        self._lock = lock
        self._log = output
        self._model = model
        self._closed = False
        self._failed = False
        self._mutex = threading.RLock()

    @classmethod
    def create(cls, directory: str | Path, manifest) -> JsonlStore:
        manifest = validate_manifest(codec.to_data(manifest))
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        root = root.resolve(strict=True)
        lock = _WriterLock(root)
        created = []
        try:
            metadata = _safe_path(root, "manifest.json")
            log = _safe_path(root, "evidence.jsonl")
            if os.path.lexists(metadata) or os.path.lexists(log):
                raise EvidenceError("already_exists", "Evidence already exists; open it to continue recording")
            content = (codec.dumps(manifest) + "\n").encode("utf-8")
            if len(content) - 1 > MAX_RECORD_BYTES:
                raise EvidenceError("size_limit", "Manifest exceeds the size limit")
            with metadata.open("xb") as output:
                created.append(metadata)
                _write_all(output, content)
            output = log.open("xb", buffering=0)
            created.append(log)
            return cls(root, lock, output, EvidenceModel(manifest))
        except BaseException:
            try:
                for path in reversed(created):
                    path.unlink(missing_ok=True)
            finally:
                lock.close()
            raise

    @classmethod
    def open(cls, directory: str | Path) -> JsonlStore:
        root = Path(directory).resolve(strict=True)
        lock = _WriterLock(root)
        try:
            model = EvidenceReader.read(root)
            return cls(root, lock, _regular(root, "evidence.jsonl").open("ab", buffering=0), model)
        except BaseException:
            lock.close()
            raise

    def _require_open(self) -> None:
        if self._closed:
            raise EvidenceError("closed", "Evidence store is closed")

    def _require_writable(self) -> None:
        self._require_open()
        if self._failed:
            raise EvidenceError("writer_failed", "A write failed; reopen the evidence before continuing")

    @property
    def model(self) -> EvidenceModel:
        with self._mutex:
            self._require_open()
            return self._model.snapshot()

    def record(self, envelope) -> bool:
        with self._mutex:
            self._require_writable()
            envelope = codec.to_data(envelope)
            if not self._model.validate(envelope):
                return False
            _verify_artifacts(self.directory, envelope)
            content = (codec.dumps(envelope) + "\n").encode("utf-8")
            if len(content) - 1 > MAX_RECORD_BYTES:
                raise EvidenceError("size_limit", "Evidence record exceeds the size limit")
            try:
                _write_all(self._log, content)
            except BaseException:
                self._failed = True
                raise
            self._model.apply(envelope)
            return True

    def put_artifact(self, data: bytes, media_type: str, width=None, height=None) -> dict:
        with self._mutex:
            self._require_writable()
            if not isinstance(data, bytes):
                raise EvidenceError("invalid_record", "Artifact data must be bytes")
            if not isinstance(media_type, str):
                raise EvidenceError("invalid_record", "Artifact media type must be text")
            digest = hashlib.sha256(data).hexdigest()
            extension = {"image/png": ".png", "image/jpeg": ".jpeg"}.get(media_type, ".bin")
            reference = validate_artifact({
                "id": digest,
                "path": "artifacts/" + digest + extension,
                "media_type": media_type,
                "sha256": digest,
                "width": width,
                "height": height,
            })
            path = _safe_path(self.directory, reference["path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                temporary = path.parent / (".artifact-" + uuid4().hex)
                try:
                    with temporary.open("xb") as output:
                        _write_all(output, data)
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)
            _artifact(self.directory, reference)
            return reference

    def read_artifact(self, reference) -> bytes:
        with self._mutex:
            self._require_open()
            return _artifact(self.directory, reference)

    @staticmethod
    def recover_tail(directory: str | Path) -> int:
        root = Path(directory).resolve(strict=True)
        lock = _WriterLock(root)
        try:
            model = EvidenceModel(EvidenceReader._manifest(root))
            completed = EvidenceReader._replay(root, model, allow_incomplete=True)
            path = _regular(root, "evidence.jsonl")
            removed = path.stat().st_size - completed
            if removed:
                with path.open("r+b") as output:
                    output.truncate(completed)
                    output.flush()
                    os.fsync(output.fileno())
            return removed
        finally:
            lock.close()

    def close(self) -> None:
        with self._mutex:
            if not self._closed:
                self._closed = True
                try:
                    self._log.close()
                finally:
                    self._lock.close()

    def __enter__(self) -> JsonlStore:
        self._require_open()
        return self

    def __exit__(self, exception_type, exception, traceback) -> None:
        self.close()
