#!/usr/bin/env python3
"""Run the production NSIS path and model-size functions in isolated harnesses.

The source functions and generated production profiles are copied unchanged.
The executable harnesses never install a payload, write registry entries or
start a model. Sparse fixtures exercise actual 64-bit model lengths while
allocating only a few kilobytes. --build-only reports compilation separately
and never claims Windows runtime validation.
"""
from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from pack_profiles import generate, load_pack, nsis_escape, pack_names
from windows_test_support import run_process, sparse_file

ROOT = Path(__file__).resolve().parents[1]
# An unrelated installation is a test identity, not an obsolete production
# pack. Legacy user-data paths remain protected and are exercised separately.
FOREIGN_INSTALLATION = {
    "installation_marker": "OpenCodeLocalForeignFixture:{C8716759-ED28-4744-863D-B6D657B608B6}",
}
CASES = {
    "missing_nested": True,
    "missing_nested_trailing_separator": True,
    "missing_spaces_and_unicode": True,
    "default_location": True,
    "documents_nested": True,
    "existing_empty": True,
    "existing_empty_trailing_separator": True,
    "drive_root": False,
    "documents_root": False,
    "unowned_nonempty": False,
    "existing_file": False,
    "personal_data": False,
    "legacy_personal_data": False,
    "legacy_32go_personal_data": False,
    "owned_installation": True,
    "other_pack_installation": False,
}


