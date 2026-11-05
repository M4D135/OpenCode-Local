#!/usr/bin/env python3
"""Publish a validated full pack or an explicitly scoped launcher update.

Only a trusted push on main and successful Windows checks can reach publication.
Full packs additionally require real model/tool execution. Launcher updates
require fresh runtime regression checks and reference unchanged model assets;
they never download, upload or re-qualify the model. Full-pack streaming uploads
avoid another complete model copy on the build runner. Old releases are never
removed before the replacement is published and every asset digest is checked.
No Git ref, source commit or tag is deleted.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from pack_profiles import ROOT, load_pack, pack_names
from signing import HOUSE_FILES

REPOSITORY = "M4D135/OpenCode-Local"
API = "https://api.github.com/repos/" + REPOSITORY


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class GitHub:
    def __init__(self) -> None:
        self.token = os.environ.get("GH_TOKEN", "")
        require(bool(self.token), "A repository-scoped Actions token is required")

    def call(self, path: str, method: str = "GET", body: dict | None = None,
             missing_ok: bool = False):
        require(path.startswith("/") and not path.startswith("//"), "Invalid API path")
        request = urllib.request.Request(
            API + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "OpenCode-Local-release"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            if missing_ok and exc.code == 404:
                return None
            detail = exc.read(1000).decode("utf-8", "replace").replace(self.token, "[redacted]")
            raise RuntimeError(f"GitHub {method} {path}: HTTP {exc.code}: {detail}") from None

    def upload(self, release: dict, path: Path, name: str, expected_sha: str) -> dict:
        require(re.fullmatch(r"[A-Za-z0-9_.-]+", name) is not None, "Unsafe release filename")
        require(path.is_file() and not path.is_symlink(), "Upload must be a regular file")
        size = path.stat().st_size
        existing = self.call(f'/releases/{release["id"]}/assets?per_page=100')
        matches = [asset for asset in existing if asset["name"] == name]
        if matches:
            require(len(matches) == 1, "Duplicate release asset name")
            self.check_asset(matches[0], size, expected_sha)
            print("REUSE VERIFIED ASSET", name, size, flush=True)
            return matches[0]
        base = release["upload_url"].split("{", 1)[0]
        parsed = urllib.parse.urlsplit(base)
        require(parsed.scheme == "https" and parsed.hostname == "uploads.github.com"
                and parsed.path == f'/repos/{REPOSITORY}/releases/{release["id"]}/assets',
                "Unexpected GitHub upload endpoint")
        connection = http.client.HTTPSConnection(parsed.hostname, timeout=600, blocksize=1024 * 1024)
        try:
            with path.open("rb") as stream:
                connection.request("POST", parsed.path + "?" + urllib.parse.urlencode({"name": name}),
                                   body=stream, headers={"Authorization": "Bearer " + self.token,
                                       "Content-Type": "application/octet-stream", "Content-Length": str(size),
                                       "Accept": "application/vnd.github+json",
                                       "X-GitHub-Api-Version": "2022-11-28",
                                       "User-Agent": "OpenCode-Local-release"})
                response = connection.getresponse()
                data = response.read()
                require(response.status == 201, f"Upload failed for {name}: HTTP {response.status}")
                asset = json.loads(data)
        finally:
            connection.close()
        self.check_asset(asset, size, expected_sha)
        print("UPLOADED AND VERIFIED", name, size, flush=True)
        return asset

    @staticmethod
    def check_asset(asset: dict, size: int, digest: str) -> None:
        require(asset.get("state") == "uploaded" and asset.get("size") == size,
                "Incomplete or differently sized release asset")
        require(asset.get("digest") == "sha256:" + digest,
                "GitHub release digest differs from the local file")


def plan() -> dict:
    value = json.loads((ROOT / "config/release-plan.json").read_text(encoding="utf-8"))
    pack = load_pack("moe")
    require(value.get("schema_version") == 1 and value.get("repository") == REPOSITORY,
            "Unexpected publication plan")
    require(pack_names() == ("moe",) and value.get("pack") == "moe", "Only the single MoE pack may be published")
    require(value.get("version") == pack["app_version"] and value.get("signing") == "unsigned",
            "Publication version or explicit signing status differs from source")
    value["kind"] = value.get("kind", "full-pack")
    require(value["kind"] in {"full-pack", "launcher-update"}, "Unknown publication kind")
    tags, ids = set(), set()
    current = tuple(map(int, value["version"].split(".")))
    for entry in value["remove_releases_after_verified_publication"]:
        tag = entry["tag"]
        require(re.fullmatch(r"v\d+\.\d+\.\d+", tag) is not None, "Invalid obsolete release tag")
        require(tuple(map(int, tag[1:].split("."))) < current, "Cannot remove the current or a newer release")
        require(tag not in tags and entry["id"] not in ids, "Duplicate cleanup target")
        tags.add(tag)
        ids.add(entry["id"])
    if value["kind"] == "launcher-update":
        source_tag = value.get("model_source_release", "")
        require(re.fullmatch(r"v\d+\.\d+\.\d+", source_tag) is not None
                and tuple(map(int, source_tag[1:].split("."))) < current,
                "A launcher update must name an earlier model source release")
        require(not value["remove_releases_after_verified_publication"]
                and not value["remove_obsolete_actions_artifacts"],
                "Launcher updates cannot delete releases or model source assets")
    return value


def verify_run(api: GitHub, source: str, run_id: int, *, current_checkout: bool = True) -> dict:
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None, "Expected a full source commit")
    require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY, "Wrong repository")
    if current_checkout:
        checked_out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        require(checked_out == source, "Checkout differs from the validated commit")
    run = api.call(f"/actions/runs/{run_id}")
    require(run.get("head_sha") == source and run.get("head_branch") == "main"
            and run.get("event") == "push" and run.get("status") == "completed"
            and run.get("conclusion") == "success"
            and run.get("path") == ".github/workflows/validate-unsigned.yml"
            and run.get("head_repository", {}).get("full_name") == REPOSITORY,
            "Publication requires this repository's successful main-push Windows workflow")
    if current_checkout:
        head = api.call("/git/ref/heads/main")["object"]["sha"]
        require(head == source, "Main has moved; wait for validation of its new commit")
    return run


def check_tag(api: GitHub, version: str, source: str, *, required: bool = False) -> None:
    tag = api.call("/git/ref/tags/v" + version, missing_ok=not required)
    if tag is not None:
        require(tag["object"]["type"] == "commit" and tag["object"]["sha"] == source,
                "Existing release tag does not identify the validated source")


def preflight(api: GitHub, source: str, run_id: int) -> None:
    value = plan()
    verify_run(api, source, run_id)
    existing = api.call("/releases/tags/v" + value["version"], missing_ok=True)
    output = {"ready": "false", "cleanup_ready": "false", "kind": value["kind"]}
    if existing and not existing.get("draft"):
        output["cleanup_ready"] = "true"
        print("Version already published; verify its manifest and finish only the requested cleanup", flush=True)
    else:
        check_tag(api, value["version"], source)
        if existing:
            require(existing.get("target_commitish") == source, "An existing draft belongs to another commit")
        artifacts = api.call(f"/actions/runs/{run_id}/artifacts?per_page=100")["artifacts"]
        for prefix, key in (("unsigned-candidates-", "candidate_id"),
                            ("unsigned-windows-reports-", "windows_report_id")):
            candidates = [a for a in artifacts if not a["expired"]
                          and re.fullmatch(prefix + str(run_id) + r"-\d+", a["name"])
                          and a.get("workflow_run", {}).get("head_sha") == source]
            require(bool(candidates), "Missing verified workflow artifact: " + prefix)
            latest = max(candidates, key=lambda a: (a["created_at"], a["id"]))
            require(str(latest.get("digest", "")).startswith("sha256:"), "Missing artifact digest")
            output[key] = str(latest["id"])
        output["ready"] = "true"
    destination = os.environ.get("GITHUB_OUTPUT")
    require(bool(destination), "GITHUB_OUTPUT is required")
    with open(destination, "a", encoding="utf-8") as stream:
        for key, value in output.items():
            stream.write(f"{key}={value}\n")


def validate_windows_reports(source: str, dist: Path, windows_report: Path) -> dict:
    pack = load_pack("moe")
    final = json.loads((dist / "SIGNATURES.json").read_text(encoding="utf-8"))
    require(final.get("pack") == "moe" and final.get("version") == pack["app_version"]
            and final.get("source_commit") == source and final.get("signing") == "unsigned"
            and final.get("zip_contains") == ["Setup.exe"] and final.get("model") == pack["model"],
            "Final packaging manifest differs from the validated source/model")
    archive = dist / "Setup.zip"
    require(archive.stat().st_size == final["zip_bytes"] and sha256(archive) == final["zip_sha256"],
            "Final ZIP digest mismatch")
    with zipfile.ZipFile(archive) as zipped:
        require(zipped.namelist() == ["Setup.exe"] and zipped.testzip() is None, "Invalid setup-only ZIP")
        with zipped.open("Setup.exe") as stream:
            require(hashlib.file_digest(stream, "sha256").hexdigest() == final["setup"]["sha256"],
                    "ZIP changed the installer")
    windows = json.loads(windows_report.read_text(encoding="utf-8-sig"))
    require(windows.get("passed") is True and windows.get("phase") == "All"
            and windows.get("packs") == ["moe"] and windows.get("checks")
            and all(check.get("passed") is True for check in windows["checks"]),
            "Windows component checks did not pass")
    # Link the Windows report to exactly the setup we are about to publish.
    matching = [c for c in windows["checks"] if c["name"] == "moe/setup/archive-and-exact-native-payload"]
    require(len(matching) == 1 and matching[0].get("details", {}).get("archive_sha256") == final["setup"]["sha256"],
            "Windows checked another installer")
    return {"pack": pack, "final": final, "windows": windows}


def validate_reports(source: str, dist: Path, model_report: Path, windows_report: Path) -> dict:
    checked = validate_windows_reports(source, dist, windows_report)
    pack, final = checked["pack"], checked["final"]
    runtime = json.loads(model_report.read_text(encoding="utf-8"))
    require(runtime.get("passed") is True and runtime.get("context") == pack["default_context"],
            "Real model/harness validation did not pass at the default context")
    for test in ("text", "tool_call", "tool_result", "opencode"):
        require(runtime.get("tests", {}).get(test, {}).get("passed") is True,
                "Missing successful runtime check: " + test)
    require(runtime["tests"]["opencode"].get("executed") is True, "OpenCode was not actually executed")
    observed = runtime.get("model", {})
    require(observed.get("filename") == pack["model"]["filename"]
            and observed.get("bytes") == pack["model"]["bytes"]
            and observed.get("sha256") == pack["model"]["sha256"], "Runtime tested another model")
    peak = runtime.get("memory", {}).get("peak_combined_rss_bytes")
    require(isinstance(peak, (int, float)) and 0 < peak <= 14 * 2**30,
            "Measured engine/harness RSS exceeds the 14-GiB release budget or was not measured")
    full = runtime.get("memory", {}).get("peak_full_residency_estimate_bytes")
    require(runtime.get("memory", {}).get("passed") is True
            and isinstance(full, (int, float)) and 0 < full <= 14 * 2**30,
            "Full-residency memory allowance exceeds the release budget or was not measured")
    return {"pack": pack, "final": final, "runtime": runtime}


def validate_launcher_update(source: str, dist: Path, windows_report: Path) -> dict:
    """Qualify the newly built program; never accept an old inference report."""
    checked = validate_windows_reports(source, dist, windows_report)
    pack, final, windows = checked["pack"], checked["final"], checked["windows"]
    require(windows.get("source_commit") == source
            and windows.get("app_version") == pack["app_version"],
            "Launcher update requires this commit and version's Windows report")
    require(final.get("model_downloads") is False and final.get("parts") == []
            and windows.get("model_downloaded") is False and windows.get("model_loaded") is False,
            "Launcher update must be validated and packaged without a model download")
    checks = {item["name"]: item for item in windows["checks"]}
    require(len(checks) == len(windows["checks"]), "Duplicate Windows check names")
    required_checks = {"moe/launcher-diagnostic", "assembler/real-fixtures-and-production-metadata",
                       "installer/production-path-and-size-functions", "moe/setup/metadata-and-signature",
                       "moe/setup/archive-and-exact-native-payload", "launcher/runtime-regressions"}
    required_checks.update("moe/native/" + name for name in HOUSE_FILES)
    require(required_checks <= checks.keys(), "Launcher update is missing required Windows checks")
    regression = checks["launcher/runtime-regressions"].get("details", {})
    require(regression.get("passed") is True
            and regression.get("execution", {}).get("exit_code") == 0
            and regression.get("execution", {}).get("timed_out") is False
            and regression.get("results", {}).get("passed") is True,
            "Fresh launcher runtime regressions did not pass")
    runtime = regression["results"]
    require(runtime.get("build_only") is False and runtime.get("runtime_executed") is True
            and runtime.get("app_version") == pack["app_version"]
            and runtime.get("launcher_source_sha256") == sha256(ROOT / "src/launcher.cpp"),
            "Launcher regression report did not execute this version's current source")
    fixtures = runtime.get("checks")
    require(isinstance(fixtures, list) and fixtures
            and all(isinstance(fixture, dict) for fixture in fixtures),
            "Launcher regression report has no executed fixtures")
    fixture_names = {fixture.get("name") for fixture in fixtures}
    require(len(fixture_names) == len(fixtures) and
            {"launcher_process_tests", "launcher_profile_tests", "launcher_http_ui_tests"} <= fixture_names,
            "Launcher regression report is missing a required fixture")
    for fixture in fixtures:
        results = fixture.get("results") or {}
        assertions = results.get("checks")
        require(fixture.get("runtime_executed") is True and fixture.get("passed") is True
                and results.get("passed") is True and isinstance(assertions, list) and assertions
                and all(isinstance(assertion, dict) and assertion.get("passed") is True
                        for assertion in assertions),
                "Launcher fixture was not executed or its checks did not pass")
    native = final.get("native_validation", {})
    require(set(native) == {"moe/" + name for name in HOUSE_FILES},
            "Launcher update must identify all three current project executables")
    embedded = checks["moe/setup/archive-and-exact-native-payload"]["details"].get(
        "embedded_home_built_executables", [])
    by_name = {item.get("inspection", {}).get("metadata", {}).get("original_filename"): item
               for item in embedded}
    require(len(embedded) == len(HOUSE_FILES) and set(by_name) == set(HOUSE_FILES),
            "Installer inspection must identify each embedded project executable exactly once")
    for name in HOUSE_FILES:
        expected = native["moe/" + name]
        actual = checks["moe/native/" + name].get("details", {})
        row = by_name[name]
        metadata = actual.get("metadata", {})
        require(actual.get("passed") is True and actual.get("sha256") == expected.get("sha256")
                and metadata.get("file_bytes") == expected.get("bytes")
                and metadata.get("product_version") in {pack["app_version"], pack["app_version"] + ".0"}
                and actual.get("authenticode", {}).get("status") == "NotSigned",
                "Windows checked another native executable: " + name)
        require(row.get("passed") is True and row.get("exact_native_match") is True
                and row.get("extracted_sha256") == expected["sha256"]
                and row.get("native_sha256") == expected["sha256"]
                and row.get("inspection", {}).get("sha256") == expected["sha256"],
                "Installer embeds another native executable: " + name)
    setup_check = checks["moe/setup/metadata-and-signature"].get("details", {})
    require(setup_check.get("sha256") == final["setup"]["sha256"]
            and setup_check.get("metadata", {}).get("product_version") in
            {pack["app_version"], pack["app_version"] + ".0"},
            "Windows checked another setup version")
    return checked


def read_release_json(release: dict, tag: str, filename: str) -> tuple[dict, dict]:
    """Read only a small public JSON asset; no Actions token reaches storage."""
    matches = [item for item in release["assets"] if item["name"] == filename]
    require(len(matches) == 1, "Model source is missing metadata: " + filename)
    asset = matches[0]
    expected_url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}"
    require(asset.get("browser_download_url") == expected_url
            and 0 < asset.get("size", 0) <= 2 * 1024 * 1024,
            "Unexpected model metadata URL or size")
    with urllib.request.urlopen(expected_url, timeout=90) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    require(len(data) <= 2 * 1024 * 1024, "Model metadata exceeds the size limit")
    GitHub.check_asset(asset, len(data), hashlib.sha256(data).hexdigest())
    value = json.loads(data.decode("utf-8-sig"))
    require(isinstance(value, dict), "Release metadata must be a JSON object")
    return value, asset


def verify_model_reference(api: GitHub, value: dict, pack: dict) -> dict:
    """Verify existing slice identities using metadata, without reading weights."""
    require(value.get("kind") == "launcher-update", "A model reference is only used for launcher updates")
    tag = value.get("model_source_release", "")
    require(re.fullmatch(r"v\d+\.\d+\.\d+", tag) is not None
            and tuple(map(int, tag[1:].split("."))) < tuple(map(int, value["version"].split("."))),
            "Invalid model source release")
    require(not value["remove_releases_after_verified_publication"]
            and not value["remove_obsolete_actions_artifacts"],
            "The model source release and its assets must be preserved")
    release = api.call("/releases/tags/" + tag)
    require(release.get("draft") is False and release.get("tag_name") == tag,
            "Model source release must be published")
    proof, proof_asset = read_release_json(release, tag, "MANIFEST-DISTRIBUTION.json")
    require(proof.get("assets_verified") is True and proof.get("version") == tag[1:]
            and proof.get("model_sha256") == pack["model"]["sha256"]
            and re.fullmatch(r"[0-9a-f]{40}", proof.get("source_commit", "")) is not None,
            "Model source manifest does not identify the unchanged model")
    check_tag(api, tag[1:], proof["source_commit"], required=True)
    model, model_asset = read_release_json(release, tag, "MODELE.json")
    require(all(model.get(key) == expected for key, expected in pack["model"].items()),
            "Launcher update changed the model definition")
    recorded = {item["name"]: item for item in proof.get("assets", [])}
    remote = {item["name"]: item for item in release["assets"]}
    require(len(recorded) == len(proof.get("assets", [])) and len(remote) == len(release["assets"]),
            "Duplicate model source assets")

    def match_record(asset: dict, expected_bytes: int, expected_sha: str) -> None:
        original = recorded.get(asset["name"], {})
        require(original.get("id") == asset["id"] and original.get("bytes") == expected_bytes
                and original.get("sha256") == expected_sha,
                "Model source asset differs from its publication manifest: " + asset["name"])
        GitHub.check_asset(asset, expected_bytes, expected_sha)

    match_record(model_asset, model_asset["size"], model_asset["digest"].removeprefix("sha256:"))
    definitions = pack["parts"]
    old_parts = model.get("parts", [])
    require(len(definitions) == len(old_parts) == 8, "The unchanged model must have exactly eight slices")
    require({item["name"] for item in release["assets"] if item["name"].endswith(".bin")} ==
            {item["name"] for item in definitions}, "Model source slice set has changed")
    parts = []
    for definition, old in zip(definitions, old_parts):
        name = definition["name"]
        digest = old.get("sha256", "")
        require(old.get("name") == name and old.get("bytes") == definition["bytes"]
                and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                "Model source slice metadata differs: " + name)
        asset = remote[name]
        match_record(asset, definition["bytes"], digest)
        url = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
        require(asset.get("browser_download_url") == url, "Unexpected model slice URL")
        parts.append({"name": name, "bytes": definition["bytes"], "sha256": digest,
                      "asset_id": asset["id"], "url": url})
    return {"release_tag": tag, "release_id": release["id"], "source_commit": proof["source_commit"],
            "release_url": f"https://github.com/{REPOSITORY}/releases/tag/{tag}",
            "manifest_sha256": proof_asset["digest"].removeprefix("sha256:"),
            "model_metadata_sha256": model_asset["digest"].removeprefix("sha256:"),
            "model_sha256": pack["model"]["sha256"], "model_bytes": pack["model"]["bytes"],
            "parts": parts, "weights_downloaded": False, "inference_executed": False,
            "verification_scope": "Published JSON metadata and GitHub slice identities/digests only"}


def verify_assets(api: GitHub, release_id: int, assets: list[dict]) -> dict:
    release = api.call(f"/releases/{release_id}")
    remote = {item["name"]: item for item in release["assets"]}
    require(set(remote) == {item["name"] for item in assets}, "Release asset list is incomplete or has extra files")
    for asset in assets:
        GitHub.check_asset(remote[asset["name"]], asset["bytes"], asset["sha256"])
        require(remote[asset["name"]]["id"] == asset["id"], "Release asset identity changed")
    return release


def clean_old(api: GitHub, value: dict, replacement: dict) -> dict:
    require(replacement.get("draft") is False and replacement.get("tag_name") == "v" + value["version"],
            "Replacement must be public before cleanup")
    if value.get("kind") == "launcher-update":
        require(not value["remove_releases_after_verified_publication"]
                and not value["remove_obsolete_actions_artifacts"],
                "Launcher update cleanup must preserve the model source release")
    results = {"releases": [], "actions_artifacts": [], "release_bytes_removed": 0,
               "actions_bytes_removed": 0, "git_history_and_tags_deleted": False}
    for target in value["remove_releases_after_verified_publication"]:
        require(target["id"] != replacement["id"], "Refusing to delete replacement")
        old = api.call(f'/releases/{target["id"]}', missing_ok=True)
        if old is None:
            results["releases"].append({**target, "status": "already_absent"})
            continue
        require(old["tag_name"] == target["tag"] and not old.get("draft") and not old.get("immutable"),
                "Obsolete release changed; refusing deletion")
        size = sum(asset["size"] for asset in old["assets"])
        require(size == target["expected_asset_bytes"], "Obsolete release assets changed since the requested cleanup")
        api.call(f'/releases/{target["id"]}', "DELETE")
        require(api.call(f'/releases/{target["id"]}', missing_ok=True) is None, "Release removal not confirmed")
        results["release_bytes_removed"] += size
        results["releases"].append({**target, "status": "deleted", "bytes_removed": size})
        print("REMOVED OBSOLETE RELEASE", target["tag"], size, flush=True)
    for artifact_id in value["remove_obsolete_actions_artifacts"]:
        old = api.call(f"/actions/artifacts/{artifact_id}", missing_ok=True)
        if old is None:
            results["actions_artifacts"].append({"id": artifact_id, "status": "already_absent"})
            continue
        require(re.fullmatch(r"unsigned-(candidates|linux-reports|windows-reports)-\d+-\d+", old["name"]) is not None,
                "Unexpected obsolete artifact name")
        require(old.get("created_at", "") < "2026-11-05T00:00:00Z", "Refusing to remove a new artifact")
        api.call(f"/actions/artifacts/{artifact_id}", "DELETE")
        require(api.call(f"/actions/artifacts/{artifact_id}", missing_ok=True) is None, "Artifact removal not confirmed")
        results["actions_bytes_removed"] += old["size_in_bytes"]
        results["actions_artifacts"].append({"id": artifact_id, "name": old["name"], "status": "deleted",
                                             "bytes_removed": old["size_in_bytes"]})
    return results


def resume_cleanup(api: GitHub, source: str, run_id: int) -> None:
    value = plan()
    verify_run(api, source, run_id)
    release = api.call("/releases/tags/v" + value["version"])
    require(release.get("draft") is False, "Cleanup requires a public replacement")
    manifests = [item for item in release["assets"] if item["name"] == "MANIFEST-DISTRIBUTION.json"]
    require(len(manifests) == 1, "Published distribution manifest is missing")
    asset = manifests[0]
    expected_url = f'https://github.com/{REPOSITORY}/releases/download/v{value["version"]}/MANIFEST-DISTRIBUTION.json'
    require(asset["browser_download_url"] == expected_url and asset["size"] <= 2 * 1024 * 1024,
            "Unexpected distribution manifest URL or size")
    # Public download: never send the repository token to the redirect/storage host.
    with urllib.request.urlopen(expected_url, timeout=90) as response:
        data = response.read(2 * 1024 * 1024 + 1)
    GitHub.check_asset(asset, len(data), hashlib.sha256(data).hexdigest())
    proof = json.loads(data)
    require(proof.get("assets_verified") is True and proof.get("signing") == "unsigned"
            and proof.get("version") == value["version"]
            and proof.get("model_sha256") == load_pack("moe")["model"]["sha256"],
            "Published manifest differs from this requested version")
    if value["kind"] == "launcher-update":
        require(proof.get("kind") == "launcher-update"
                and proof.get("model_reference") == verify_model_reference(api, value, load_pack("moe")),
                "Published launcher update lost or changed its model reference")
    check_tag(api, value["version"], proof["source_commit"], required=True)
    verify_run(api, proof["source_commit"], int(proof["windows_run_id"]), current_checkout=False)
    records = list(proof["assets"]) + [{"name": asset["name"], "id": asset["id"],
        "bytes": asset["size"], "sha256": hashlib.sha256(data).hexdigest()}]
    verified = verify_assets(api, release["id"], records)
    result = {**proof, "publication_verified": True, "release_url": verified["html_url"], "cleanup": clean_old(api, value, verified)}
    target = ROOT / "qa/publication.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    print("PUBLICATION AND CLEANUP VERIFIED", verified["html_url"], flush=True)


def publish_launcher_update(api: GitHub, source: str, run_id: int, dist: Path,
                            windows_report: Path) -> None:
    value = plan()
    require(value["kind"] == "launcher-update", "This publication path only accepts launcher updates")
    verify_run(api, source, run_id)
    check_tag(api, value["version"], source)
    checked = validate_launcher_update(source, dist, windows_report)
    pack, final = checked["pack"], checked["final"]
    reference = verify_model_reference(api, value, pack)
    notes = (ROOT / "docs" / f'RELEASE-{value["version"]}.md').read_text(encoding="utf-8")
    notes += f"\n\nConstruction : `{source}`. [Validation Windows](https://github.com/{REPOSITORY}/actions/runs/{run_id}).\n"
    release = api.call("/releases/tags/v" + value["version"], missing_ok=True)
    if release:
        require(release.get("draft") and release.get("target_commitish") == source,
                "Refusing to replace a published release or another build's draft")
    else:
        release = api.call("/releases", "POST", {"tag_name": "v" + value["version"],
            "target_commitish": source,
            "name": f'OpenCode Local {value["version"]} — corrections du lanceur',
            "body": notes, "draft": True, "prerelease": False})
    assets = []

    def upload(path: Path, name: str | None = None, digest: str | None = None):
        name = name or path.name
        digest = digest or sha256(path)
        remote = api.upload(release, path, name, digest)
        record = {"name": name, "id": remote["id"], "bytes": path.stat().st_size,
                  "sha256": digest,
                  "url": f'https://github.com/{REPOSITORY}/releases/download/v{value["version"]}/{name}'}
        assets.append(record)
        return record

    # The existing model stays in its original release. No local model path
    # is opened, no slices are regenerated, and no old inference report is used.
    final["release_kind"] = "launcher-update"
    final["model_reference"] = reference
    final["inference_validation"] = {"performed": False,
        "reason": "Program-only update; unchanged weights are referenced by their published identities"}
    (dist / "SIGNATURES.json").write_text(json.dumps(final, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (dist / "MODELE.json").write_text(json.dumps({**pack["model"],
        "parts": reference["parts"], "source_release": reference["release_tag"],
        "parts_in_this_release": False}, indent=2) + "\n", encoding="utf-8")
    notice = dist / "LISEZ-MOI.txt"
    introduction = (
        f'MISE À JOUR DU PROGRAMME {value["version"]} — modèle inchangé\n\n'
        'Pour une installation 1.2.0 existante, conserver son dossier et le choisir\n'
        'dans le nouveau setup : le modèle installé est vérifié puis réutilisé.\n'
        'Pour une première installation, les huit parties du modèle restent ici :\n'
        + reference["release_url"] + '\n\n')
    text = notice.read_text(encoding="utf-8")
    if not text.startswith(introduction):
        notice.write_text(introduction + text, encoding="utf-8")
    upload(dist / "Setup.zip", digest=final["zip_sha256"])
    for path, name in ((windows_report, "VALIDATION-WINDOWS.json"), (dist / "MODELE.json", None),
                       (notice, None), (dist / "SIGNATURES.json", None)):
        upload(path, name)
    checksums = {item["name"]: item["sha256"] for item in assets}
    checksums["Setup.exe"] = final["setup"]["sha256"]
    checksums[pack["model"]["filename"]] = pack["model"]["sha256"]
    sums = dist / "SHA256SUMS.txt"
    sums.write_text("".join(f"{digest}  {name}\n" for name, digest in checksums.items()), encoding="ascii")
    upload(sums)
    proof = {"schema_version": 1, "kind": "launcher-update", "version": value["version"],
             "source_commit": source, "windows_run_id": run_id,
             "model_sha256": pack["model"]["sha256"], "model_reference": reference,
             "signing": "unsigned", "assets_verified": True, "assets": list(assets),
             "inference_validation_performed": False, "tehtris_compatibility": "not_validated",
             "verification_scope": "Fresh Windows component and launcher regression tests; unchanged model referenced, not re-executed"}
    manifest = dist / "MANIFEST-DISTRIBUTION.json"
    manifest.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    upload(manifest)
    verify_assets(api, release["id"], assets)
    require(verify_model_reference(api, value, pack) == reference,
            "Model source changed during launcher update publication")
    verify_run(api, source, run_id)
    check_tag(api, value["version"], source)
    api.call(f'/releases/{release["id"]}', "PATCH", {"draft": False, "make_latest": "true", "body": notes})
    published = verify_assets(api, release["id"], assets)
    require(published.get("draft") is False and published.get("tag_name") == "v" + value["version"],
            "Launcher update publication not confirmed")
    public_urls = {asset["name"]: asset.get("browser_download_url") for asset in published["assets"]}
    require(all(public_urls[asset["name"]] == asset["url"] for asset in assets),
            "Published launcher asset URLs differ from the release tag")
    check_tag(api, value["version"], source, required=True)
    report = {**proof, "assets": assets, "release_url": published["html_url"],
              "publication_verified": True, "cleanup": clean_old(api, value, published)}
    report_path = ROOT / "qa/publication.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("LAUNCHER UPDATE VERIFIED; MODEL SOURCE PRESERVED", published["html_url"], flush=True)


def publish(api: GitHub, source: str, run_id: int, dist: Path, model_report: Path,
            windows_report: Path) -> None:
    value = plan()
    require(value["kind"] == "full-pack", "Full-pack publication cannot handle a launcher update")
    verify_run(api, source, run_id)
    check_tag(api, value["version"], source)
    checked = validate_reports(source, dist, model_report, windows_report)
    pack, final = checked["pack"], checked["final"]
    model = ROOT / "downloads" / pack["model"]["filename"]
    require(model.is_file() and not model.is_symlink() and model.stat().st_size == pack["model"]["bytes"]
            and sha256(model) == pack["model"]["sha256"], "Model source SHA-256 mismatch")
    notes = (ROOT / "docs/RELEASE-1.2.0.md").read_text(encoding="utf-8")
    notes += f"\n\nConstruction : `{source}`. [Validation Windows](https://github.com/{REPOSITORY}/actions/runs/{run_id}).\n"
    release = api.call("/releases/tags/v" + value["version"], missing_ok=True)
    if release:
        require(release.get("draft") and release.get("target_commitish") == source,
                "Refusing to replace a published release or another build's draft")
    else:
        release = api.call("/releases", "POST", {"tag_name": "v" + value["version"],
            "target_commitish": source, "name": "OpenCode Local 1.2.0 — pack unique MoE léger",
            "body": notes, "draft": True, "prerelease": False})
    assets = []

    def upload(path: Path, name: str | None = None, digest: str | None = None):
        name = name or path.name
        digest = digest or sha256(path)
        remote = api.upload(release, path, name, digest)
        record = {"name": name, "id": remote["id"], "bytes": path.stat().st_size,
                  "sha256": digest, "url": remote["browser_download_url"]}
        assets.append(record)
        return record

    upload(dist / "Setup.zip", digest=final["zip_sha256"])
    parts = []
    with tempfile.TemporaryDirectory(prefix="opencode-release-part-") as temporary, model.open("rb") as source_file:
        whole = hashlib.sha256()
        for definition in pack["parts"]:
            path = Path(temporary) / definition["name"]
            digest = hashlib.sha256()
            remaining = definition["bytes"]
            with path.open("xb") as output:
                while remaining:
                    block = source_file.read(min(4 * 1024 * 1024, remaining))
                    require(bool(block), "Model truncated during streaming split")
                    output.write(block)
                    digest.update(block)
                    whole.update(block)
                    remaining -= len(block)
            record = upload(path, digest=digest.hexdigest())
            parts.append({key: record[key] for key in ("name", "bytes", "sha256")})
            path.unlink()  # Only our completed temporary slice, after digest-confirmed upload.
        require(not source_file.read(1) and whole.hexdigest() == pack["model"]["sha256"],
                "Model changed during streaming split")
    (dist / "MODELE.json").write_text(json.dumps({**pack["model"], "parts": parts}, indent=2) + "\n")
    final["parts"] = parts
    (dist / "SIGNATURES.json").write_text(json.dumps(final, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for path, name in ((model_report, "VALIDATION-MODELE.json"), (windows_report, "VALIDATION-WINDOWS.json"),
                       (dist / "MODELE.json", None), (dist / "LISEZ-MOI.txt", None), (dist / "SIGNATURES.json", None)):
        upload(path, name)
    checksums = {item["name"]: item["sha256"] for item in assets}
    checksums["Setup.exe"] = final["setup"]["sha256"]
    checksums[pack["model"]["filename"]] = pack["model"]["sha256"]
    sums = dist / "SHA256SUMS.txt"
    sums.write_text("".join(f"{digest}  {name}\n" for name, digest in checksums.items()), encoding="ascii")
    upload(sums)
    proof = {"schema_version": 1, "version": value["version"], "source_commit": source,
             "windows_run_id": run_id, "model_sha256": pack["model"]["sha256"],
             "signing": "unsigned", "assets_verified": True, "assets": list(assets),
             "verification_scope": "All listed assets digest-verified in the draft; publication and cleanup checked separately"}
    distribution_manifest = dist / "MANIFEST-DISTRIBUTION.json"
    distribution_manifest.write_text(json.dumps(proof, indent=2) + "\n")
    upload(distribution_manifest)
    verify_assets(api, release["id"], assets)
    verify_run(api, source, run_id)  # Stop before publication if main changed while uploading.
    check_tag(api, value["version"], source)
    api.call(f'/releases/{release["id"]}', "PATCH", {"draft": False, "make_latest": "true", "body": notes})
    published = verify_assets(api, release["id"], assets)
    require(not published["draft"] and published["tag_name"] == "v" + value["version"], "Publication not confirmed")
    check_tag(api, value["version"], source, required=True)
    report = {"release_url": published["html_url"], "source_commit": source, "windows_run_id": run_id,
              "signing": "unsigned", "assets": assets, "model_sha256": pack["model"]["sha256"],
              "publication_verified": True}
    report_path = ROOT / "qa/publication.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    report["cleanup"] = clean_old(api, value, published)
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print("PUBLICATION VERIFIED", published["html_url"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "publish", "publish-launcher-update", "cleanup"))
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--run-id", required=True, type=int)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist/moe")
    parser.add_argument("--model-report", type=Path, default=ROOT / "qa/model-runtime.json")
    parser.add_argument("--windows-report", type=Path, default=ROOT / "qa/ci-reports/validation/windows-unsigned.json")
    args = parser.parse_args()
    api = GitHub()
    if args.phase == "preflight":
        preflight(api, args.source_sha, args.run_id)
    elif args.phase == "cleanup":
        resume_cleanup(api, args.source_sha, args.run_id)
    elif args.phase == "publish-launcher-update":
        publish_launcher_update(api, args.source_sha, args.run_id, args.dist.resolve(), args.windows_report.resolve())
    else:
        publish(api, args.source_sha, args.run_id, args.dist.resolve(),
                args.model_report.resolve(), args.windows_report.resolve())


if __name__ == "__main__":
    main()
