"""Preparation and cache tests use synthetic responses without network access."""

import hashlib
import io
import json
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import warnings
from zipfile import ZipFile, ZipInfo

from tools import prepare_conformance as preparation


def release(identifier=1, published="2026-01-01T00:00:00Z", *, prerelease=False, draft=False):
    return {
        "id": identifier,
        "tag_name": f"v0.2.{identifier}",
        "published_at": published,
        "prerelease": prerelease,
        "draft": draft,
        "html_url": f"https://github.com/ferpasri/testar-evidence-specs/releases/tag/v0.2.{identifier}",
        "zipball_url": f"https://api.github.com/repos/ferpasri/testar-evidence-specs/zipball/v0.2.{identifier}",
    }


def archive(extra=(), *, omit=()):
    entries = [
        ("repository/README.md", b"Repository documentation"),
        ("repository/conformance/README.md", b"Shared cases"),
        ("repository/conformance/cases/example/manifest.json", b'{"schema_version":3,"evidence_id":"example"}\n'),
        ("repository/conformance/cases/example/evidence.jsonl", b"{}\n"),
        ("repository/conformance/cases/example/expected.json", b'{"status":"accepted"}\n'),
    ]
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(output, "w") as zipped:
            for name, content in entries + list(extra):
                if isinstance(name, str) and name in omit:
                    continue
                zipped.writestr(name, content)
    return output.getvalue()


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="testar-conformance-tool-")
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)

    def prepare(self, content=None, releases=None):
        content = archive() if content is None else content
        releases = [release()] if releases is None else releases
        with patch.object(preparation, "_download", side_effect=[json.dumps(releases).encode(), content]):
            return preparation.prepare_latest(self.repository)

    def test_downloads_latest_publication_including_prereleases_and_records_checksums(self):
        releases = [
            release(8, "2026-01-01T00:00:00Z"),
            release(2, "2026-02-01T00:00:00Z", prerelease=True),
            release(99, "2026-03-01T00:00:00Z", draft=True),
            {"draft": False, "published_at": None},
        ]
        content = archive()
        with patch.object(preparation, "_download", side_effect=[json.dumps(releases).encode(), content]) as download:
            root = preparation.prepare_latest(self.repository)
        self.assertEqual(releases[1]["zipball_url"], download.call_args.args[0])
        metadata = json.loads((root.parent / "current.json").read_text(encoding="utf-8"))
        self.assertEqual("v0.2.2", metadata["tag"])
        self.assertEqual(releases[1]["html_url"], metadata["release_url"])
        self.assertEqual(hashlib.sha256(content).hexdigest(), metadata["archive_sha256"])
        self.assertEqual(root.name, metadata["root"])
        self.assertEqual(4, len(metadata["files"]))
        self.assertFalse((root / "README.md").exists(), "Only conformance data is extracted")
        for name, digest in metadata["files"].items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)
        with patch.object(preparation, "_download", side_effect=AssertionError("Offline cache loading must not download")):
            self.assertEqual(root, preparation.load_prepared(self.repository))

    def test_release_selection_paginates_and_breaks_publication_ties_by_identifier(self):
        first_page = [release(index) for index in range(100)]
        second_page = [release(101, "2026-02-01T00:00:00Z"), release(102, "2026-02-01T00:00:00Z")]
        with patch.object(preparation, "_download", side_effect=[
            json.dumps(first_page).encode(), json.dumps(second_page).encode(), archive()
        ]) as download:
            root = preparation.prepare_latest(self.repository)
        self.assertIn("page=2", download.call_args_list[1].args[0])
        metadata = json.loads((root.parent / "current.json").read_text(encoding="utf-8"))
        self.assertEqual("102", metadata["release_id"])

    def test_failing_preparation_keeps_previous_selection_and_removes_partial_directory(self):
        previous = self.prepare()
        current = previous.parent / "current.json"
        before = current.read_bytes()
        with self.assertRaises(preparation.PreparationError):
            self.prepare(archive(omit=("repository/conformance/cases/example/expected.json",)))
        self.assertEqual(before, current.read_bytes())
        self.assertEqual(previous, preparation.load_prepared(self.repository))
        self.assertEqual({previous.name, "current.json"}, {path.name for path in previous.parent.iterdir()})

    def test_unsafe_archive_paths_are_rejected_even_outside_conformance(self):
        for name in (
            "repository/conformance/../escape.txt",
            "repository/../../escape.txt",
            "/repository/conformance/escape.txt",
            "repository/conformance/C:/escape.txt",
            "repository/conformance/back\\slash.txt",
            "repository/conformance//empty.txt",
            "repository/conformance/CON.txt",
            "repository/conformance/trailing. /file.txt",
        ):
            with self.subTest(path=name):
                content = archive(((name.replace("\\", "!"), b"unsafe"),))
                content = content.replace(b"back!slash", b"back\\slash")
                with self.assertRaises(preparation.PreparationError):
                    self.prepare(content)
        self.assertFalse((self.repository / "escape.txt").exists())

    def test_symbolic_links_and_special_files_in_archive_are_rejected(self):
        for file_type in (stat.S_IFLNK, stat.S_IFIFO):
            entry = ZipInfo("repository/conformance/link")
            entry.create_system = 3
            entry.external_attr = (file_type | 0o777) << 16
            with self.subTest(file_type=file_type):
                with self.assertRaises(preparation.PreparationError):
                    self.prepare(archive(((entry, b"../../outside"),)))

    def test_duplicate_and_case_colliding_archive_entries_are_rejected(self):
        for name in (
            "repository/conformance/README.md",
            "repository/conformance/readme.MD",
        ):
            with self.subTest(path=name):
                with self.assertRaises(preparation.PreparationError):
                    self.prepare(archive(((name, b"duplicate"),)))

    def test_archive_size_entry_size_expanded_size_and_entry_count_are_bounded(self):
        for option, limit in (
            ("MAX_ARCHIVE_BYTES", 10),
            ("MAX_ENTRY_BYTES", 2),
            ("MAX_EXPANDED_BYTES", 30),
            ("MAX_ARCHIVE_ENTRIES", 2),
        ):
            with self.subTest(limit=option):
                with patch.object(preparation, option, limit):
                    with self.assertRaises(preparation.PreparationError):
                        self.prepare()

    def test_multiple_repository_roots_and_invalid_zip_are_rejected(self):
        for content in (archive((("another/conformance/extra.txt", b"extra"),)), b"invalid ZIP"):
            with self.subTest(content=content[:30]):
                with self.assertRaises(preparation.PreparationError):
                    self.prepare(content)

    def test_missing_cache_has_an_explicit_preparation_instruction(self):
        with self.assertRaisesRegex(preparation.PreparationError, "python -m tools.prepare_conformance"):
            preparation.load_prepared(self.repository)

    def test_modified_cached_file_is_rejected(self):
        root = self.prepare()
        (root / "conformance" / "README.md").write_bytes(b"modified")
        with self.assertRaisesRegex(preparation.PreparationError, "checksum mismatch"):
            preparation.load_prepared(self.repository)

    def test_extra_or_removed_cached_files_are_rejected(self):
        for operation in ("extra", "removed"):
            with self.subTest(operation=operation):
                root = self.prepare()
                if operation == "extra":
                    (root / "conformance" / "unexpected.txt").write_bytes(b"unexpected")
                else:
                    (root / "conformance" / "README.md").unlink()
                with self.assertRaisesRegex(preparation.PreparationError, "contents differ"):
                    preparation.load_prepared(self.repository)

    def test_cache_metadata_cannot_point_outside_its_cache(self):
        root = self.prepare()
        current = root.parent / "current.json"
        metadata = json.loads(current.read_text(encoding="utf-8"))
        for name in ("../elsewhere", "/absolute", "C:/absolute", "one/two", ".."):
            with self.subTest(path=name):
                metadata["root"] = name
                current.write_text(json.dumps(metadata), encoding="utf-8")
                with self.assertRaises(preparation.PreparationError):
                    preparation.load_prepared(self.repository)

    def test_malformed_metadata_and_missing_checksums_are_rejected(self):
        root = self.prepare()
        current = root.parent / "current.json"
        metadata = json.loads(current.read_text(encoding="utf-8"))
        for value in ("not JSON", "[]", json.dumps({**metadata, "files": {}})):
            with self.subTest(metadata=value[:40]):
                current.write_text(value, encoding="utf-8")
                with self.assertRaises(preparation.PreparationError):
                    preparation.load_prepared(self.repository)

    def test_no_published_release_or_malformed_listing_is_rejected(self):
        for response in ([], [release(draft=True)], {}, [{**release(), "published_at": "yesterday"}]):
            with self.subTest(response=response):
                with patch.object(preparation, "_download", return_value=json.dumps(response).encode()):
                    with self.assertRaises(preparation.PreparationError):
                        preparation._latest_release()

    def test_release_pagination_is_bounded(self):
        response = json.dumps([release(index) for index in range(100)]).encode()
        with patch.object(preparation, "MAX_RELEASE_PAGES", 2):
            with patch.object(preparation, "_download", return_value=response) as download:
                with self.assertRaisesRegex(preparation.PreparationError, "pagination limit"):
                    preparation._latest_release()
        self.assertEqual(2, download.call_count)

    def test_downloads_and_redirects_require_https(self):
        for url in ("http://example.org/release.zip", "file:///release.zip", "https://user:pass@example.org/release.zip"):
            with self.subTest(url=url):
                with self.assertRaises(preparation.PreparationError):
                    preparation._download(url, 100, "application/zip")
                with self.assertRaises(preparation.PreparationError):
                    preparation._HttpsRedirects().redirect_request(None, None, 302, "Found", {}, url)

    def test_bounded_reader_accepts_limit_and_rejects_one_extra_byte(self):
        self.assertEqual(b"1234", preparation._bounded(io.BytesIO(b"1234"), 4))
        with self.assertRaises(preparation.PreparationError):
            preparation._bounded(io.BytesIO(b"12345"), 4)
