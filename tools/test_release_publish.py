#!/usr/bin/env python3
"""Exercise release gates with small local fixtures and a fake GitHub API."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

import publish_single_release as publication
from pack_profiles import load_pack
from publish_single_release import GitHub, check_tag, clean_old, plan


class FakeGitHub:
    def __init__(self, entries):
        self.entries = dict(entries)
        self.deletions = []
        self.calls = []

    def call(self, path, method="GET", body=None, missing_ok=False):
        self.calls.append((method, path))
        if method == "DELETE":
            self.deletions.append(path)
            self.entries.pop(path)
            return None
        if path not in self.entries and not missing_ok:
            raise ValueError("Missing mock object")
        return self.entries.get(path)


class ReleaseGuards(unittest.TestCase):
    def setUp(self):
        self.request = {"version": "1.2.0", "remove_releases_after_verified_publication": [
            {"id": 1, "tag": "v1.0.0", "expected_asset_bytes": 123}],
            "remove_obsolete_actions_artifacts": []}
        self.current = {"id": 2, "tag_name": "v1.2.0", "draft": False}
        self.old = {"id": 1, "tag_name": "v1.0.0", "draft": False,
                    "immutable": False, "assets": [{"size": 123}]}

    def test_actual_plan_is_only_old_versions(self):
        self.assertEqual(plan()["pack"], "moe")

    def test_no_cleanup_before_publication(self):
        api = FakeGitHub({"/releases/1": self.old})
        with self.assertRaises(ValueError):
            clean_old(api, self.request, {**self.current, "draft": True})
        self.assertEqual(api.deletions, [])

    def test_never_delete_replacement(self):
        api = FakeGitHub({"/releases/1": self.old})
        with self.assertRaises(ValueError):
            clean_old(api, self.request, {**self.current, "id": 1})
        self.assertEqual(api.deletions, [])

    def test_changed_release_is_preserved(self):
        for change in ({"tag_name": "v2.0.0"}, {"immutable": True},
                       {"assets": [{"size": 124}]}, {"draft": True}):
            api = FakeGitHub({"/releases/1": {**self.old, **change}})
            with self.assertRaises(ValueError):
                clean_old(api, self.request, self.current)
            self.assertEqual(api.deletions, [])

    def test_cleanup_is_idempotent_and_never_changes_refs(self):
        api = FakeGitHub({"/releases/1": self.old})
        result = clean_old(api, self.request, self.current)
        self.assertEqual(result["release_bytes_removed"], 123)
        self.assertFalse(result["git_history_and_tags_deleted"])
        self.assertEqual(api.deletions, ["/releases/1"])
        again = clean_old(api, self.request, self.current)
        self.assertEqual(again["release_bytes_removed"], 0)
        self.assertEqual(again["releases"][0]["status"], "already_absent")

    def test_existing_conflicting_tag_fails(self):
        api = FakeGitHub({"/git/ref/tags/v1.2.0": {"object": {"type": "commit", "sha": "a" * 40}}})
        with self.assertRaises(ValueError):
            check_tag(api, "1.2.0", "b" * 40)
        self.assertEqual(api.deletions, [])

    def test_remote_digest_and_upload_state_are_required(self):
        item = {"state": "uploaded", "size": 123, "digest": "sha256:" + "a" * 64}
        GitHub.check_asset(item, 123, "a" * 64)
        for change in ({"state": "starter"}, {"size": 124}, {"digest": "sha256:" + "b" * 64}):
            with self.assertRaises(ValueError):
                GitHub.check_asset({**item, **change}, 123, "a" * 64)


class LauncherUpdateGuards(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="opencode-release-gates-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        self.source = "b" * 40
        self.pack = load_pack("moe")
        self.launcher_source = (publication.ROOT / "src/launcher.cpp").read_bytes()
        self.launcher_digest = hashlib.sha256(self.launcher_source).hexdigest()
        self.request = {"version": self.pack["app_version"], "kind": "launcher-update",
                        "model_source_release": "v1.2.0", "remove_releases_after_verified_publication": [],
                        "remove_obsolete_actions_artifacts": []}
        setup = b"MZ-local-test-fixture-not-an-executable"
        setup_hash = hashlib.sha256(setup).hexdigest()
        archive = self.dist / "Setup.zip"
        with zipfile.ZipFile(archive, "w") as zipped:
            zipped.writestr("Setup.exe", setup)
        self.final = {"pack": "moe", "version": self.pack["app_version"], "source_commit": self.source,
                      "signing": "unsigned", "zip_contains": ["Setup.exe"], "model": self.pack["model"],
                      "zip_bytes": archive.stat().st_size, "zip_sha256": publication.sha256(archive),
                      "setup": {"sha256": setup_hash}, "native_validation": {},
                      "model_downloads": False, "parts": []}
        checks, embedded = [], []
        for name in publication.HOUSE_FILES:
            data = name.encode()
            digest = hashlib.sha256(data).hexdigest()
            self.final["native_validation"]["moe/" + name] = {"sha256": digest, "bytes": len(data)}
            inspection = {"passed": True, "sha256": digest,
                          "metadata": {"file_bytes": len(data), "product_version": self.pack["app_version"],
                                       "original_filename": name}, "authenticode": {"status": "NotSigned"}}
            checks.append({"name": "moe/native/" + name, "passed": True, "details": inspection})
            embedded.append({"passed": True, "exact_native_match": True, "extracted_sha256": digest,
                             "native_sha256": digest, "inspection": copy.deepcopy(inspection)})
        for name in ("moe/launcher-diagnostic", "assembler/real-fixtures-and-production-metadata",
                     "installer/production-path-and-size-functions"):
            checks.append({"name": name, "passed": True, "details": {"passed": True}})
        checks += [
            {"name": "launcher/runtime-regressions", "passed": True,
             "details": {"passed": True, "execution": {"exit_code": 0, "timed_out": False},
                         "results": {"passed": True, "build_only": False, "runtime_executed": True,
                                     "app_version": self.pack["app_version"],
                                     "launcher_source_sha256": self.launcher_digest,
                                     "checks": [{"name": name, "runtime_executed": True, "passed": True,
                                                 "results": {"passed": True,
                                                             "checks": [{"name": "fixture-check", "passed": True}]}}
                                                for name in ("launcher_process_tests", "launcher_profile_tests",
                                                             "launcher_http_ui_tests")]}}},
            {"name": "moe/setup/metadata-and-signature", "passed": True,
             "details": {"passed": True, "sha256": setup_hash,
                         "metadata": {"product_version": self.pack["app_version"]}}},
            {"name": "moe/setup/archive-and-exact-native-payload", "passed": True,
             "details": {"passed": True, "archive_sha256": setup_hash,
                         "embedded_home_built_executables": embedded}},
        ]
        self.windows = {"passed": True, "phase": "All", "packs": ["moe"],
                        "source_commit": self.source, "app_version": self.pack["app_version"],
                        "model_downloaded": False, "model_loaded": False, "checks": checks}
        self.report = self.root / "windows.json"
        self.save_reports()

    def save_reports(self):
        (self.dist / "SIGNATURES.json").write_text(json.dumps(self.final))
        self.report.write_text(json.dumps(self.windows))

    def check(self, name):
        return next(check for check in self.windows["checks"] if check["name"] == name)

    def model_source(self):
        tag = "v1.2.0"
        base = f"https://github.com/{publication.REPOSITORY}/releases/download/{tag}/"
        assets, records, parts = [], [], []
        for index, definition in enumerate(self.pack["parts"], 1):
            digest = hashlib.sha256(f"slice-{index}".encode()).hexdigest()
            parts.append({**definition, "sha256": digest})
            assets.append({"id": index, "name": definition["name"], "size": definition["bytes"],
                           "digest": "sha256:" + digest, "state": "uploaded",
                           "browser_download_url": base + definition["name"]})
            records.append({"id": index, "name": definition["name"], "bytes": definition["bytes"], "sha256": digest})
        model = json.dumps({**self.pack["model"], "parts": parts}).encode()
        model_digest = hashlib.sha256(model).hexdigest()
        assets.append({"id": 9, "name": "MODELE.json", "size": len(model), "digest": "sha256:" + model_digest,
                       "state": "uploaded", "browser_download_url": base + "MODELE.json"})
        records.append({"id": 9, "name": "MODELE.json", "bytes": len(model), "sha256": model_digest})
        proof = json.dumps({"version": "1.2.0", "assets_verified": True, "source_commit": "a" * 40,
                            "model_sha256": self.pack["model"]["sha256"], "assets": records}).encode()
        assets.append({"id": 10, "name": "MANIFEST-DISTRIBUTION.json", "size": len(proof),
                       "digest": "sha256:" + hashlib.sha256(proof).hexdigest(), "state": "uploaded",
                       "browser_download_url": base + "MANIFEST-DISTRIBUTION.json"})
        release = {"id": 120, "draft": False, "tag_name": tag, "assets": assets}
        api = FakeGitHub({"/releases/tags/" + tag: release,
                          "/git/ref/tags/" + tag: {"object": {"type": "commit", "sha": "a" * 40}}})
        contents = {base + "MODELE.json": model, base + "MANIFEST-DISTRIBUTION.json": proof}

        def open_metadata(url, **_):
            # A request for even one model slice fails this test immediately.
            self.assertIn(url, contents)
            return io.BytesIO(contents[url])

        return api, release, open_metadata

    def test_current_plan_is_program_only_and_preserves_model_source(self):
        value = plan()
        self.assertEqual(value["kind"], "launcher-update")
        self.assertEqual(value["model_source_release"], "v1.2.0")
        self.assertEqual(value["remove_releases_after_verified_publication"], [])
        self.assertEqual(value["remove_obsolete_actions_artifacts"], [])

    def test_wrong_publication_route_fails_before_api_or_model_access(self):
        api = FakeGitHub({})
        with mock.patch.object(publication, "plan", return_value=self.request):
            with self.assertRaises(ValueError):
                publication.publish(api, self.source, 1, self.dist, self.root / "absent-model-report", self.report)
        with mock.patch.object(publication, "plan", return_value={**self.request, "kind": "full-pack"}):
            with self.assertRaises(ValueError):
                publication.publish_launcher_update(api, self.source, 1, self.dist, self.report)
        self.assertEqual(api.calls, [])

    def test_preflight_exposes_the_explicit_routing_kind(self):
        api = FakeGitHub({"/actions/runs/7/artifacts?per_page=100": {"artifacts": [
            {"name": prefix + "7-1", "expired": False, "id": index, "created_at": "2026-11-05",
             "digest": "sha256:" + "a" * 64, "workflow_run": {"head_sha": self.source}}
            for index, prefix in enumerate(("unsigned-candidates-", "unsigned-windows-reports-"), 1)]}})
        output = self.root / "outputs"
        with mock.patch.object(publication, "plan", return_value=self.request), \
                mock.patch.object(publication, "verify_run"), mock.patch.object(publication, "check_tag"), \
                mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            publication.preflight(api, self.source, 7)
        self.assertIn("kind=launcher-update\n", output.read_text())
        self.assertIn("ready=true\n", output.read_text())

    def test_exact_new_windows_report_is_accepted_without_inference(self):
        result = publication.validate_launcher_update(self.source, self.dist, self.report)
        self.assertEqual(result["final"]["source_commit"], self.source)
        self.assertNotIn("runtime", result)

    def test_stale_incomplete_failed_or_mismatched_windows_reports_fail(self):
        mutations = [
            lambda: self.windows.update(source_commit="a" * 40),
            lambda: self.windows.update(app_version="1.2.0"),
            lambda: self.windows.update(model_loaded=True),
            lambda: self.windows["checks"].remove(self.check("launcher/runtime-regressions")),
            lambda: self.check("launcher/runtime-regressions")["details"]["results"].update(passed=False),
            lambda: self.check("launcher/runtime-regressions")["details"]["execution"].update(exit_code=1),
            lambda: self.check("moe/native/OpenCodeLocal.exe")["details"].update(sha256="c" * 64),
            lambda: self.check("moe/setup/archive-and-exact-native-payload")["details"]["embedded_home_built_executables"][0].update(extracted_sha256="c" * 64),
            lambda: self.windows["checks"].append(copy.deepcopy(self.windows["checks"][0])),
            lambda: self.final.update(parts=[{"name": "unexpected.bin"}]),
        ]
        original_windows, original_final = copy.deepcopy(self.windows), copy.deepcopy(self.final)
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                self.windows, self.final = copy.deepcopy(original_windows), copy.deepcopy(original_final)
                mutate()
                self.save_reports()
                with self.assertRaises(ValueError):
                    publication.validate_launcher_update(self.source, self.dist, self.report)

    def test_model_reference_reads_only_two_small_metadata_files(self):
        api, release, open_metadata = self.model_source()
        with mock.patch.object(publication.urllib.request, "urlopen", side_effect=open_metadata) as opening:
            result = publication.verify_model_reference(api, self.request, self.pack)
        self.assertEqual(opening.call_count, 2)
        self.assertEqual(len(result["parts"]), 8)
        self.assertFalse(result["weights_downloaded"])
        self.assertFalse(result["inference_executed"])
        self.assertEqual(result["release_id"], release["id"])
        self.assertEqual(api.deletions, [])

    def test_build_only_unexecuted_or_different_launcher_source_reports_fail(self):
        results = self.check("launcher/runtime-regressions")["details"]["results"]
        original = copy.deepcopy(results)
        for change in ({"build_only": True}, {"runtime_executed": False},
                       {"app_version": "1.2.0"}, {"launcher_source_sha256": "0" * 64}):
            with self.subTest(change=change):
                results.clear()
                results.update(copy.deepcopy(original))
                results.update(change)
                self.save_reports()
                with self.assertRaises(ValueError):
                    publication.validate_launcher_update(self.source, self.dist, self.report)

    def test_missing_unexecuted_or_empty_launcher_fixture_fails(self):
        results = self.check("launcher/runtime-regressions")["details"]["results"]
        original = copy.deepcopy(results["checks"])
        for change in ("missing", "unexecuted", "empty", "failed"):
            with self.subTest(change=change):
                results["checks"] = copy.deepcopy(original)
                if change == "missing":
                    results["checks"].pop()
                elif change == "unexecuted":
                    results["checks"][0]["runtime_executed"] = False
                elif change == "empty":
                    results["checks"][0]["results"]["checks"] = []
                else:
                    results["checks"][0]["results"]["checks"][0]["passed"] = False
                self.save_reports()
                with self.assertRaises(ValueError):
                    publication.validate_launcher_update(self.source, self.dist, self.report)

    def test_missing_changed_or_replaced_model_source_assets_fail(self):
        mutations = [
            lambda release: release.update(draft=True),
            lambda release: release["assets"].pop(0),
            lambda release: release["assets"][0].update(digest="sha256:" + "0" * 64),
            lambda release: release["assets"][0].update(size=123),
            lambda release: release["assets"][0].update(id=1000),
            lambda release: release["assets"][-1].update(digest="sha256:" + "0" * 64),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(case=index):
                api, release, open_metadata = self.model_source()
                mutate(release)
                with mock.patch.object(publication.urllib.request, "urlopen", side_effect=open_metadata):
                    with self.assertRaises(ValueError):
                        publication.verify_model_reference(api, self.request, self.pack)
                self.assertEqual(api.deletions, [])

    def test_launcher_cleanup_can_never_delete_model_source(self):
        api = FakeGitHub({})
        current = {"id": 121, "tag_name": "v" + self.request["version"], "draft": False}
        self.assertEqual(clean_old(api, self.request, current)["release_bytes_removed"], 0)
        for field, entries in (("remove_releases_after_verified_publication", [{"id": 120, "tag": "v1.2.0"}]),
                               ("remove_obsolete_actions_artifacts", [123])):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    clean_old(api, {**self.request, field: entries}, current)
        self.assertEqual(api.deletions, [])


    def test_program_publication_resumes_without_model_upload_or_deletion(self):
        source_api, old_release, open_metadata = self.model_source()
        original_model_release = copy.deepcopy(old_release)
        testcase, source_sha = self, self.source

        class PublicationAPI(FakeGitHub):
            def __init__(self, entries):
                super().__init__(entries)
                self.fail_next_publish = True

            def call(self, path, method="GET", body=None, missing_ok=False):
                if method == "POST":
                    testcase.assertEqual(path, "/releases")
                    self.calls.append((method, path))
                    release = {**body, "id": 121, "assets": [],
                               "html_url": "https://github.com/test/releases/tag/" + body["tag_name"]}
                    self.entries["/releases/121"] = release
                    self.entries["/releases/tags/" + body["tag_name"]] = release
                    return release
                if method == "PATCH":
                    testcase.assertEqual(path, "/releases/121")
                    self.calls.append((method, path))
                    if self.fail_next_publish:
                        self.fail_next_publish = False
                        raise RuntimeError("Simulated interruption before draft publication")
                    release = self.entries[path]
                    release.update(body)
                    for asset in release["assets"]:
                        asset["browser_download_url"] = (f"https://github.com/{publication.REPOSITORY}/releases/download/"
                                                         + release["tag_name"] + "/" + asset["name"])
                    self.entries["/git/ref/tags/" + release["tag_name"]] = {
                        "object": {"type": "commit", "sha": source_sha}}
                    return release
                testcase.assertEqual(method, "GET")
                return super().call(path, method, body, missing_ok)

            def upload(self, release, path, name, digest):
                testcase.assertFalse(name.endswith((".bin", ".gguf")))
                testcase.assertNotEqual(name, "VALIDATION-MODELE.json")
                testcase.assertEqual(publication.sha256(path), digest)
                existing = [asset for asset in release["assets"] if asset["name"] == name]
                if existing:
                    GitHub.check_asset(existing[0], path.stat().st_size, digest)
                    return existing[0]
                asset = {"id": 1000 + len(release["assets"]), "name": name, "size": path.stat().st_size,
                         "state": "uploaded", "digest": "sha256:" + digest,
                         "browser_download_url": "https://github.com/test/" + name}
                release["assets"].append(asset)
                return asset

        api = PublicationAPI(source_api.entries)
        (self.root / "src").mkdir()
        (self.root / "src/launcher.cpp").write_bytes(self.launcher_source)
        (self.root / "docs").mkdir()
        (self.root / "docs" / f'RELEASE-{self.pack["app_version"]}.md').write_text("Program update fixture.\n")
        (self.dist / "LISEZ-MOI.txt").write_text("Installation fixture.\n")
        with mock.patch.object(publication, "ROOT", self.root), \
                mock.patch.object(publication, "plan", return_value=self.request), \
                mock.patch.object(publication, "verify_run"), \
                mock.patch.object(publication.urllib.request, "urlopen", side_effect=open_metadata):
            with self.assertRaises(RuntimeError):
                publication.publish_launcher_update(api, self.source, 7, self.dist, self.report)
            self.assertTrue(api.entries["/releases/121"]["draft"])
            publication.publish_launcher_update(api, self.source, 7, self.dist, self.report)
        names = {asset["name"] for asset in api.entries["/releases/121"]["assets"]}
        self.assertEqual(names, {"Setup.zip", "VALIDATION-WINDOWS.json", "MODELE.json", "LISEZ-MOI.txt",
                                 "SIGNATURES.json", "SHA256SUMS.txt", "MANIFEST-DISTRIBUTION.json"})
        self.assertEqual(old_release, original_model_release)
        self.assertEqual(api.deletions, [])
        proof = json.loads((self.root / "qa/publication.json").read_text())
        self.assertTrue(proof["publication_verified"])
        self.assertFalse(proof["inference_validation_performed"])
        self.assertEqual(proof["cleanup"]["release_bytes_removed"], 0)
        self.assertTrue(all("/releases/download/v" + self.pack["app_version"] + "/" in asset["url"]
                            for asset in proof["assets"]))


if __name__ == "__main__":
    unittest.main()
