#!/usr/bin/env python3
"""Parse Chrome Stable security posts and maintain v8go triage issues."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import html.parser
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


FEED_URL = (
    "https://chromereleases.googleblog.com/feeds/posts/default/-/"
    "Stable%20updates?alt=json&max-results=50"
)
RELEASES_URL = (
    "https://chromiumdash.appspot.com/fetch_releases"
    "?channel=Stable&platform=Linux&num=100&offset=0"
)
ALLOWED_SOURCE_HOST = "chromereleases.googleblog.com"
ENTRY_ID_RE = re.compile(
    r"^tag:blogger\.com,1999:blog-8982037438137564684\.post-\d+$"
)
CHROME_VERSION_RE = re.compile(r"\b\d{2,3}\.\d+\.\d+\.\d+\b")
CVE_START_RE = re.compile(
    r"\b(Critical|High|Medium|Low)\s+(CVE-\d{4}-\d+)\s*:\s*",
    re.IGNORECASE,
)
EXPLOITED_RE = re.compile(
    r"exploit\s+for\s+(CVE-\d{4}-\d+)\s+exists\s+in\s+the\s+wild",
    re.IGNORECASE,
)
ENGINE_RE = re.compile(r"(?:\bV8\b|\bWebAssembly\b|\bWasm\b)", re.IGNORECASE)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
LOGIN_RE = re.compile(r"^[A-Za-z0-9-]+$")


class FeedError(ValueError):
    """The public feed is unsafe, malformed, or ambiguous."""


@dataclasses.dataclass(frozen=True)
class ChromeCVE:
    identifier: str
    severity: str
    description: str


@dataclasses.dataclass(frozen=True)
class ChromeSecurityNotice:
    entry_id: str
    updated_at: str
    published_at: str
    source_url: str
    title: str
    chrome_versions: tuple[str, ...]
    cves: tuple[ChromeCVE, ...]
    explicitly_engine_related: bool
    review_required: bool
    exploited_cves: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class RenderedIssue:
    title: str
    body: str
    labels: tuple[str, ...]
    fingerprint: str


class _TextExtractor(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"br", "div", "h1", "h2", "h3", "h4", "li", "p", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"div", "h1", "h2", "h3", "h4", "li", "p", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        return " ".join("".join(self.parts).split())


def _mapping(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FeedError(f"{context} must be an object")
    return value


def _field_text(record: dict[str, Any], field: str) -> str:
    wrapper = _mapping(record.get(field), field)
    value = wrapper.get("$t")
    if not isinstance(value, str) or not value.strip():
        raise FeedError(f"{field} must contain text")
    return value.strip()


def _validated_time(value: str, field: str) -> str:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FeedError(f"{field} is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise FeedError(f"{field} must include a timezone")
    return value


def _source_url(record: dict[str, Any]) -> str:
    links = record.get("link")
    if not isinstance(links, list):
        raise FeedError("entry links must be a list")
    matches = []
    for raw_link in links:
        link = _mapping(raw_link, "entry link")
        if link.get("rel") != "alternate":
            continue
        href = link.get("href")
        if not isinstance(href, str):
            raise FeedError("alternate link must be text")
        parsed = urllib.parse.urlparse(href)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_SOURCE_HOST:
            raise FeedError("alternate link is not an official HTTPS Chrome Releases URL")
        if parsed.username or parsed.password or parsed.port:
            raise FeedError("alternate link contains unexpected authority fields")
        matches.append(href)
    if len(matches) != 1:
        raise FeedError("entry must have one official alternate link")
    return matches[0]


def _plain_text(markup: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except html.parser.HTMLParseError as exc:
        raise FeedError("entry HTML is malformed") from exc
    return parser.text()


def _parse_cves(text: str) -> tuple[ChromeCVE, ...]:
    starts = list(CVE_START_RE.finditer(text))
    cves = []
    seen = set()
    for index, match in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        segment = text[match.end() : end]
        description = re.split(r"\bReported\s+by\b", segment, maxsplit=1, flags=re.IGNORECASE)[0]
        description = re.sub(r"\s+\[[^\]]+\]\[[^\]]+\]\s*$", "", description)
        description = " ".join(description.split()).strip(" .")
        identifier = match.group(2).upper()
        if identifier in seen:
            raise FeedError(f"duplicate CVE {identifier} in one entry")
        if not description or len(description) > 500:
            raise FeedError(f"invalid description for {identifier}")
        seen.add(identifier)
        cves.append(
            ChromeCVE(
                identifier=identifier,
                severity=match.group(1).capitalize(),
                description=description,
            )
        )
    return tuple(cves)


def parse_security_feed(payload: Any) -> list[ChromeSecurityNotice]:
    document = _mapping(payload, "feed document")
    feed = _mapping(document.get("feed"), "feed")
    entries = feed.get("entry", [])
    if not isinstance(entries, list):
        raise FeedError("feed entries must be a list")

    notices = []
    seen_entry_ids = set()
    for raw_entry in entries:
        record = _mapping(raw_entry, "feed entry")
        entry_id = _field_text(record, "id")
        if not ENTRY_ID_RE.fullmatch(entry_id):
            raise FeedError("entry ID is not an official Chrome Releases Blogger ID")
        if entry_id in seen_entry_ids:
            raise FeedError(f"duplicate feed entry ID {entry_id}")
        seen_entry_ids.add(entry_id)

        title = _field_text(record, "title")
        folded_title = title.casefold()
        if "stable" not in folded_title or "desktop" not in folded_title:
            continue
        updated_at = _validated_time(_field_text(record, "updated"), "updated")
        published_at = _validated_time(_field_text(record, "published"), "published")
        source_url = _source_url(record)
        markup = _field_text(record, "content")
        text = _plain_text(markup)
        cves = _parse_cves(text)
        has_security_heading = "security fixes" in text.casefold()
        if not cves and not has_security_heading:
            continue

        explicitly_engine_related = any(ENGINE_RE.search(cve.description) for cve in cves)
        severe = any(cve.severity in {"Critical", "High"} for cve in cves)
        review_required = has_security_heading or severe or explicitly_engine_related
        exploited = []
        known_cves = {cve.identifier for cve in cves}
        for match in EXPLOITED_RE.finditer(text):
            identifier = match.group(1).upper()
            if identifier in known_cves and identifier not in exploited:
                exploited.append(identifier)
        versions = tuple(dict.fromkeys(CHROME_VERSION_RE.findall(text)))
        notices.append(
            ChromeSecurityNotice(
                entry_id=entry_id,
                updated_at=updated_at,
                published_at=published_at,
                source_url=source_url,
                title=title[:200],
                chrome_versions=versions,
                cves=cves,
                explicitly_engine_related=explicitly_engine_related,
                review_required=review_required,
                exploited_cves=tuple(exploited),
            )
        )
    return notices


def map_chrome_versions_to_v8(
    release_payload: Any, versions: tuple[str, ...]
) -> dict[str, str]:
    if not isinstance(release_payload, list):
        raise FeedError("Chromium Dash releases must be a list")
    wanted = set(versions)
    result: dict[str, str] = {}
    for raw_release in release_payload:
        release = _mapping(raw_release, "Chromium Dash release")
        if release.get("channel") != "Stable" or release.get("platform") != "Linux":
            continue
        version = release.get("version")
        if version not in wanted:
            continue
        hashes = _mapping(release.get("hashes"), "Chromium Dash release hashes")
        v8_hash = hashes.get("v8")
        if not isinstance(v8_hash, str) or not SHA_RE.fullmatch(v8_hash):
            raise FeedError(f"invalid V8 hash for Chrome {version}")
        previous = result.get(version)
        if previous is not None and previous != v8_hash:
            raise FeedError(f"conflicting V8 hashes for Chrome {version}")
        result[version] = v8_hash
    return result


def _table_text(value: str) -> str:
    return " ".join(value.replace("|", "\\|").split())[:500]


def render_issue(
    notice: ChromeSecurityNotice, version_hashes: dict[str, str]
) -> RenderedIssue:
    labels = ["upstream-security"]
    if notice.review_required:
        labels.append("v8-review-required")
    if notice.explicitly_engine_related:
        labels.append("v8-confirmed-signal")
    if notice.exploited_cves:
        labels.append("exploited-in-wild")

    version_text = ", ".join(notice.chrome_versions) or "version unavailable"
    title = f"[upstream security] Chrome Stable {version_text}: V8 review"
    severity_counts = {
        severity: sum(cve.severity == severity for cve in notice.cves)
        for severity in ("Critical", "High", "Medium", "Low")
    }
    severity_summary = ", ".join(
        f"{severity}: {count}"
        for severity, count in severity_counts.items()
        if count
    ) or "not disclosed"
    relevant_cves = [
        cve
        for cve in notice.cves
        if ENGINE_RE.search(cve.description) or cve.identifier in notice.exploited_cves
    ]
    lines = [
        f"<!-- chrome-release-id:{notice.entry_id} -->",
        f"<!-- chrome-release-updated:{notice.updated_at} -->",
        "",
        "An official public Chrome Stable security announcement requires v8go applicability review.",
        "",
        f"- Source: {notice.source_url}",
        f"- Published: `{notice.published_at}`",
        f"- Updated: `{notice.updated_at}`",
        f"- Explicit V8/WebAssembly signal: `{'yes' if notice.explicitly_engine_related else 'no'}`",
        f"- Exploited in the wild: `{', '.join(notice.exploited_cves) or 'not stated'}`",
        f"- Public CVEs in post: `{len(notice.cves)}` ({severity_summary})",
        "",
        "### Explicit engine or exploitation signals",
        "",
        "| Severity | CVE | Public description |",
        "|---|---|---|",
    ]
    if relevant_cves:
        lines.extend(
            f"| {cve.severity} | {cve.identifier} | {_table_text(cve.description)} |"
            for cve in relevant_cves
        )
    else:
        lines.append("| Unknown | None explicit | Review the official post; component details may remain restricted |")
    lines.extend(["", "### Chrome-to-V8 mapping", ""])
    for version in notice.chrome_versions:
        lines.append(f"- `{version}` → `{version_hashes.get(version, 'unknown')}`")
    if not notice.chrome_versions:
        lines.append("- Chrome version could not be parsed; map manually.")
    lines.extend(
        [
            "",
            "### Maintainer checklist",
            "",
            "- [ ] Confirm whether the public issue affects standalone V8/v8go rather than only browser integration.",
            "- [ ] Establish the upstream fix commit and its ancestry for every affected v8go V8 hash.",
            "- [ ] Publish and verify a fixed v8go release before disclosure metadata names a patched version.",
            "- [ ] Publish a v8go GHSA with the existing CVE, accurate Go module ranges, and patched version.",
            "- [ ] Verify downstream Dependabot alerts/security PRs after GitHub review.",
            "",
            "Do not infer v8go impact solely from Chrome severity. Routine v8go version updates remain the fast patch path while this review is open.",
        ]
    )
    body_without_fingerprint = "\n".join(lines).rstrip() + "\n"
    fingerprint = hashlib.sha256(body_without_fingerprint.encode("utf-8")).hexdigest()
    body = (
        body_without_fingerprint
        + f"\n<!-- chrome-release-fingerprint:{fingerprint} -->\n"
    )
    return RenderedIssue(
        title=title[:250],
        body=body,
        labels=tuple(labels),
        fingerprint=fingerprint,
    )


def _fetch_json(url: str, *, attempts: int = 3, timeout: int = 20) -> Any:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {
        ALLOWED_SOURCE_HOST,
        "chromiumdash.appspot.com",
    }:
        raise FeedError("refusing non-official feed URL")
    request = urllib.request.Request(url, headers={"User-Agent": "stumble-v8go-security-watch/1"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(1 << attempt)
    raise FeedError(f"failed to fetch validated JSON from {url}: {last_error}")


def _run_gh(arguments: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["gh", *arguments],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        detail = " ".join((exc.stderr or "").split())[:500]
        raise FeedError(f"GitHub issue operation failed: {detail or exc}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FeedError(f"GitHub issue operation failed: {exc}") from exc
    return completed.stdout


def _existing_security_issues(repository: str) -> dict[str, dict[str, Any]]:
    raw = _run_gh(
        [
            "api",
            "--method",
            "GET",
            f"repos/{repository}/issues",
            "-f",
            "state=all",
            "-f",
            "labels=upstream-security",
            "-f",
            "per_page=100",
            "--paginate",
            "--slurp",
        ]
    )
    pages = json.loads(raw)
    issues = [issue for page in pages for issue in page]
    result = {}
    for issue in issues:
        body = issue.get("body") or ""
        match = re.search(r"<!-- chrome-release-id:([^>]+) -->", body)
        if match:
            result[match.group(1)] = issue
    return result


def _ensure_labels(repository: str) -> None:
    definitions = {
        "upstream-security": ("b60205", "Official upstream security announcement"),
        "v8-review-required": ("d93f0b", "Standalone V8 impact requires maintainer review"),
        "v8-confirmed-signal": ("d1242f", "Announcement explicitly names V8 or WebAssembly"),
        "exploited-in-wild": ("8b0000", "Official source reports exploitation in the wild"),
    }
    for name, (color, description) in definitions.items():
        _run_gh(
            [
                "label",
                "create",
                name,
                "--repo",
                repository,
                "--color",
                color,
                "--description",
                description,
                "--force",
            ]
        )


def sync_issues(
    notices: list[ChromeSecurityNotice],
    release_payload: Any,
    *,
    repository: str,
    assignee: str,
    dry_run: bool,
    since_days: int,
) -> list[str]:
    if not REPOSITORY_RE.fullmatch(repository):
        raise FeedError("invalid GitHub repository name")
    if not LOGIN_RE.fullmatch(assignee):
        raise FeedError("invalid GitHub assignee")
    if since_days < 1 or since_days > 90:
        raise FeedError("since-days must be between 1 and 90")

    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=since_days)
    recent = []
    for notice in notices:
        updated = dt.datetime.fromisoformat(notice.updated_at.replace("Z", "+00:00"))
        if updated >= cutoff:
            recent.append(notice)
    all_versions = tuple(
        dict.fromkeys(version for notice in recent for version in notice.chrome_versions)
    )
    mappings = map_chrome_versions_to_v8(release_payload, all_versions)
    rendered = [(notice, render_issue(notice, mappings)) for notice in recent]
    if dry_run:
        print(
            json.dumps(
                [
                    {
                        "entry_id": notice.entry_id,
                        "title": issue.title,
                        "labels": issue.labels,
                        "body": issue.body,
                    }
                    for notice, issue in rendered
                ],
                indent=2,
                sort_keys=True,
            )
        )
        return [notice.entry_id for notice, _ in rendered]

    _ensure_labels(repository)
    existing = _existing_security_issues(repository)
    changed = []
    with tempfile.TemporaryDirectory(prefix="v8go-security-") as directory:
        for index, (notice, issue) in enumerate(rendered):
            body_path = os.path.join(directory, f"issue-{index}.md")
            with open(body_path, "w", encoding="utf-8") as body_file:
                body_file.write(issue.body)
            current = existing.get(notice.entry_id)
            label_args = []
            for label in issue.labels:
                label_args.extend(["--add-label", label])
            if current is None:
                create_labels = []
                for label in issue.labels:
                    create_labels.extend(["--label", label])
                _run_gh(
                    [
                        "issue",
                        "create",
                        "--repo",
                        repository,
                        "--title",
                        issue.title,
                        "--body-file",
                        body_path,
                        "--assignee",
                        assignee,
                        *create_labels,
                    ]
                )
                changed.append(notice.entry_id)
                continue

            old_body = current.get("body") or ""
            old_fingerprint = re.search(
                r"<!-- chrome-release-fingerprint:([0-9a-f]{64}) -->", old_body
            )
            if old_fingerprint and old_fingerprint.group(1) == issue.fingerprint:
                continue
            number = str(current["number"])
            _run_gh(
                [
                    "issue",
                    "edit",
                    number,
                    "--repo",
                    repository,
                    "--title",
                    issue.title,
                    "--body-file",
                    body_path,
                    "--add-assignee",
                    assignee,
                    *label_args,
                ]
            )
            if current.get("state") == "closed":
                _run_gh(["issue", "reopen", number, "--repo", repository])
            _run_gh(
                [
                    "issue",
                    "comment",
                    number,
                    "--repo",
                    repository,
                    "--body",
                    f"Official Chrome post updated at `{notice.updated_at}`; triage facts were refreshed.",
                ]
            )
            changed.append(notice.entry_id)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", "Stumble/v8go"))
    parser.add_argument("--assignee", default="Stumble")
    parser.add_argument("--since-days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        notices = parse_security_feed(_fetch_json(FEED_URL))
        changed = sync_issues(
            notices,
            _fetch_json(RELEASES_URL),
            repository=args.repository,
            assignee=args.assignee,
            dry_run=args.dry_run,
            since_days=args.since_days,
        )
        print(f"processed {len(changed)} new or updated Chrome security notice(s)", file=sys.stderr)
        return 0
    except (FeedError, OSError, json.JSONDecodeError) as exc:
        print(f"Chrome security watch failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
