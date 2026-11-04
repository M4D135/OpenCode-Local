#!/usr/bin/env python3
"""Exercise the installer's actual NSIS path functions without installing a payload.

The generated x86 Unicode NSIS harness matches the production installer target
and can be run on Windows directly, or with a Wine installation supporting it.
Only temporary fixtures bearing a per-process name are created and removed.
Use --build-only where Windows execution is unavailable. Compilation alone is
reported separately from runtime validation; it is never considered a pass.
"""
import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from pack_profiles import load_pack, nsis_escape

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/test-installer-paths"
CASES = {
    "missing_nested": True,
    "default_location": True,
    "documents_nested": True,
    "existing_empty": True,
    "existing_empty_trailing_separator": True,
    "drive_root": False,
    "documents_root": False,
    "unowned_nonempty": False,
    "personal_data": False,
    "other_pack_personal_data": False,
    "owned_installation": True,
    "other_pack_installation": False,
}


def nsis_function(source, name):
    match = re.search(r"^Function " + re.escape(name) + r"\n.*?^FunctionEnd\s*$",
                      source, re.MULTILINE | re.DOTALL)
    if not match:
        raise ValueError("Installer function not found: " + name)
    return match.group(0).replace("${PREFIX}", "")


def make_harness(source, label, pack, other_pack):
    marker = re.search(r'^!define PRODUCT_MARKER (.+)$', source, re.MULTILINE).group(1)
    marker_path = re.search(r'^!define MARKER_PATH (.+)$', source, re.MULTILINE).group(1)
    config = ROOT / "build" / pack["id"] / "generated" / "pack-profile.nsh"
    profile = re.search(r'^!define PROFILE_DIRECTORY (.+)$', source, re.MULTILINE)
    profile_definition = profile.group(1) if profile else '"${PACK_PROFILE_NAME}"'
    functions = "\n\n".join(nsis_function(source, name) for name in (
        "${PREFIX}CheckAncestors", "${PREFIX}CheckTree", "${PREFIX}ReadOwner", "ValidateTarget"))
    # Retain the actual full-file 64-bit size and disk checks in the compiled
    # harness as well. Runtime path cases do not create multi-gigabyte files.
    auxiliary = []
    for name in ("ValidateDataParts", "CheckDiskSpace"):
        if re.search(r"^Function " + name + r"$", source, re.MULTILINE):
            functions += "\n\n" + nsis_function(source, name)
            auxiliary.append(name)
    header = f'''Target x86-unicode
Unicode true
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
Name "OpenCode Local path regression"
OutFile "{(BUILD / (label + '.exe')).as_posix()}"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "{nsis_escape(config.as_posix())}"
!define PRODUCT_MARKER {marker}
!define MARKER_PATH {marker_path}
!define PROFILE_DIRECTORY {profile_definition}
!define OTHER_PROFILE_DIRECTORY "{nsis_escape(other_pack["profile_name"])}"
!define OTHER_PRODUCT_MARKER "{nsis_escape(other_pack["installation_marker"])}"
Var Problem
Var Owned
Var HasContent
Var FixtureDir
Var ReportPath
Var Failures

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
    section = r'''
Section
  SetShellVarContext current
  SetRegView 64
  StrCpy $Failures 0
  System::Call 'kernel32::GetCurrentProcessId() i .r7'
  System::Call 'kernel32::GetTickCount() i .r0'
  StrCpy $FixtureDir "$TEMP\OpenCodeLocal-PathRegression-${PACK_ID}-$7-$0"
  StrCpy $ReportPath "$EXEDIR\REPORT_NAME.ini"
  AUXILIARY_REFERENCES
  CreateDirectory "$FixtureDir\empty"
  CreateDirectory "$FixtureDir\nonempty"
  FileOpen $6 "$FixtureDir\nonempty\keep.txt" w
  FileWrite $6 "Regression fixture."
  FileClose $6
  CreateDirectory "$FixtureDir\owned\wad\support"
  FileOpen $6 "$FixtureDir\owned\${MARKER_PATH}" w
  FileWrite $6 "${PRODUCT_MARKER}$\r$\n"
  FileClose $6
  CreateDirectory "$FixtureDir\other-owned\wad\support"
  FileOpen $6 "$FixtureDir\other-owned\${MARKER_PATH}" w
  FileWrite $6 "${OTHER_PRODUCT_MARKER}$\r$\n"
  FileClose $6

  !insertmacro TestPath "missing_nested" "$FixtureDir\new\nested\OpenCodeLocal" 1
  !insertmacro TestPath "default_location" "$LOCALAPPDATA\Programs\${PACK_INSTALL_DIR}-PathRegression-$7" 1
  !insertmacro TestPath "documents_nested" "$DOCUMENTS\${PACK_INSTALL_DIR}-PathRegression-$7\IA\OpenCodeLocal" 1
  !insertmacro TestPath "existing_empty" "$FixtureDir\empty" 1
  !insertmacro TestPath "existing_empty_trailing_separator" "$FixtureDir\empty\" 1
  ${GetRoot} "$WINDIR" $R7
  !insertmacro TestPath "drive_root" "$R7\" 0
  !insertmacro TestPath "documents_root" "$DOCUMENTS" 0
  !insertmacro TestPath "unowned_nonempty" "$FixtureDir\nonempty" 0
  !insertmacro TestPath "personal_data" "$LOCALAPPDATA\${PROFILE_DIRECTORY}\nested" 0
  !insertmacro TestPath "other_pack_personal_data" "$LOCALAPPDATA\${OTHER_PROFILE_DIRECTORY}\nested" 0
  !insertmacro TestPath "owned_installation" "$FixtureDir\owned" 1
  !insertmacro TestPath "other_pack_installation" "$FixtureDir\other-owned" 0

  Delete "$FixtureDir\nonempty\keep.txt"
  Delete "$FixtureDir\owned\${MARKER_PATH}"
  Delete "$FixtureDir\other-owned\${MARKER_PATH}"
  RMDir "$FixtureDir\owned\wad\support"
  RMDir "$FixtureDir\owned\wad"
  RMDir "$FixtureDir\owned"
  RMDir "$FixtureDir\other-owned\wad\support"
  RMDir "$FixtureDir\other-owned\wad"
  RMDir "$FixtureDir\other-owned"
  RMDir "$FixtureDir\nonempty"
  RMDir "$FixtureDir\empty"
  RMDir "$FixtureDir"
  WriteINIStr "$ReportPath" "summary" "failures" "$Failures"
  SetErrorLevel $Failures