def nsis_function(source: str, name: str) -> str:
    match = re.search(r"^Function " + re.escape(name) + r"\s*\n.*?^FunctionEnd[ \t]*$",
                      source, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError("Installer function not found: " + name)
    return match.group(0).replace("${PREFIX}", "")


def shared_header(source: str, executable: Path, config: Path) -> str:
    def definition(name: str) -> str:
        match = re.search(r"^!define " + re.escape(name) + r" (.+)$", source, re.MULTILINE)
        if not match:
            raise ValueError("Installer definition not found: " + name)
        return match.group(1)
    profile = re.search(r"^!define PROFILE_DIRECTORY (.+)$", source, re.MULTILINE)
    profile_value = profile.group(1) if profile else '"${PACK_PROFILE_NAME}"'
    return f'''Target x86-unicode
Unicode true
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
Name "OpenCode Local production-function regression"
OutFile "{nsis_escape(str(executable))}"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "{nsis_escape(str(config))}"
!define PRODUCT_MARKER {definition("PRODUCT_MARKER")}
!define MARKER_PATH {definition("MARKER_PATH")}
!define MODEL_PATH {definition("MODEL_PATH")}
!define PROFILE_DIRECTORY {profile_value}
Var Problem
Var Owned
Var HasContent
Var ModelSourceDir
Var FixtureDir
Var ReportPath
Var Failures
'''


def path_harness(source: str, executable: Path, config: Path, fixture: Path,
                 suffix: str) -> str:
    functions = "\n\n".join(nsis_function(source, name) for name in (
        "${PREFIX}CheckAncestors", "${PREFIX}CheckTree", "${PREFIX}ReadOwner", "ValidateTarget"))
    header = shared_header(source, executable, config)
    header += f'''!define TEST_SUFFIX "{suffix}"
!macro TestPath NAME PATH EXPECTED
  StrCpy $R9 "${{PATH}}"
  StrCpy $INSTDIR $R9
  Call ValidateTarget
  StrCpy $R8 0
  StrCmp $Problem "" 0 +2
  StrCpy $R8 1
  WriteINIStr "$ReportPath" "${{NAME}}" "input" "$R9"
  WriteINIStr "$ReportPath" "${{NAME}}" "normalized" "$INSTDIR"
  WriteINIStr "$ReportPath" "${{NAME}}" "allowed" "$R8"
  WriteINIStr "$ReportPath" "${{NAME}}" "expected" "${{EXPECTED}}"
  WriteINIStr "$ReportPath" "${{NAME}}" "problem" "$Problem"
  StrCmp $R8 "${{EXPECTED}}" +2
  IntOp $Failures $Failures + 1
!macroend
'''
    # The harness only reads candidates outside the test-owned directory.
    section = r'''
Section
  SetShellVarContext current
  SetRegView 64
  StrCpy $Failures 0
  StrCpy $FixtureDir "FIXTURE_PATH"
  StrCpy $ReportPath "$EXEDIR\paths.ini"

  !insertmacro TestPath "missing_nested" "$FixtureDir\new\nested\OpenCodeLocal" 1
  !insertmacro TestPath "missing_nested_trailing_separator" "$FixtureDir\new\nested\OpenCodeLocal\" 1
  !insertmacro TestPath "missing_spaces_and_unicode" "$FixtureDir\Projet été\assistant local" 1
  !insertmacro TestPath "default_location" "$LOCALAPPDATA\Programs\${PACK_INSTALL_DIR}-PathRegression-${TEST_SUFFIX}" 1
  !insertmacro TestPath "documents_nested" "$DOCUMENTS\${PACK_INSTALL_DIR}-PathRegression-${TEST_SUFFIX}\IA\OpenCodeLocal" 1
  !insertmacro TestPath "existing_empty" "$FixtureDir\empty" 1
  !insertmacro TestPath "existing_empty_trailing_separator" "$FixtureDir\empty\" 1
  ${GetRoot} "$WINDIR" $R7
  !insertmacro TestPath "drive_root" "$R7\" 0
  !insertmacro TestPath "documents_root" "$DOCUMENTS" 0
  !insertmacro TestPath "unowned_nonempty" "$FixtureDir\nonempty" 0
  !insertmacro TestPath "existing_file" "$FixtureDir\nonempty\keep.txt" 0
  !insertmacro TestPath "personal_data" "$LOCALAPPDATA\${PROFILE_DIRECTORY}\nested" 0
  !insertmacro TestPath "legacy_personal_data" "$LOCALAPPDATA\OpenCodeLocal\nested" 0
  !insertmacro TestPath "legacy_32go_personal_data" "$LOCALAPPDATA\OpenCodeLocal32Go\nested" 0
  !insertmacro TestPath "owned_installation" "$FixtureDir\owned" 1
  !insertmacro TestPath "other_pack_installation" "$FixtureDir\other-owned" 0

  WriteINIStr "$ReportPath" "summary" "failures" "$Failures"
  SetErrorLevel $Failures
SectionEnd
'''.replace("FIXTURE_PATH", nsis_escape(str(fixture)))
    return header + functions + "\n" + section


def metadata_harness(source: str, executable: Path, config: Path) -> str:
    # Each case runs the same harness beside its fixture files. $EXEDIR and
    # the production ValidateDataParts function are not patched for tests.
    functions = "\n\n".join(nsis_function(source, name) for name in (
        "${PREFIX}CheckAncestors", "${PREFIX}ReadOwner", "ValidateDataParts"))
    return shared_header(source, executable, config) + functions + r'''
Section
  StrCpy $INSTDIR "$EXEDIR\installed"
  Call ValidateDataParts
  StrCpy $0 0
  StrCmp $Problem "" 0 +2
  StrCpy $0 1
  WriteINIStr "$EXEDIR\metadata.ini" "result" "allowed" "$0"
  WriteINIStr "$EXEDIR\metadata.ini" "result" "problem" "$Problem"
  WriteINIStr "$EXEDIR\metadata.ini" "result" "model_source" "$ModelSourceDir"
  IntOp $1 1 - $0
  SetErrorLevel $1
SectionEnd
'''


def read_ini(path: Path) -> configparser.ConfigParser:
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "cp1252"
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(raw.decode(encoding))
    return config


def execute(exe: Path, wine: str | None) -> dict:
    if os.name == "nt":
        command = [str(exe), "/S"]
    elif wine:
        command = [wine, str(exe), "/S"]
    else:
        raise RuntimeError("Windows runtime is required; use --build-only for a compilation check")
    return run_process(command, timeout=45, cwd=exe.parent)


def compile_harness(text: str, path: Path, makensis: str) -> dict:
    path.write_text(text, encoding="utf-8")
    return run_process([makensis, "/V2" if os.name == "nt" else "-V2", str(path)], timeout=90)


def prepare_paths(fixture: Path, pack: dict, other_pack: dict) -> None:
    (fixture / "empty").mkdir(parents=True)
    (fixture / "nonempty").mkdir()
    (fixture / "nonempty/keep.txt").write_text("User data must remain unchanged.\n", encoding="utf-8")
    for label, profile in (("owned", pack), ("other-owned", other_pack)):
        support = fixture / label / "wad/support"
        support.mkdir(parents=True)
        (support / "installation.id").write_text(profile["installation_marker"] + "\n", encoding="utf-8")


def exercise_metadata(folder: Path, exe: Path, pack: dict, wine: str | None) -> list[dict]:
    whole = pack["model"]["bytes"]
    specs = [
        ("full_exact_size", True, whole, "none", None),
        ("full_truncated", False, whole - 1, "none", None),
        ("full_oversized", False, whole + 1, "none", None),
        ("split_exact_sizes", True, None, "exact", None),
        ("split_missing_last", False, None, "missing", None),
        ("split_wrong_last_size", False, None, "wrong", None),
        ("invalid_full_takes_priority", False, whole - 1, "exact", None),
        ("missing_all_sources", False, None, "none", None),
        ("reuse_owned_installed_model", True, None, "none", "exact"),
        ("reject_unowned_installed_model", False, None, "none", "unowned"),
        ("reject_foreign_installed_model", False, None, "none", "foreign"),
        ("reject_truncated_installed_model", False, None, "none", "truncated"),
        ("external_full_precedes_installed", True, whole, "none", "exact"),
        ("external_parts_precede_installed", True, None, "exact", "exact"),
        ("invalid_external_full_no_installed_fallback", False, whole - 1, "none", "exact"),
        ("partial_external_parts_no_installed_fallback", False, None, "missing", "exact"),
    ]
    if whole > 2**32:
        specs.append(("full_low_32_bits_only", False, whole % 2**32, "none", None))
    rows = []
    for name, expected, full_size, parts, installed in specs:
        case = folder / name
        case.mkdir()
        runner = case / "MetadataHarness.exe"
        shutil.copyfile(exe, runner)
        files = []
        if installed is not None:
            support = case / "installed/wad/support"
            support.mkdir(parents=True)
            if installed != "unowned":
                marker = (FOREIGN_INSTALLATION["installation_marker"] if installed == "foreign"
                          else pack["installation_marker"])
                (support / "installation.id").write_text(marker + "\n", encoding="utf-8")
            installed_model = case / "installed/wad/model" / pack["model"]["filename"]
            installed_model.parent.mkdir()
            files.append(sparse_file(installed_model, whole - (1 if installed == "truncated" else 0)))
        if full_size is not None:
            files.append(sparse_file(case / pack["model"]["filename"], full_size))
        if parts != "none":
            for index, part in enumerate(pack["parts"]):
                if parts == "missing" and index == len(pack["parts"]) - 1:
                    continue
                count = part["bytes"] - (1 if parts == "wrong" and index == len(pack["parts"]) - 1 else 0)
                files.append(sparse_file(case / part["name"], count))
        result = execute(runner, wine)
        row = {"case": name, "expected_allowed": expected, "execution": result,
               "fixtures": files, "passed": False}
        ini = case / "metadata.ini"
        if ini.is_file():
            values = read_ini(ini)
            actual = values.getboolean("result", "allowed")
            selected_source = values.get("result", "model_source", fallback="")
            source_matches = True
            if expected:
                expected_source = (case / "installed/wad/model" if name == "reuse_owned_installed_model" else case)
                source_matches = Path(selected_source).resolve() == expected_source.resolve() if os.name == "nt" else True
            row.update(actual_allowed=actual, problem=values.get("result", "problem"),
                       selected_source=selected_source, expected_installed_source=name == "reuse_owned_installed_model",
                       passed=actual == expected and source_matches and result["exit_code"] == (0 if expected else 1))
        else:
            row["problem"] = "Harness produced no result file"
        rows.append(row)
        shutil.rmtree(case)  # Exclusively created inside TemporaryDirectory.
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "installer/OpenCodeLocal.nsi")
    parser.add_argument("--baseline", type=Path, help="Optional older source with current profiles; reported separately")
    parser.add_argument("--pack", choices=pack_names(), action="append")
    parser.add_argument("--makensis", default="makensis")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--runtime-blocker")
    parser.add_argument("--report", type=Path, default=ROOT / "validation/installer-paths.json")
    parser.add_argument("--wine", help="Optional Wine executable for a locally supported environment")
    args = parser.parse_args()
    report = {"schema_version": 2, "scope": "Unmodified production NSIS functions and pack constants",
              "platform": sys.platform, "target": "x86-unicode", "runtime": "not_run",
              "runtime_blocker": args.runtime_blocker, "model_loaded": False,
              "path_cases": CASES, "artifacts": {}, "passed": False}
    try:
        selected = args.pack or pack_names()
        profiles = {name: load_pack(name) for name in selected}
        sources = [("current-" + name, args.source, name) for name in selected]
        if args.baseline:
            sources = [("baseline-" + name, args.baseline, name) for name in selected] + sources
        with tempfile.TemporaryDirectory(prefix="opencode-installer-functions-") as temporary:
            work = Path(temporary)
            for label, source_path, name in sources:
                folder = work / label
                folder.mkdir()
                pack, other = profiles[name], FOREIGN_INSTALLATION
                if pack["installation_marker"] == other["installation_marker"]:
                    raise ValueError("The foreign fixture identity must differ from the production installation")
                generated = folder / "generated"
                generate(pack, generated)  # Real production model sizes and identities.
                config = generated / "pack-profile.nsh"
                source = source_path.read_text(encoding="utf-8")
                fixture = folder / "paths"
                prepare_paths(fixture, pack, other)
                path_exe = folder / "PathHarness.exe"
                data_exe = folder / "MetadataHarness.exe"
                texts = [path_harness(source, path_exe, config, fixture, work.name),
                         metadata_harness(source, data_exe, config)]
                compiled = [compile_harness(text, exe.with_suffix(".nsi"), args.makensis)
                            for text, exe in zip(texts, (path_exe, data_exe))]
                artifact = {"pack": name, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                            "pack_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                            "model_bytes": pack["model"]["bytes"], "model_parts": len(pack["parts"]),
                            "installation_marker": pack["installation_marker"],
                            "profile_directory": pack["profile_name"],
                            "compile_passed": all(row["exit_code"] == 0 for row in compiled),
                            "compilation": compiled, "runtime_passed": False}
                report["artifacts"][label] = artifact
                if not artifact["compile_passed"]:
                    raise RuntimeError("NSIS harness compilation failed for " + name)
                if args.build_only:
                    continue
                execution = execute(path_exe, args.wine)
                artifact["path_execution"] = execution
                ini = folder / "paths.ini"
                if not ini.is_file():
                    raise RuntimeError("Path harness produced no results: " + execution["stderr"])
                values = read_ini(ini)
                rows = []
                for case, expected in CASES.items():
                    actual = values.getboolean(case, "allowed")
                    normalized = values.get(case, "normalized")
                    # Cover the original empty normalized path regression.
                    normalized_ok = bool(normalized) if expected else True
                    rows.append({"case": case, "expected_allowed": expected,
                                 "actual_allowed": actual, "normalized": normalized,
                                 "input": values.get(case, "input"),
                                 "problem": values.get(case, "problem"),
                                 "passed": actual == expected and normalized_ok})
                artifact["path_results"] = rows
                artifact["metadata_results"] = exercise_metadata(folder, data_exe, pack, args.wine)
                artifact["runtime_passed"] = execution["exit_code"] == 0 and all(
                    row["passed"] for row in rows + artifact["metadata_results"])
                report["runtime"] = "executed"
        current = [value for name, value in report["artifacts"].items() if name.startswith("current-")]
        report["compilation_passed"] = bool(current) and all(row["compile_passed"] for row in current)
        report["passed"] = not args.build_only and bool(current) and all(row["runtime_passed"] for row in current)
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        if report["runtime"] == "not_run":
            report["runtime"] = "blocked"
        report["passed"] = False
    if args.runtime_blocker and args.build_only:
        report["runtime"] = "blocked"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report.get(key) for key in ("passed", "compilation_passed", "runtime", "error")},
                     ensure_ascii=False))
    if args.build_only:
        return 0 if report.get("compilation_passed") and not report.get("error") else 1
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
