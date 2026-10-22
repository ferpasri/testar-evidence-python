"""Download published shared cases; tests subsequently use the verified cache offline."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
from datetime import datetime, timezone
from typing import BinaryIO
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4
from zipfile import BadZipFile, ZipFile


RELEASES_URL = "https://api.github.com/repos/ferpasri/testar-evidence-specs/releases"
MEBIBYTE = 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * MEBIBYTE
MAX_ENTRY_BYTES = 32 * MEBIBYTE
MAX_EXPANDED_BYTES = 128 * MEBIBYTE
MAX_ARCHIVE_ENTRIES = 10000
MAX_RELEASE_PAGES = 20
PREPARATION_COMMAND = "python -m tools.prepare_conformance"


class PreparationError(OSError):
    """A release or prepared cache cannot be used safely."""


class _HttpsRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        _https_url(new_url)
        return super().redirect_request(request, response, code, message, headers, new_url)


def _https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise PreparationError("Release downloads require an HTTPS URL without credentials")


def _bounded(input_stream: BinaryIO, limit: int) -> bytes:
    chunks = []
    total = 0
    while chunk := input_stream.read(min(65536, limit - total + 1)):
        total += len(chunk)
        if total > limit:
            raise PreparationError("Download or archive entry exceeds the size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _download(url: str, limit: int, accept: str) -> bytes:
    _https_url(url)
    request = Request(url, headers={
        "User-Agent": "testar-evidence-python-conformance",
        "Accept": accept,
    })
    try:
        with build_opener(_HttpsRedirects()).open(request, timeout=60) as response:
            _https_url(response.geturl())
            if response.status != 200:
                raise PreparationError(f"Download failed: HTTP {response.status} from {url}")
            return _bounded(response, limit)
    except URLError as error:
        raise PreparationError(f"Could not download shared conformance data: {error}") from error


def _publication_key(release: dict) -> tuple[datetime, int]:
    try:
        published = datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
        if published.tzinfo is None:
            raise ValueError("missing timezone")
        release_id = release["id"]
        if isinstance(release_id, bool) or not isinstance(release_id, int) or release_id < 0:
            raise ValueError("invalid release identifier")
        return published.astimezone(timezone.utc), release_id
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise PreparationError("Published release has an invalid date or identifier") from error


def _latest_release() -> dict:
    latest = None
    for page in range(1, MAX_RELEASE_PAGES + 1):
        response = _download(f"{RELEASES_URL}?per_page=100&page={page}",
            4 * MEBIBYTE, "application/vnd.github+json")
        try:
            releases = json.loads(response)
        except (UnicodeError, ValueError) as error:
            raise PreparationError("GitHub releases response is invalid JSON") from error
        if not isinstance(releases, list) or any(not isinstance(item, dict) for item in releases):
            raise PreparationError("GitHub releases response is not an array of releases")
        for release in releases:
            if release.get("draft") or release.get("published_at") is None:
                continue
            if latest is None or _publication_key(release) > _publication_key(latest):
                _publication_key(release)
                latest = release
        if len(releases) < 100:
            if latest is None:
                raise PreparationError("No published specs release was found")
            for field in ("tag_name", "zipball_url", "html_url"):
                if not isinstance(latest.get(field), str) or not latest[field]:
                    raise PreparationError(f"Published release is missing {field}")
            _https_url(latest["zipball_url"])
            _https_url(latest["html_url"])
            return latest
    raise PreparationError("Release listing exceeds the pagination limit")


def _safe_parts(name: str, *, directory: bool = False) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise PreparationError("Unsafe release or cache path")
    candidate = name[:-1] if directory and name.endswith("/") else name
    parts = candidate.split("/")
    reserved = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE)
    if any(not part or part in (".", "..") or part.endswith((".", " "))
        or reserved.match(part) or any(ord(character) < 32 or character in '<>"|?*' for character in part)
        for part in parts):
        raise PreparationError("Unsafe release or cache path")
    return tuple(parts)


def _case_directories(root: Path) -> list[Path]:
    cases = root / "conformance" / "cases"
    if not cases.is_dir():
        raise PreparationError("Release has no conformance/cases directory")
    directories = sorted(path for path in cases.iterdir() if path.is_dir())
    if not directories:
        raise PreparationError("Release has no conformance cases")
    for directory in directories:
        if any(not (directory / name).is_file() for name in (
            "manifest.json", "evidence.jsonl", "expected.json"
        )):
            raise PreparationError(f"Incomplete released conformance case: {directory.name}")
    return directories


def _unpack(archive: bytes, root: Path) -> None:
    if len(archive) > MAX_ARCHIVE_BYTES:
        raise PreparationError("Release archive exceeds the download size limit")
    expanded = 0
    prefix = None
    names = set()
    try:
        with ZipFile(io.BytesIO(archive)) as entries:
            if len(entries.infolist()) > MAX_ARCHIVE_ENTRIES:
                raise PreparationError("Release archive contains too many entries")
            for entry in entries.infolist():
                parts = _safe_parts(entry.orig_filename, directory=entry.is_dir())
                if prefix is None:
                    prefix = parts[0]
                if parts[0] != prefix:
                    raise PreparationError("Release archive has multiple root directories")
                key = "/".join(parts).casefold()
                if key in names:
                    raise PreparationError("Release archive has duplicate paths")
                names.add(key)
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise PreparationError("Release archive contains a symbolic link or special file")
                if entry.is_dir():
                    continue
                if len(parts) < 2:
                    raise PreparationError("Release file is outside its repository directory")
                if entry.flag_bits & 1:
                    raise PreparationError("Encrypted release archive entries are unsupported")
                if entry.file_size > MAX_ENTRY_BYTES:
                    raise PreparationError("Archive entry exceeds the size limit")
                if expanded + entry.file_size > MAX_EXPANDED_BYTES:
                    raise PreparationError("Expanded release archive exceeds the size limit")
                with entries.open(entry) as input_stream:
                    content = _bounded(input_stream, min(MAX_ENTRY_BYTES, MAX_EXPANDED_BYTES - expanded))
                expanded += len(content)
                if expanded > MAX_EXPANDED_BYTES:
                    raise PreparationError("Expanded release archive exceeds the size limit")
                if parts[1] != "conformance":
                    continue
                destination = root.joinpath(*parts[1:])
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    output.write(content)
    except (BadZipFile, RuntimeError, NotImplementedError) as error:
        raise PreparationError(f"Invalid release ZIP archive: {error}") from error
    _case_directories(root)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _repository(repository: Path | None) -> Path:
    return Path(repository).resolve() if repository is not None else Path(__file__).resolve().parents[1]


def prepare_latest(repository: Path | None = None) -> Path:
    """Prepare the newest published release, including prereleases, without changing an older selection on failure."""
    release = _latest_release()
    archive = _download(release["zipball_url"], MAX_ARCHIVE_BYTES, "application/zip")
    cache = _repository(repository) / "target" / "conformance"
    cache.mkdir(parents=True, exist_ok=True)
    name = f"release-{release['id']}-{uuid4().hex}"
    root = cache / name
    root.mkdir()
    temporary = cache / f"current-{uuid4().hex}.json"
    try:
        _unpack(archive, root)
        hashes = {
            path.relative_to(root).as_posix(): _sha256(path.read_bytes())
            for path in sorted(root.rglob("*")) if path.is_file()
        }
        metadata = {
            "root": name,
            "tag": release["tag_name"],
            "release_id": str(release["id"]),
            "release_url": release["html_url"],
            "published_at": release["published_at"],
            "archive_sha256": _sha256(archive),
            "files": hashes,
        }
        temporary.write_text(json.dumps(metadata, indent=4) + "\n", encoding="utf-8", newline="\n")
        os.replace(temporary, cache / "current.json")
    except BaseException:
        if root.resolve().parent == cache.resolve() and root.name == name:
            shutil.rmtree(root)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return root


def load_prepared(repository: Path | None = None) -> Path:
    """Verify every prepared file offline and return the root containing conformance/cases."""
    cache = _repository(repository) / "target" / "conformance"
    current = cache / "current.json"
    if not current.is_file():
        raise PreparationError(f"Prepare shared cases first: {PREPARATION_COMMAND}")
    try:
        metadata = json.loads(current.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise PreparationError("Conformance cache metadata must be an object")
        root_parts = _safe_parts(metadata.get("root"))
        if len(root_parts) != 1:
            raise PreparationError("Conformance cache root must be one directory")
        root = cache.joinpath(*root_parts)
        if root.is_symlink() or not root.is_dir() or root.resolve().parent != cache.resolve():
            raise PreparationError("Conformance cache path leaves its directory or is unavailable")
        files = metadata.get("files")
        if not isinstance(files, dict) or not files:
            raise PreparationError("Conformance cache has no file checksums")
        actual = set()
        for path in root.rglob("*"):
            if path.is_symlink():
                raise PreparationError("Conformance cache contains a symbolic link")
            if path.is_file():
                actual.add(path.relative_to(root).as_posix())
            elif not path.is_dir():
                raise PreparationError("Conformance cache contains a special file")
        if actual != set(files):
            raise PreparationError("Conformance cache contents differ from the prepared release")
        for name, digest in files.items():
            parts = _safe_parts(name)
            if parts[0] != "conformance" or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise PreparationError("Invalid conformance cache checksum metadata")
            path = root.joinpath(*parts)
            if not path.resolve().is_relative_to(root.resolve()) or _sha256(path.read_bytes()) != digest:
                raise PreparationError(f"Conformance checksum mismatch: {name}")
        _case_directories(root)
        return root
    except (ValueError, UnicodeError, TypeError) as error:
        raise PreparationError(f"Invalid conformance cache metadata: {error}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=None,
        help="repository directory; defaults to the directory containing tools")
    arguments = parser.parse_args()
    try:
        root = prepare_latest(arguments.repository)
        metadata = json.loads((root.parent / "current.json").read_text(encoding="utf-8"))
        print(f"Prepared {metadata['tag']}: {len(_case_directories(root))} shared conformance cases")
        print(f"Cache: {root}")
    except OSError as error:
        parser.exit(1, f"Could not prepare conformance data: {error}\n")


if __name__ == "__main__":
    main()
