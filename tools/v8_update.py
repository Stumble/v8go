#!/usr/bin/env python3
"""Discover and classify the V8 tip used by the current Chrome Stable line."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


CHROME_RELEASES_URL = (
    "https://chromiumdash.appspot.com/fetch_releases"
    "?channel=Stable&platform=Linux&num=1&offset=0"
)
CHROME_MILESTONES_URL = "https://chromiumdash.appspot.com/fetch_milestones"
V8_REPOSITORY = "https://chromium.googlesource.com/v8/v8.git"

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CHROME_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.\d+$")
V8_BRANCH_RE = re.compile(r"^\d+\.\d+$")


class DiscoveryError(ValueError):
    """The upstream response is missing or ambiguous, so discovery must stop."""


@dataclasses.dataclass(frozen=True)
class StableV8:
    milestone: int
    chrome_version: str
    branch: str
    commit: str
    version: str | None
    state: str
    quarantine_reason: str | None = None


def _require_mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DiscoveryError(f"{context} must be an object")
    return value


def parse_stable_release(payload: Any) -> tuple[int, str]:
    if not isinstance(payload, list) or len(payload) != 1:
        raise DiscoveryError("expected exactly one Chrome Stable Linux release")
    release = _require_mapping(payload[0], "Chrome release")
    if release.get("channel") != "Stable" or release.get("platform") != "Linux":
        raise DiscoveryError("Chrome release is not Stable Linux")
    milestone = release.get("milestone")
    version = release.get("version")
    if not isinstance(milestone, int) or milestone <= 0:
        raise DiscoveryError("Chrome release milestone must be a positive integer")
    if not isinstance(version, str) or not CHROME_VERSION_RE.fullmatch(version):
        raise DiscoveryError("Chrome release version is invalid")
    return milestone, version


def parse_v8_branch(payload: Any, milestone: int) -> str:
    if not isinstance(payload, list):
        raise DiscoveryError("Chrome milestone response must be a list")
    matches = []
    for raw in payload:
        record = _require_mapping(raw, "Chrome milestone")
        if record.get("milestone") == milestone:
            matches.append(record)
    if len(matches) != 1:
        raise DiscoveryError(f"expected one Chrome milestone {milestone} record")
    branch = matches[0].get("v8_branch")
    if not isinstance(branch, str) or not V8_BRANCH_RE.fullmatch(branch):
        raise DiscoveryError("Chrome milestone V8 branch is invalid")
    return branch


def _parse_remote_lines(output: str, context: str) -> list[tuple[str, str]]:
    refs = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != 2 or not SHA_RE.fullmatch(fields[0]):
            raise DiscoveryError(f"invalid {context} line")
        refs.append((fields[0], fields[1]))
    return refs


def parse_branch_head(output: str, branch: str) -> str:
    expected = f"refs/branch-heads/{branch}"
    matches = [sha for sha, ref in _parse_remote_lines(output, "branch ref") if ref == expected]
    if len(matches) != 1:
        raise DiscoveryError(f"expected one exact {expected} ref")
    return matches[0]


def parse_version_tag(output: str, branch: str, commit: str) -> str | None:
    refs = _parse_remote_lines(output, "tag ref") if output.strip() else []
    direct: dict[str, str] = {}
    peeled: dict[str, str] = {}
    for sha, ref in refs:
        if not ref.startswith("refs/tags/"):
            continue
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            peeled[name[:-3]] = sha
        else:
            direct[name] = sha

    version_re = re.compile(rf"^{re.escape(branch)}\.\d+\.\d+$")
    matches = []
    for name, object_sha in direct.items():
        if name.endswith("-pgo") or not version_re.fullmatch(name):
            continue
        resolved_sha = peeled.get(name, object_sha)
        if resolved_sha == commit:
            matches.append(name)
    matches.sort(key=lambda value: tuple(int(part) for part in value.split(".")))
    if len(matches) > 1:
        raise DiscoveryError("multiple non-PGO V8 version tags point at the Stable tip")
    return matches[0] if matches else None


def parse_quarantines(text: str) -> dict[str, str]:
    quarantines: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or not SHA_RE.fullmatch(fields[0]):
            continue
        quarantines.setdefault(fields[0], fields[1].strip())
    return quarantines


def classify_stable_v8(
    *,
    release_payload: Any,
    milestone_payload: Any,
    branch_ref_output: str,
    tag_ref_output: str,
    current_hash: str,
    bad_hashes_text: str,
) -> StableV8:
    milestone, chrome_version = parse_stable_release(release_payload)
    branch = parse_v8_branch(milestone_payload, milestone)
    commit = parse_branch_head(branch_ref_output, branch)
    version = parse_version_tag(tag_ref_output, branch, commit)

    current_hash = current_hash.strip()
    if not SHA_RE.fullmatch(current_hash):
        raise DiscoveryError("current V8 hash is invalid")
    quarantine_reason = parse_quarantines(bad_hashes_text).get(commit)
    if quarantine_reason is not None:
        state = "quarantined"
    elif commit == current_hash:
        state = "current"
    elif version is None:
        state = "candidate"
    else:
        state = "release"
    return StableV8(
        milestone=milestone,
        chrome_version=chrome_version,
        branch=branch,
        commit=commit,
        version=version,
        state=state,
        quarantine_reason=quarantine_reason,
    )


def _fetch_json(url: str, *, attempts: int = 3, timeout: int = 20) -> Any:
    last_error: Exception | None = None
    request = urllib.request.Request(url, headers={"User-Agent": "stumble-v8go-security-updater/1"})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.url != url:
                    raise DiscoveryError(f"unexpected redirect for {url}")
                return json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1 << attempt)
    raise DiscoveryError(f"failed to fetch validated JSON from {url}: {last_error}")


def _ls_remote(*patterns: str) -> str:
    command = ["git", "ls-remote"]
    if any(pattern.startswith("refs/tags/") for pattern in patterns):
        command.append("--tags")
    command.extend([V8_REPOSITORY, *patterns])
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise DiscoveryError(f"failed to read V8 refs: {exc}") from exc
    return completed.stdout


def discover_live(current_hash_file: Path, bad_hashes_file: Path) -> StableV8:
    release_payload = _fetch_json(CHROME_RELEASES_URL)
    milestone, _ = parse_stable_release(release_payload)
    query = urllib.parse.urlencode({"mstone": milestone})
    milestone_payload = _fetch_json(f"{CHROME_MILESTONES_URL}?{query}")
    branch = parse_v8_branch(milestone_payload, milestone)
    branch_refs = _ls_remote(f"refs/branch-heads/{branch}")
    tag_refs = _ls_remote(f"refs/tags/{branch}.*")
    return classify_stable_v8(
        release_payload=release_payload,
        milestone_payload=milestone_payload,
        branch_ref_output=branch_refs,
        tag_ref_output=tag_refs,
        current_hash=current_hash_file.read_text(encoding="utf-8"),
        bad_hashes_text=bad_hashes_file.read_text(encoding="utf-8"),
    )


def _write_github_output(path: Path, result: StableV8) -> None:
    values = {
        "milestone": str(result.milestone),
        "chrome_version": result.chrome_version,
        "v8_branch": result.branch,
        "v8_hash": result.commit,
        "v8_version": result.version or "",
        "state": result.state,
        "quarantine_reason": result.quarantine_reason or "",
    }
    with path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise DiscoveryError(f"unsafe multiline GitHub output for {key}")
            output.write(f"{key}={value}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-hash-file", type=Path, default=Path("deps/v8_hash"))
    parser.add_argument("--bad-hashes-file", type=Path, default=Path("deps/bad_v8_hashes"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = discover_live(args.current_hash_file, args.bad_hashes_file)
        if args.github_output is not None:
            _write_github_output(args.github_output, result)
        print(json.dumps(dataclasses.asdict(result), sort_keys=True))
        return 0
    except (DiscoveryError, OSError) as exc:
        print(f"v8 discovery failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
