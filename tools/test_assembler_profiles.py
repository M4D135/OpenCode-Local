#!/usr/bin/env python3
"""Validate the real assembler with small pinned two-part and eleven-part models.

The native Linux tests cover the actual streaming and publication code; a
MinGW build also verifies that both production headers compile for Windows.
No production model is downloaded, joined, rewritten, or opened by this test.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/assemble_model.cpp"


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, timeout=30, **kwargs)


def compile_fixture(root: Path, count: int, payload: bytes, cxx: str):
    folder = root / f"fixture-{count}"
    folder.mkdir()
    if count == 2:
        sizes = [11111, len(payload) - 11111]
        names = ["Setup-1.bin", "Setup-2.bin"]
    else:
        # Split the GGUF magic across three parts to exercise boundary reads.
        sizes = [1, 2, 7, 23, 61, 127, 257, 509, 1021, 2047]
        sizes.append(len(payload) - sum(sizes))
        names = [f"Setup-32Go-{i:02d}.bin" for i in range(1, count + 1)]
    model_name = f"fixture-{count}.gguf"
    entries = ",\n".join(f'    {{"{name}", {size}ULL}}' for name, size in zip(names, sizes))
    header = f'''#pragma once
#include <cstddef>
#include <cstdint>
namespace PackConfig {{
constexpr char ModelFile[] = "{model_name}";
constexpr char ModelAlias[] = "fixture-{count}";
constexpr char ModelSha256[] = "{hashlib.sha256(payload).hexdigest()}";
constexpr std::uint64_t ModelBytes = {len(payload)}ULL;
struct Part {{ const char* name; std::uint64_t bytes; }};
constexpr Part Parts[] = {{
{entries}
}};
constexpr std::size_t PartCount = sizeof(Parts) / sizeof(Parts[0]);
}}
'''
    (folder / "pack_config.h").write_text(header, encoding="utf-8")
    # Copy unchanged source so this fixture's header always wins include lookup.
    shutil.copyfile(SOURCE, folder / "assemble_model.cpp")
    exe = folder / "assembler"
    result = run([cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                  str(folder / "assemble_model.cpp"), "-o", str(exe)])
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return {"exe": exe, "names": names, "sizes": sizes, "model": model_name}


def write_parts(source: Path, profile: dict, payload: bytes):
    offset = 0
    for name, size in zip(profile["names"], profile["sizes"]):
        (source / name).write_bytes(payload[offset:offset + size])
        offset += size
    assert offset == len(payload)


def exercise(root: Path, profile: dict, payload: bytes):
    rows = []
    count = len(profile["names"])
    case_root = root / f"cases-{count}"
    case_root.mkdir()
    previous = b"An existing installation must survive a failed replacement."

    def fixture(name, with_parts=True, prior=True):
        directory = case_root / name
        source = directory / "source"
        source.mkdir(parents=True)
        destination = directory / "installed" / profile["model"]
        destination.parent.mkdir()
        if prior:
            destination.write_bytes(previous)
        if with_parts:
            write_parts(source, profile, payload)
        return source, destination

    def check(name, source, destination, expected_code, expected_bytes,
              protected=None, stable_stat=None):
        start = time.monotonic()
        result = run([str(profile["exe"]), str(source), str(destination)])
        problems = []
        if result.returncode != expected_code:
            problems.append(f"exit {result.returncode}, expected {expected_code}")
        if expected_bytes is not None and destination.read_bytes() != expected_bytes:
            problems.append("destination contents changed unexpectedly")
        if protected:
            for path, contents in protected:
                if path.read_bytes() != contents:
                    problems.append("source or protected file changed: " + path.name)
        if stable_stat:
            stat = destination.stat()
            if (stat.st_ino, stat.st_mtime_ns) != stable_stat:
                problems.append("a reusable destination was rewritten")
        if list(destination.parent.glob(destination.name + ".partial.*")):
            problems.append("temporary model file remained after completion")
        rows.append({"parts": count, "case": name, "passed": not problems,
                     "expected_exit": expected_code, "exit": result.returncode,
                     "duration_seconds": round(time.monotonic() - start, 4),
                     "problems": problems, "stdout": result.stdout.strip(),
                     "stderr": result.stderr.strip()})

    source, destination = fixture("split_success", prior=False)
    check("split_success", source, destination, 0, payload)

    source, destination = fixture("replace_existing_after_verification")
    check("replace_existing_after_verification", source, destination, 0, payload)

    source, destination = fixture("missing_last_part")
    (source / profile["names"][-1]).unlink()
    check("missing_last_part", source, destination, 3, previous)

    source, destination = fixture("truncated_last_part")
    last = source / profile["names"][-1]
    last.write_bytes(last.read_bytes()[:-1])
    check("truncated_last_part", source, destination, 3, previous)

    source, destination = fixture("oversized_part")
    last = source / profile["names"][-1]
    last.write_bytes(last.read_bytes() + b"x")
    check("oversized_part", source, destination, 3, previous)

    source, destination = fixture("corrupt_part_preserves_previous")
    last = source / profile["names"][-1]
    corrupted = bytearray(last.read_bytes())
    corrupted[-8] ^= 1
    last.write_bytes(corrupted)
    check("corrupt_part_preserves_previous", source, destination, 7, previous)

    source, destination = fixture("full_gguf_only", with_parts=False)
    (source / profile["model"]).write_bytes(payload)
    check("full_gguf_only", source, destination, 0, payload)

    source, destination = fixture("full_gguf_takes_priority")
    (source / profile["model"]).write_bytes(payload)
    # A valid full model does not depend on the split representation.
    (source / profile["names"][-1]).unlink()
    check("full_gguf_takes_priority", source, destination, 0, payload)

    source, destination = fixture("corrupt_full_does_not_fall_back")
    corrupted = bytearray(payload)
    corrupted[-8] ^= 1
    (source / profile["model"]).write_bytes(corrupted)
    check("corrupt_full_does_not_fall_back", source, destination, 7, previous)

    source, destination = fixture("truncated_full_does_not_fall_back")
    (source / profile["model"]).write_bytes(payload[:-1])
    check("truncated_full_does_not_fall_back", source, destination, 3, previous)

    source, destination = fixture("compressed_source_is_rejected", with_parts=False)
    compressed = io.BytesIO()
    with zipfile.ZipFile(compressed, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(profile["model"], payload)
    disguised = compressed.getvalue().ljust(len(payload), b"\0")
    assert len(disguised) == len(payload)
    (source / profile["model"]).write_bytes(disguised)
    check("compressed_source_is_rejected", source, destination, 7, previous)

    source, destination = fixture("quarantined_suffix_is_not_a_source", with_parts=False)
    (source / (profile["model"] + ".egambit")).write_bytes(payload)
    check("quarantined_suffix_is_not_a_source", source, destination, 3, previous)

    source, unused = fixture("destination_is_last_part", prior=False)
    destination = source / profile["names"][-1]
    original = destination.read_bytes()
    check("destination_is_last_part", source, destination, 4, original)

    source, unused = fixture("destination_is_complete_source", with_parts=False, prior=False)
    destination = source / profile["model"]
    destination.write_bytes(payload)
    check("destination_is_complete_source", source, destination, 4, payload)

    source, destination = fixture("destination_hardlinks_complete_source", with_parts=False, prior=False)
    full = source / profile["model"]
    full.write_bytes(payload)
    destination.hardlink_to(full)
    check("destination_hardlinks_complete_source", source, destination, 4, payload,
          protected=[(full, payload)])

    source, destination = fixture("destination_symlink", prior=False)
    protected = destination.parent / "preserve.txt"
    protected.write_bytes(previous)
    destination.symlink_to(protected)
    check("destination_symlink", source, destination, 4, previous,
          protected=[(protected, previous)])

    source, destination = fixture("source_gguf_symlink", with_parts=False)
    outside = source.parent / "original.gguf"
    outside.write_bytes(payload)
    (source / profile["model"]).symlink_to(outside)
    check("source_gguf_symlink", source, destination, 3, previous,
          protected=[(outside, payload)])

    source, destination = fixture("verified_existing_reuse", with_parts=False)
    destination.write_bytes(payload)
    stat = destination.stat()
    check("verified_existing_reuse", source, destination, 0, payload,
          stable_stat=(stat.st_ino, stat.st_mtime_ns))

    self_test = run([str(profile["exe"]), "--self-test"])
    rows.append({"parts": count, "case": "sha256_self_test", "passed": self_test.returncode == 0,
                 "exit": self_test.returncode, "stdout": self_test.stdout.strip(),
                 "stderr": self_test.stderr.strip()})
    hashed = run([str(profile["exe"]), "--hash-file", str(destination)])
    rows.append({"parts": count, "case": "hash_file_cli", "passed": hashed.returncode == 0 and
                 hashed.stdout.strip() == hashlib.sha256(payload).hexdigest(),
                 "exit": hashed.returncode, "stdout": hashed.stdout.strip(),
                 "stderr": hashed.stderr.strip()})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default="g++")
    parser.add_argument("--windows-cxx", default="x86_64-w64-mingw32-g++")
    parser.add_argument("--work-root", type=Path, default=ROOT / "build/signing",
                        help="Pack build root containing <pack>/generated/pack_config.h")
    parser.add_argument("--report", type=Path, default=ROOT / "validation/assembler-new-profiles.json")
    args = parser.parse_args()
    report = {"source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
              "scope": "Small pinned fixtures using the production assembler source",
              "windows_runtime": "not_run", "native_cases": [], "windows_compilation": []}
    payload = b"GGUF" + struct.pack("<IQQ", 3, 0, 0) + bytes(range(256)) * 1024
    report["fixture_model_bytes"] = len(payload)
    with tempfile.TemporaryDirectory(prefix="opencode-assembler-tests-") as temp:
        folder = Path(temp)
        for count in (2, 11):
            profile = compile_fixture(folder, count, payload, args.cxx)
            report["native_cases"].extend(exercise(folder, profile, payload))
        for pack in ("16go", "32go"):
            header = args.work_root / pack / "generated" / "pack_config.h"
            if not header.is_file():
                report["windows_compilation"].append({"pack": pack, "status": "header_missing"})
                continue
            command = [args.windows_cxx, "-std=c++17", "-O2", "-Wall", "-Wextra", "-Werror",
                       "-static", "-municode", "-I", str(header.parent), str(SOURCE),
                       "-o", str(folder / (pack + "-AssembleModel.exe"))]
            compiled = run(command)
            report["windows_compilation"].append({"pack": pack, "passed": compiled.returncode == 0,
                                                  "compiler_output": compiled.stdout + compiled.stderr})
    report["passed"] = all(row["passed"] for row in report["native_cases"]) and all(
        row.get("passed", False) for row in report["windows_compilation"])
    report["native_passed"] = sum(row["passed"] for row in report["native_cases"])
    report["native_total"] = len(report["native_cases"])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("passed", "native_passed", "native_total",
                                                  "windows_compilation", "windows_runtime")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