SectionEnd
'''.replace("REPORT_NAME", label).replace("AUXILIARY_REFERENCES", "\n  ".join(
        "GetFunctionAddress $R0 " + name for name in auxiliary))
    return header + functions + "\n" + section


def read_results(path):
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "cp1252"
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(raw.decode(encoding))
    results = []
    for name, expected in CASES.items():
        if not config.has_section(name):
            raise ValueError("Missing runtime case: " + name)
        actual = config.getboolean(name, "allowed")
        results.append({"case": name, "expected_allowed": expected,
                        "actual_allowed": actual, "passed": actual == expected,
                        "input": config.get(name, "input"),
                        "normalized": config.get(name, "normalized"),
                        "problem": config.get(name, "problem")})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "installer/OpenCodeLocal.nsi")
    parser.add_argument("--baseline", type=Path, help="Optional original installer source for comparison")
    parser.add_argument("--pack", choices=("16go", "32go"), action="append",
                        help="Profiles to compile; by default both profiles")
    parser.add_argument("--build-only", action="store_true")
    parser.add_argument("--runtime-blocker", help="Record an observed reason runtime could not run")
    parser.add_argument("--report", type=Path, default=ROOT / "validation/installer-paths.json")
    parser.add_argument("--wine", default="/usr/lib/wine/wine64")
    args = parser.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    profiles = {name: load_pack(name) for name in ("16go", "32go")}
    sources = [("current-" + name, args.source, name) for name in (args.pack or ("16go", "32go"))]
    if args.baseline:
        sources.insert(0, ("baseline-16go", args.baseline, "16go"))
    report = {"scope": "Actual installer path functions with each generated production pack configuration",
              "target": "x86-unicode",
              "runtime": "not_run", "runtime_blocker": args.runtime_blocker,
              "cases": CASES, "artifacts": {},
              "auxiliary_compile_only": ["ValidateDataParts", "CheckDiskSpace"]}
    for label, path, pack_name in sources:
        source = path.read_text(encoding="utf-8")
        pack = profiles[pack_name]
        other_pack = profiles["32go" if pack_name == "16go" else "16go"]
        config = ROOT / "build" / pack_name / "generated" / "pack-profile.nsh"
        nsi = BUILD / (label + ".nsi")
        nsi.write_text(make_harness(source, label, pack, other_pack), encoding="utf-8")
        compiled = subprocess.run(["makensis", "-V2", str(nsi)], capture_output=True, text=True)
        artifact = {"source_sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "pack": pack_name,
                    "pack_config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
                    "model_bytes": pack["model"]["bytes"],
                    "model_parts": len(pack["parts"]),
                    "installation_marker": pack["installation_marker"],
                    "profile_directory": pack["profile_name"],
                    "install_directory": pack["install_dir_name"],
                    "compile_passed": compiled.returncode == 0,
                    "compiler_output": compiled.stdout + compiled.stderr,
                    "harness": nsi.relative_to(ROOT).as_posix(),
                    "executable": nsi.with_suffix(".exe").relative_to(ROOT).as_posix()}
        report["artifacts"][label] = artifact
        if compiled.returncode:
            report["runtime_blocker"] = "NSIS harness compilation failed"
            break
        if args.build_only:
            continue
        exe = nsi.with_suffix(".exe")
        env = os.environ.copy()
        if os.name == "nt":
            command = [str(exe), "/S"]
        else:
            env.update(WINEPREFIX=str(ROOT / "build/wine-installer-test"),
                       WINEARCH="win64", WINEDEBUG="-all")
            command = [args.wine, str(exe), "/S"]
            if shutil.which("xvfb-run"):
                command = ["xvfb-run", "-a", *command]
        try:
            ini = nsi.with_suffix(".ini")
            if ini.exists():
                ini.unlink()  # Never mistake a previous run for fresh execution.
            executed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=45)
            artifact["runtime_output"] = executed.stdout + executed.stderr
            artifact["runtime_returncode"] = executed.returncode
            if not ini.exists():
                report["runtime"] = "blocked"
                report["runtime_blocker"] = artifact["runtime_output"].strip() or "No runtime results produced"
                break
            artifact["results"] = read_results(ini)
            artifact["runtime_passed"] = all(row["passed"] for row in artifact["results"])
            report["runtime"] = "executed"
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            report["runtime"] = "blocked"
            report["runtime_blocker"] = str(exc)
            break
    if args.runtime_blocker and args.build_only:
        report["runtime"] = "blocked"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    current = [value for name, value in report["artifacts"].items() if name.startswith("current-")]
    if args.build_only:
        return 0 if all(a["compile_passed"] for a in report["artifacts"].values()) else 1
    return 0 if current and all(value.get("runtime_passed", False) for value in current) else 1


if __name__ == "__main__":
    sys.exit(main())
