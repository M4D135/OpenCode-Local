#!/usr/bin/env python3
"""Read a GGUF header and optionally exercise llama.cpp's vocabulary-only loader.

This does not load model weights, measure 32-GB performance, or validate a tool
execution round trip.  Use --tokenizer /path/to/llama-tokenize for the safe native
loader check.  Linux child address space is capped at 1536 MiB for that check.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time


FIXED_TYPES = {
    0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f",
    7: "?", 10: "Q", 11: "q", 12: "d",
}


def inspect_header(path):
    with path.open("rb") as stream:
        def scalar(fmt):
            size = struct.calcsize("<" + fmt)
            return struct.unpack("<" + fmt, stream.read(size))[0]

        def string(keep=True):
            size = scalar("Q")
            if keep:
                return stream.read(size).decode("utf-8")
            stream.seek(size, 1)

        def value(kind, keep=True):
            if kind in FIXED_TYPES:
                fmt = FIXED_TYPES[kind]
                if keep:
                    return scalar(fmt)
                stream.seek(struct.calcsize("<" + fmt), 1)
            elif kind == 8:
                return string(keep)
            elif kind == 9:
                subtype, count = scalar("I"), scalar("Q")
                if not keep and subtype in FIXED_TYPES:
                    stream.seek(count * struct.calcsize("<" + FIXED_TYPES[subtype]), 1)
                else:
                    values = [value(subtype, keep) for _ in range(count)]
                    if keep:
                        return values
            else:
                raise ValueError("Unsupported GGUF metadata type: " + str(kind))

        if stream.read(4) != b"GGUF":
            raise ValueError("Not a GGUF file")
        version, tensors, entries = scalar("I"), scalar("Q"), scalar("Q")
        if version not in (2, 3):
            raise ValueError("Unsupported GGUF version: " + str(version))
        result = {"gguf_version": version, "tensor_count": tensors, "metadata_count": entries}
        metadata = {}
        for _ in range(entries):
            key, kind = string(), scalar("I")
            keep = (key.startswith(("general.", "qwen35."))
                    or key == "tokenizer.chat_template" or key.endswith("_token_id"))
            item = value(kind, keep)
            if keep:
                metadata[key] = item
        kinds = {}
        for _ in range(tensors):
            string(False)
            dimensions = scalar("I")
            stream.seek(dimensions * 8, 1)
            kind = str(scalar("I"))
            scalar("Q")
            kinds[kind] = kinds.get(kind, 0) + 1
        result["header_bytes"] = stream.tell()
        result["tensor_type_counts"] = kinds
        template = metadata.pop("tokenizer.chat_template", "")
        result["metadata"] = metadata
        result["chat_template"] = {
            "bytes": len(template.encode()),
            "sha256": hashlib.sha256(template.encode()).hexdigest(),
            "markers": [marker for marker in (
                "<tool_call>", "</tool_call>", "<tool_response>", "tool_calls",
                "tools", "enable_thinking", "reasoning_effort",
            ) if marker in template],
        }
        return result


def check_tokenizer(model, binary):
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = str(binary.parent) + os.pathsep + env.get("LD_LIBRARY_PATH", "")
    command = [str(binary), "-m", str(model), "-p",
               '<|im_start|>user\nRéponds OK. <tool_call>{"name":"test"}</tool_call><|im_end|>',
               "--ids", "--offline"]
    options = {}
    if os.name == "posix":
        import resource

        def limit():
            resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024**2, 1536 * 1024**2))

        options["preexec_fn"] = limit
    started = time.monotonic()
    completed = subprocess.run(command, env=env, capture_output=True, text=True,
                               timeout=30, **options)
    tokens = None
    if completed.returncode == 0:
        tokens = json.loads(completed.stdout)
    return {
        "passed": completed.returncode == 0 and isinstance(tokens, list) and bool(tokens),
        "exit_code": completed.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "token_ids": tokens,
        "stderr": completed.stderr.strip(),
        "loads_weights": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("--tokenizer", type=Path)
    args = parser.parse_args()
    result = {"model": args.model.name, "bytes": args.model.stat().st_size,
              "header": inspect_header(args.model)}
    if args.tokenizer:
        result["tokenizer_check"] = check_tokenizer(args.model.resolve(), args.tokenizer.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.tokenizer and not result["tokenizer_check"]["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
