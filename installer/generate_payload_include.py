#!/usr/bin/env python3
"""Generate exact NSIS install/remove entries; the model is in external slices."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path, PurePosixPath

RESERVED = {"con", "prn", "aux", "nul"} | {f"{p}{i}" for p in ("com", "lpt") for i in range(1, 10)}

def escape(value: str) -> str:
    return value.replace("$", "$$").replace('"', '$\\"')

def generate(payload: Path, output: Path) -> dict:
    payload = payload.resolve(strict=True)
    files = []
    seen = set()
    size = 0
    for directory, subdirs, names in os.walk(payload, followlinks=False):
        current = Path(directory)
        for name in subdirs:
            if (current / name).is_symlink():
                raise ValueError(f"Symbolic directory not allowed: {current / name}")
        for name in sorted(names):
            source = current / name
            if source.is_symlink() or not source.is_file():
                raise ValueError(f"Non-regular payload file: {source}")
            path = PurePosixPath(source.relative_to(payload).as_posix())
            if path.parts[:2] == ("wad", "model"):
                continue
            if name.endswith((".part", ".partial", ".tmp", ".download")):
                continue
            if name.startswith(".opencode.exe."):
                continue
            if str(path) in {"wad/support/installation.id", "wad/support/Uninstall.exe"}:
                continue
            if path.parts[0] != "wad" and str(path) != "OpenCodeLocal.exe":
                raise ValueError(f"Only OpenCodeLocal.exe and wad allowed at root: {path}")
            for part in path.parts:
                if part in {".", ".."} or part.endswith((".", " ")) or any(ord(c) < 32 or c in '<>:"\\|?*' for c in part):
                    raise ValueError(f"Invalid Windows path: {path}")
                if part.split(".", 1)[0].casefold() in RESERVED:
                    raise ValueError(f"Reserved Windows filename: {path}")
            folded = str(path).casefold()
            if folded in seen:
                raise ValueError(f"Windows filename collision: {path}")
            seen.add(folded)
            files.append(path)
            size += source.stat().st_size
    mandatory = {"OpenCodeLocal.exe", "wad/engine/llama-server.exe", "wad/engine/llama-completion.exe", "wad/opencode/opencode.exe", "wad/tools/rg.exe"}
    missing = mandatory - {str(p) for p in files}
    if missing:
        raise ValueError(f"Missing required files: {sorted(missing)}")
    files.sort(key=lambda p: (str(p.parent).casefold(), p.name.casefold()))
    lines = ["; Generated; do not edit. Paths are relative to ${PayloadDir}.", f"!define PAYLOAD_NON_MODEL_BYTES {size}", "!macro InstallPayload TARGET"]
    previous = None
    for path in files:
        parent = "" if path.parent == PurePosixPath(".") else "\\" + str(path.parent).replace("/", "\\")
        if parent != previous:
            lines.append(f'  SetOutPath "${{TARGET}}{escape(parent)}"')
            previous = parent
        lines.append(f'  File "${{PayloadDir}}/{escape(str(path))}"')
    lines += ["!macroend", "", "!macro RemovePayload TARGET"]
    removals = set(files) | {PurePosixPath(p) for p in ["wad/model/Qwen3.5-4B-Q4_K_M.gguf", "wad/support/installation.id", "wad/support/Uninstall.exe"]}
    dirs = set()
    for path in sorted(removals, key=lambda p: str(p).casefold()):
        windows_path = str(path).replace("/", "\\")
        lines.append(f'  Delete "${{TARGET}}\\{escape(windows_path)}"')
        dirs.update(p for p in path.parents if p != PurePosixPath("."))
    for path in sorted(dirs, key=lambda p: (-len(p.parts), str(p).casefold())):
        windows_path = str(path).replace("/", "\\")
        lines.append(f'  RMDir "${{TARGET}}\\{escape(windows_path)}"')
    lines += ['  RMDir "${TARGET}"', "!macroend", ""]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return {"files": len(files), "non_model_bytes": size, "output": str(output.resolve()), "model_embedded": False, "recursive_delete": False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(generate(args.payload, args.output), ensure_ascii=False))

if __name__ == "__main__":
    main()
