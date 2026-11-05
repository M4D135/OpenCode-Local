#!/usr/bin/env python3
"""Regression tests for signing input integrity and fail-closed imports.

The artificial WIN_CERTIFICATE records below are format fixtures, not real
signatures. No private key, self-signed certificate or OS trust change is used.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import build
import build_packs
import signing
from pack_profiles import load_pack


def pe_fixture() -> bytes:
    data = bytearray(1027)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", data, 0x80 + 20, 240)
    struct.pack_into("<H", data, 0x98, 0x20b)
    struct.pack_into("<I", data, 0x98 + 108, 16)
    data[768:784] = b"compiled-content"
    return bytes(data)


def append_format_fixture(data: bytes) -> bytes:
    output = bytearray(data)
    output.extend(b"\0" * (-len(output) % 8))
    offset = len(output)
    fake_record = struct.pack("<IHH", 24, 0x200, 2) + b"not-a-signature!"
    output.extend(fake_record)
    struct.pack_into("<I", output, 0x98 + 64, 0x12345678)
    struct.pack_into("<II", output, 0x98 + 112 + 4 * 8, offset, len(fake_record))
    return bytes(output)


class SigningIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original = self.root / "unsigned.exe"
        self.original.write_bytes(pe_fixture())
        self.record = signing.image_record(self.original)
        self.candidate = self.root / "candidate.exe"
        self.candidate.write_bytes(append_format_fixture(self.original.read_bytes()))

    def tearDown(self):
        self.temporary.cleanup()

    def test_original_checksum_and_only_certificate_changes_match_image(self):
        signing.match_image(self.candidate, self.record, signed=True)

    def test_changed_code_rejected_even_with_certificate_present(self):
        data = bytearray(self.candidate.read_bytes())
        data[770] ^= 1
        self.candidate.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "compiled image"):
            signing.match_image(self.candidate, self.record, signed=True)

    def test_unexplained_overlay_before_certificate_rejected(self):
        self.candidate.write_bytes(append_format_fixture(self.original.read_bytes() + b"EVIL" * 4))
        with self.assertRaisesRegex(ValueError, "non-certificate length"):
            signing.match_image(self.candidate, self.record, signed=True)

    def test_trailing_bytes_after_certificate_rejected(self):
        self.candidate.write_bytes(self.candidate.read_bytes() + b"extra")
        with self.assertRaisesRegex(ValueError, "end of the PE"):
            signing.match_image(self.candidate, self.record, signed=True)

    def test_unsigned_in_signed_mode_rejected(self):
        with self.assertRaisesRegex(ValueError, "Missing Authenticode"):
            signing.match_image(self.original, self.record, signed=True)

    def test_signed_in_unsigned_mode_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsigned mode"):
            signing.match_image(self.candidate, self.record, signed=False)

    def test_unsigned_checksum_modification_rejected(self):
        data = bytearray(self.original.read_bytes())
        data[0x98 + 64] = 7
        self.original.write_bytes(data)
        with self.assertRaisesRegex(ValueError, "differs"):
            signing.match_image(self.original, self.record, signed=False)

    def test_trust_failure_is_not_masked_by_valid_image_structure(self):
        incoming = self.root / "incoming"
        incoming.mkdir()
        (incoming / "OpenCodeLocal.exe").write_bytes(self.candidate.read_bytes())
        record = {"path": "OpenCodeLocal.exe", **self.record}
        with mock.patch.object(signing, "verify_trust", side_effect=RuntimeError("untrusted")) as verifier:
            with self.assertRaisesRegex(RuntimeError, "untrusted"):
                signing.validate_files(incoming, [record], signed=True, expected_signer="a" * 64)
            verifier.assert_called_once()

    def test_missing_certificate_pin_fails_before_verifier(self):
        with mock.patch.object(signing, "verify_trust") as verifier:
            with self.assertRaisesRegex(ValueError, "expected-signer-sha256"):
                signing.validate_files(self.root, [], signed=True, expected_signer=None)
            verifier.assert_not_called()

    def test_extra_vendor_executable_is_not_allowed_in_signing_input(self):
        incoming = self.root / "incoming"
        incoming.mkdir()
        (incoming / "OpenCodeLocal.exe").write_bytes(self.original.read_bytes())
        (incoming / "llama-server.exe").write_bytes(self.original.read_bytes())
        with self.assertRaisesRegex(ValueError, "extra=.*llama-server"):
            signing.validate_files(incoming, [{"path": "OpenCodeLocal.exe", **self.record}], signed=False, expected_signer=None)

    def test_path_traversal_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid artifact path"):
            signing.artifact_file(self.root, "../outside.exe")

    def test_symbolic_link_artifact_rejected(self):
        link = self.root / "link.exe"
        try:
            link.symlink_to(self.original)
        except (OSError, NotImplementedError):
            self.skipTest("Symlink creation not available")
        with self.assertRaisesRegex(ValueError, "symlinks"):
            signing.artifact_file(self.root, "link.exe")

    def test_source_change_between_phases_rejected(self):
        with mock.patch.object(signing, "source_identity", return_value={"commit": "new", "inputs_sha256": "new"}):
            with self.assertRaisesRegex(ValueError, "Sources changed"):
                signing.check_source({"commit": "old", "inputs_sha256": "old"}, signed=False)

    def test_signed_release_from_dirty_checkout_rejected(self):
        identity = {"commit": "same", "inputs_sha256": "same", "dirty": True}
        with mock.patch.object(signing, "source_identity", return_value=identity):
            with self.assertRaisesRegex(ValueError, "clean source checkout"):
                signing.check_source(identity, signed=True)

    def test_base_preparation_does_not_fetch_model(self):
        # Mock the actual download/extraction boundary, not the selection rule.
        # This covers the old bug where --setup-only still fetched the legacy GGUF.
        root = self.root / "project"
        root.mkdir()
        (root / "config").mkdir()
        (root / "config/artifacts.lock.json").write_text('[{"file":"engine.zip"},{"file":"small.gguf"},{"file":"large.gguf"},{"file":"linux.tar.gz"}]')
        payload = root / "build/payload"
        def extract(archive, destination, predicate=None):
            destination.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(build, "ROOT", root), mock.patch.object(build, "PAYLOAD", payload), \
             mock.patch("fetch_payload.fetch") as fetch, mock.patch.object(build, "extract_flat", side_effect=extract), \
             mock.patch.object(build, "stage_runtime"), mock.patch.object(build, "run"):
            build.stage(False)
        self.assertEqual([call.args[0]["file"] for call in fetch.call_args_list], ["engine.zip"])
        self.assertFalse(any((payload / "wad/model").iterdir()))

    def test_reused_base_copies_only_manifest_declared_files(self):
        base = self.root / "base"
        for relative in ("wad/config", "wad/docs", "wad/support", "wad/model", "wad/opencode"):
            (base / relative).mkdir(parents=True, exist_ok=True)
        (base / "wad/config/opencode.json").write_bytes((build_packs.ROOT / "config/opencode.json.template").read_bytes())
        (base / "wad/opencode/opencode.exe").write_bytes(b"verified upstream fixture")
        entries = [{"path": path.relative_to(base).as_posix(), "size": path.stat().st_size,
                    "sha256": signing.digest(path)} for path in base.rglob("*") if path.is_file()]
        (base / "wad/support/payload-manifest.json").write_text(json.dumps(entries), encoding="utf-8")
        undeclared = (base / "wad/opencode/unlisted.exe", base / "wad/opencode/.opencode.exe.interrupted")
        for path in undeclared:
            path.write_bytes(b"not declared by the verified input manifest")
        build_packs.verify_base(base)
        payload = build_packs.prepare_payload(base, self.root / "work", load_pack("moe"))
        self.assertEqual((payload / "wad/opencode/opencode.exe").read_bytes(), b"verified upstream fixture")
        for path in undeclared:
            self.assertTrue(path.is_file(), "The original base must remain untouched")
            self.assertFalse((payload / path.relative_to(base)).exists())
        new_manifest = build_packs.write_manifest(payload, load_pack("moe"))
        self.assertFalse(any(entry["path"].endswith(("unlisted.exe", ".interrupted")) for entry in new_manifest))


if __name__ == "__main__":
    unittest.main(verbosity=2)
