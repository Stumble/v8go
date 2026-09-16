#!/usr/bin/env python3
"""Select the v8go semantic-version increment for a validated V8 release."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


V8_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.(\d+)$")
V8_VERSION_MACROS = (
    "V8_MAJOR_VERSION",
    "V8_MINOR_VERSION",
    "V8_BUILD_NUMBER",
    "V8_PATCH_LEVEL",
)


class ReleaseVersionError(ValueError):
    """The V8 version transition cannot safely select a v8go increment."""


def _parse_v8_version(value: str) -> tuple[int, int, int, int]:
    match = V8_VERSION_RE.fullmatch(value)
    if match is None:
        raise ReleaseVersionError(f"invalid V8 version: {value!r}")
    return tuple(int(part) for part in match.groups())


def parse_v8_header(source: str) -> str:
    values = []
    for macro in V8_VERSION_MACROS:
        matches = re.findall(
            rf"^\s*#define\s+{re.escape(macro)}\s+(\d+)\s*$",
            source,
            flags=re.MULTILINE,
        )
        if len(matches) != 1:
            raise ReleaseVersionError(f"expected exactly one numeric {macro}")
        values.append(int(matches[0]))
    return ".".join(str(value) for value in values)


def release_increment(current: str, target: str) -> str:
    current_parts = _parse_v8_version(current)
    target_parts = _parse_v8_version(target)
    if target_parts <= current_parts:
        raise ReleaseVersionError(
            f"target V8 version {target} must be newer than current {current}"
        )
    if target_parts[:2] == current_parts[:2]:
        return "+0.0.1"
    return "+0.1.0"


def _write_github_output(
    path: Path, *, current: str, target: str, increment: str
) -> None:
    with path.open("a", encoding="utf-8") as output:
        output.write(f"current_v8_version={current}\n")
        output.write(f"target_v8_version={target}\n")
        output.write(f"increment={increment}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--current-header",
        type=Path,
        default=Path("deps/include/v8-version.h"),
    )
    parser.add_argument("--target", required=True)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        current = parse_v8_header(args.current_header.read_text(encoding="utf-8"))
        increment = release_increment(current, args.target)
        if args.github_output is not None:
            _write_github_output(
                args.github_output,
                current=current,
                target=args.target,
                increment=increment,
            )
        print(increment)
        return 0
    except (OSError, ReleaseVersionError) as exc:
        print(f"v8 release version selection failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
