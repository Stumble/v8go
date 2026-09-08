# fix: harden V8 security update propagation

## 1. Background and Current State

- Problem/outcome: `github.com/stumble/v8go` embeds and publishes native V8 libraries for four supported OS/architecture combinations. It is therefore a security trust root for every service that executes untrusted JavaScript through those modules. The desired outcome is to absorb production-appropriate upstream V8 fixes quickly, publish only verified v8go releases, and cause downstream repositories to receive native Dependabot pull requests without a custom cross-repository updater.
- Current upstream path: `.github/workflows/v8upgrade.yml` runs weekly and reads the V8 hash pinned by the latest Linux Chrome Stable release in Chromium Dash. On a different hash it calls `.github/workflows/v8build.yml`, builds Darwin/Linux on AMD64/ARM64, commits generated libraries and module references directly to the triggering branch, and calls `.github/workflows/release.yml`. Release currently performs test and leakcheck gates, increments v8go by `+0.1.0`, commits the changelog directly, and publishes a GitHub Release.
- Signal gap: V8's documented embedder guidance is to follow the tip of the V8 branch used by Chrome Stable, because fixes continue to be backmerged after an individual Chrome release is cut. On 2026-09-05, v8go `v0.36.0` contains V8 `15.2.124.21` at hash `4323497a...`, while `refs/branch-heads/15.2` points to tagged V8 `15.2.124.24` at hash `9a9eec04...`, six commits ahead. The current Chrome-release-hash query still returns `.21`, so the weekly workflow cannot yet see those Stable-branch backports.
- Validation gap: `test.yml` retries the complete test command after a failure because `TestCPUProfileNode` is known to be sampling-flaky, and the current profiler-fix PR has still failed leakcheck by requiring a sampled child that is not guaranteed to appear. A frequent security release pipeline cannot depend on probabilistic retry as its success criterion.
- Supply-chain gap: the test, leakcheck, and format workflows download and execute `https://apt.llvm.org/llvm.sh` without authenticating the downloaded script. Actions and release helpers are referenced by mutable version tags, the optional FOSSA path executes a remote installer through a shell, and the updater/releaser have direct content-write paths. Repository branch-protection and security-analysis settings could not be verified with the current GitHub token; their current state remains unknown rather than assumed absent.
- Security-intelligence gap: no automation watches the official Chrome Stable security announcements. Stable-tip tracking can deliver code before public disclosure, but it cannot tell maintainers or consumers when Google later identifies a fix as Critical/High, attributes it to V8/WebAssembly, or reports exploitation in the wild. On 2026-09-03, the official Chrome Stable post listed two High V8 CVEs and stated that one was exploited in the wild; the repository had no durable triage event for that announcement.
- Downstream state: `alva-ai/jagent` directly requires v8go `v0.36.0` plus its four generated platform modules and has no Dependabot configuration. Its checks resolve private repositories and submodules using existing Actions credentials, which are not automatically available to Dependabot. `Stumble/v8runner` locally remains on v8go `v0.33.1`; its current Linux CI does not configure the Clang/libc++ contract required by v8go `v0.36.0`. An open hardening PR previously observed for v8runner enables JIT-less execution, adds Linux process isolation, and moves to v8go `v0.35.1`, but the repository became inaccessible to the current GitHub identity during this proposal and the PR state could not be revalidated.
- Related evidence: v8go `v0.36.0` and jagent's v8go `v0.36.0` integration were released on 2026-09-04. The still-open v8go profiler work is PR `Stumble/v8go#13`; the previously observed v8runner hardening work is PR `Stumble/v8runner#2`.
- Runtime evidence: no staging or production execution was required for this repository-governance design. Prior release validation established that jagent can build and run with v8go `v0.36.0`; that evidence does not prove the proposed automation or GitHub settings.
- Verified constraints: production releases track only the current Chrome Stable V8 branch, never V8 main or Canary; untagged Stable-tip changes may be built as candidates but are not public v8go releases; downstream updating uses native GitHub Dependabot rather than a custom updater or shared cross-repository credential; pull requests are not auto-merged or auto-deployed; a GitHub Security Advisory is published only for a confirmed vulnerability with an accurate affected range and fixed version; public Chrome announcement content is untrusted input and cannot directly publish an advisory or obtain release authority.
- Assumptions/unknowns: the v8go public Go module and generated platform modules remain the supported distribution contract; GitHub organization owners can enable the required security features and private-dependency access; the existing v8go SemVer increment policy is preserved by this change unless separately approved; GitHub-hosted Dependabot cron capacity and advisory curation remain external services with no strict completion SLA.

## 2. Problem Model and End-to-End Behavior

- Root cause: the upstream detector observes a Chrome release snapshot instead of the advancing Stable V8 branch; the build/release path mixes detection, direct branch mutation, validation, and publication; no intake converts official Chrome security disclosures into a v8go applicability review; GitHub cannot infer that an upstream V8 vulnerability affects a particular v8go version unless v8go publishes package-level advisory metadata; and downstream repositories have neither scheduled v8go version updates nor enabled security-update mappings.
- Causal chain without change: a Stable backport can remain invisible until the next Chrome refresh and weekly poll, then a flaky or compromised build dependency can delay or corrupt publication, and consumers can remain pinned indefinitely because no native dependency-update job proposes the new version.
- Goals: detect Stable branch movement within a four-hour polling window; build every newly observed Stable tip across all supported platforms; publish only an officially tagged Stable tip that passes deterministic build, test, leak, and integrity gates; surface blocked upgrades rather than silently skipping them; inspect the official Chrome Stable security feed hourly and create deduplicated, assigned triage records; configure native Dependabot to propose all v8go releases downstream without its default cooldown; publish accurate GHSA metadata for confirmed critical fixes; and harden the v8go repository and release path as a high-trust security boundary.
- Non-goals: production publication from V8 main, Canary, Beta, or an untagged commit; reproducing an exploit; claiming that every Stable change has a disclosed CVE; automatically merging or deploying downstream pull requests; introducing a central GitHub App, PAT, `repository_dispatch`, or reusable downstream updater; redesigning v8go's public API or SemVer policy; or replacing process/container isolation around untrusted code.
- **B1 — Stable-tip discovery:** every four hours, v8go resolves the current Chrome Stable milestone to its V8 branch and compares the exact branch-head commit with the last accepted or currently evaluated hash. Repeated observation of the same state is a no-op.
- **B2 — Candidate and release gates:** a new Stable tip starts one four-platform candidate build. An untagged tip remains a candidate. A non-PGO official V8 version tag at that tip may become a public v8go release only after all required deterministic checks and artifact-integrity gates pass.
- **B3 — Routine downstream propagation:** every opted-in downstream Go repository uses native Dependabot version updates to check for `github.com/stumble/v8go*` releases on a four-hour cron, groups the primary module and platform modules into one pull request, and excludes that group from Dependabot's default version cooldown. Existing repository CI evaluates the PR; no merge or deployment is automatic.
- **B4 — Critical downstream propagation:** after a confirmed V8/v8go vulnerability has a published fixed v8go release, maintainers publish a v8go repository security advisory with the Go package identity, accurate vulnerable range, patched version, severity, and upstream CVE/reference when available. Repositories with dependency graph, Dependabot alerts, and security updates enabled then receive GitHub-native alert/security-update behavior in addition to the already-running version-update path.
- **B5 — Security-repository posture:** v8go documents a latest-release support policy and private disclosure route, protects release-critical code and refs, minimizes workflow token permissions, authenticates executable build inputs, pins third-party automation immutably, and makes published releases immutable and attributable where GitHub capabilities permit.
- **B6 — Consumer compatibility:** downstream PRs update the primary v8go module and its matching four platform modules as one coherent dependency set. Each consumer must satisfy the compiler/libc++ contract of the selected v8go release before its PR can pass.
- **B7 — Public security-intelligence intake:** every hour, v8go reads the official Chrome Releases Stable feed and reduces each public security post to its immutable Blogger entry ID, update timestamp, Chrome versions, CVEs, severities, explicit V8/WebAssembly mentions, and in-the-wild statement. One assigned GitHub triage issue per post highlights direct engine signals and conservatively marks Critical/High posts with incomplete component detail for review. Chromium Dash maps the announced Chrome version to its V8 hash when available so maintainers can compare it with released v8go hashes.
- **F1 — Invalid or unavailable upstream metadata:** discovery fails closed, does not replace the accepted hash, and exposes a failed run with enough non-secret context to distinguish network failure, schema drift, missing branch, invalid hash, and missing tag.
- **F2 — Candidate failure:** any platform build, deterministic test, leakcheck, patch application, dependency synchronization, or integrity failure prevents publication and creates or updates one actionable blocked-upgrade record for that V8 hash. Re-observation does not create duplicate builds, issues, or releases.
- **F3 — Known rejected hash:** an explicitly quarantined hash is not released, but its reason remains visible and current Stable movement continues to be polled; a quarantine cannot silently suppress later hashes.
- **F4 — Downstream resolution failure:** if Dependabot cannot resolve a consumer's private dependencies or matching platform modules, its job reports a visible configuration error and no partial dependency PR is treated as successful. Organization/repository Dependabot access or Dependabot secrets are the recovery path.
- **F5 — Advisory delay or mismatch:** routine version updates remain the fast propagation path because GitHub advisory review can take up to 72 hours. An advisory with no fixed version is not published as the trigger for this workflow, and an inaccurate affected range is corrected through the advisory rather than compensated for by custom downstream code.
- **F6 — Security-feed failure or ambiguous applicability:** an unavailable/malformed feed, changed schema, unsafe link, or unparseable security entry fails visibly without executing or interpolating post content. A browser-layer or embargoed Critical/High issue remains `review required`; it does not automatically become a v8go GHSA. An updated post updates the existing triage record rather than creating a duplicate.
- Idempotency/compatibility: hashes, tags, releases, blocked-upgrade records, and grouped downstream PRs are single logical objects per upstream state. Existing consumers may continue using older releases, but the security policy supports only the latest release. This initiative preserves the current public API and release-version policy and does not force an update by bypassing repository review.

## 3. Research, Findings, and Architecture Decision

- Repository evidence: `.github/workflows/v8upgrade.yml`, `v8build.yml`, and `release.yml` already provide most of the expensive build matrix, generated-module synchronization, and post-build tests, so the design extends rather than replaces that machinery. `test.yml`, `leakcheck.yml`, and `fmt.yml` reveal the flaky profiler/retry and unauthenticated LLVM installer prerequisites. jagent's `go.mod`, `.gitmodules`, and GitHub workflows show both the coherent five-module dependency set and the private-access constraint. v8runner's `go.mod` and `go.yml` show its stale dependency and compiler mismatch.
- External research: the [V8 release process](https://v8.dev/docs/release-process) recommends the tip of the branch used by Chrome Stable for embedders. Chromium's [security update guidance](https://chromium.googlesource.com/chromium/src/+/main/docs/security/updates.md) says V8 security fixes are frequent, embedders should absorb every Stable component update, and public release notes should not be used to prioritize which updates matter. The official [Chrome Releases](https://chromereleases.googleblog.com/) site exposes a structured Stable feed and public posts containing CVE, component, severity, and exploitation statements, while warning that details may stay restricted during rollout. GitHub documents native Dependabot [`cron` schedules and a default three-day version cooldown](https://docs.github.com/en/code-security/tutorials/secure-your-dependencies/optimizing-pr-creation-version-updates), [security updates triggered by reviewed package advisories](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependabot-security-updates), [repository advisories and their up-to-72-hour review path](https://docs.github.com/en/code-security/concepts/vulnerability-reporting-and-management/repository-security-advisories), and [Dependabot access to private dependencies](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/manage-your-dependency-security/configure-access-to-private-registries).
- Validated premises: Stable branch movement is the operational security signal and CVE publication is delayed enrichment; faster polling without deterministic/reproducible release gates increases supply-chain risk; the existing Dependabot abstraction solves downstream pull-request ownership with less privilege than a central dispatcher; GHSA is required to map an embedded upstream vulnerability to v8go, but cannot be the sole urgent-delivery mechanism; human review and service isolation remain independent controls.
- Approach A — Minimal polling change: poll the existing Chromium Dash release hash more frequently and add ordinary downstream Dependabot. This is lowest effort and removes custom downstream code, but still misses Stable-branch backports before the next Chrome refresh, retains the direct-write release trust gaps, and provides no package-level security alert. Rejected as insufficient for a security trust root.
- Approach B — Stable-tip, gated release, native two-lane Dependabot: follow the current Stable branch tip, distinguish candidates from tagged releases, harden the v8go release boundary, use frequent Dependabot version updates for every release, and publish GHSA metadata for confirmed vulnerabilities. This reuses GitHub's package graph and existing v8go build system, avoids cross-repository credentials, and stays fast even while advisory curation is pending. Selected.
- Approach C — Push-based central dispatcher: have v8go dispatch update jobs immediately to every consumer through a shared PAT or installed GitHub App. This can reduce the polling delay, but adds cross-repository authority, credential rotation, consumer inventory, webhook/retry state, and a larger compromise blast radius. Rejected until measured Dependabot latency demonstrates a current need.
- **D1 — Upstream channel:** production v8go follows the tip of the V8 branch used by the current Chrome Stable milestone, not the single V8 hash recorded on the latest Chrome release and not a pre-Stable channel.
- **D2 — Detection/publication split:** poll every four hours and build a changed Stable tip once; publish only a non-PGO officially tagged tip after the complete green gate. Untagged tips provide early build evidence without becoming supported releases.
- **D3 — Release trust boundary:** remove unauthenticated executable downloads and mutable third-party action references from release-critical paths; stop treating direct unreviewed branch mutation as the publication boundary; protect and attest release inputs/outputs with GitHub-native controls where available.
- **D4 — Native downstream ownership:** use consumer-owned `dependabot.yml` configuration plus organization/repository Dependabot settings. Group `github.com/stumble/v8go*`, remove its routine cooldown, and do not add a custom downstream update workflow or shared cross-repository token.
- **D5 — Two propagation lanes:** routine version updates deliver every v8go release quickly, including fixes not yet publicly classified; a manually validated v8go GHSA supplies critical severity, affected-range mapping, and Dependabot security alerts/PRs after a fixed release exists.
- **D6 — Human deployment gate:** Dependabot and release automation may create pull requests and releases, but this initiative never auto-merges a consumer PR or promotes it to an environment.
- **D7 — Compatibility and versioning:** update the main and generated platform modules coherently and preserve the existing v8go API and `+0.1.0` automated release-version behavior in this initiative. A different SemVer cadence requires a separate explicit decision.
- **D8 — Repository scope:** v8go owns the primary security architecture record; jagent and v8runner own concise linked consumer records. The open v8runner hardening work remains independently owned and must be reconciled rather than overwritten.
- **D9 — Announcement-to-advisory bridge:** a v8go-owned hourly watcher consumes only the official Chrome Stable feed, creates/updates a public GitHub issue keyed by the feed entry ID, assigns `@Stumble`, and records explicit engine/exploitation signals plus the Chrome-to-V8 hash mapping. A maintainer must confirm standalone-engine impact and a fixed v8go version before manually publishing GHSA metadata; no feed parser or workflow token can publish an advisory.
- **R1 — External curation latency:** GitHub may take up to 72 hours to review a published repository advisory and generate downstream alerts; the frequent version-update lane mitigates but does not eliminate platform latency.
- **R2 — Private dependency access:** jagent Dependabot jobs and CI may fail until organization-level repository access and Dependabot-specific secrets are configured. Those settings require repository/organization authority and cannot be proven by code changes alone.
- **R3 — Stable metadata behavior:** Chromium Dash fields, branch refs, tag timing, or backmerge sequencing can change. Discovery must validate every boundary and fail closed; observing a commit on a Stable branch is not itself proof of a disclosed vulnerability.
- **R4 — Build cost and flakiness:** four native builds are expensive and the profiler test is currently probabilistic. Hash-level concurrency/idempotency and a fundamental deterministic test fix are prerequisites to a reliable four-hour detector.
- **R5 — GitHub security settings:** branch/ruleset protection, private vulnerability reporting, immutable releases, Dependabot settings, and organization access were not readable with the current token. During implementation, the public repository metadata did verify that GitHub Issues are currently disabled, which blocks both durable upgrade state and public security triage. Issues and the remaining desired settings must be applied and verified by an administrator during rollout.
- **R6 — v8runner state:** the current session cannot fetch or query `Stumble/v8runner` even though the local clone and an earlier PR observation exist. Consumer publication must wait for repository access to be restored and must reconcile open PR #2 before changing its dependency baseline.
- **R7 — Advisory accuracy:** upstream Chromium may initially withhold exploit details, and not every Chrome CVE applies to standalone V8 embedding. Maintainers must not publish speculative package advisories; rapid routine updates cover the uncertainty until impact is confirmed.
- **R8 — Public-feed completeness:** the Blogger feed is an official public disclosure source, not an advance-warning contract; posts can be delayed, edited, omit restricted components, or change formatting. Stable-tip delivery remains primary, and the watcher must reprocess updates, retain source links, and degrade to human review rather than infer applicability.
- Closest implementation/test/E2E/contract references: v8go `.github/workflows/v8upgrade.yml`, `v8build.yml`, `release.yml`, `test.yml`, `leakcheck.yml`, `fmt.yml`, `deps/v8_hash`, and `deps/bad_v8_hashes`; v8go `TestCPUProfileNode` and PR #13; jagent `go.mod`, `.gitmodules`, `AGENTS.md`, and `.github/workflows/{check,go-test,golangci-lint,nightly-e2e}.yml`; v8runner `go.mod`, `.github/workflows/go.yml`, and previously observed PR #2.

## 4. Implementation Design

### Change and ownership map

| Repository/location | Responsibility |
|---|---|
| `Stumble/v8go/tools/v8_update.py` and `tools/test_v8_update.py` | Own typed parsing, validation, and classification of Chrome Stable milestone metadata, V8 branch refs/tags, the accepted hash, and quarantine state. Network/process adapters remain thin and injectable so all decisions are unit-testable without the network. |
| `Stumble/v8go/tools/chrome_security.py` and `tools/test_chrome_security.py` | Own safe parsing and classification of the official Chrome Releases Stable JSON feed. They emit concise notice data; they never execute feed content, mutate GitHub, or decide v8go affected ranges. |
| `Stumble/v8go/.github/workflows/v8upgrade.yml` | Poll every four hours, invoke discovery, suppress already-validated state, orchestrate candidate build/validation, and report one hash-keyed tracking issue. It grants no write permission to discovery/build/test jobs. |
| `Stumble/v8go/.github/workflows/v8build.yml` | Read-only build of exact source commits for Darwin/Linux AMD64/ARM64 with depot_tools self-update disabled; assemble, checksum, test, and leakcheck the candidate. It contains no write-permission job, so fork pull requests can invoke it safely. |
| `Stumble/v8go/.github/workflows/v8stage.yml` | After the master publication path's read-only candidate succeeds, download the same artifacts, revalidate the official tag/checksums, create the exact automation branch commits, and return the staged commit/base/version contract with repository-local contents write. |
| `Stumble/v8go/.github/workflows/release.yml` | Validate the exact staged commit, verify the expected base has not moved, fast-forward `master` without force, and create one release/tag at that exact commit after the administrator immutable-release preflight. It is the only V8 publication path with `contents: write` to `master`. |
| `Stumble/v8go/.github/workflows/chrome-security-watch.yml` | Poll the official Stable security feed hourly, render issue bodies through temporary files, and create/update assigned triage issues with `issues: write`. It receives no contents-write or repository-advisory permission. |
| `Stumble/v8go/.github/actions/setup-clang-21/action.yml` | Reuse the signed LLVM APT-key/fingerprint installation already proven in jagent and the existing Homebrew LLVM 21 path on macOS. |
| `Stumble/v8go/.github/workflows/{fmt,test,leakcheck}.yml` and other action references | Consume the local compiler setup, remove whole-suite retry, and pin every external action to an immutable commit SHA with a version comment. Replace release/commit helper actions with explicit `git`/`gh` commands; keep FOSSA separate from release gating and pin both its action and CLI version if retained. |
| `Stumble/v8go/cpuprofilenode_test.go` and shared profiler fixture | Replace sibling-sampling assertions with one sustained call stack that still exercises child, parent, function name, source, line, and column accessors. The consolidated security PR supersedes rather than builds on the still-flaky PR #13 branch. |
| `Stumble/v8go/SECURITY.md`, `.github/CODEOWNERS`, `.github/dependabot.yml`, and security-release documentation | Declare latest-release support, GitHub Private Vulnerability Reporting, `@Stumble` ownership of release-critical paths, pinned-action maintenance, the announcement-to-GHSA runbook, and required GitHub settings. |
| `alva-ai/jagent` and `Stumble/v8runner` | Own consumer-side Dependabot/CI changes in their linked local records. No v8go workflow owns downstream credentials or writes their repositories. |

### Stable discovery contract

The pure decision boundary is represented approximately as:

```python
@dataclass(frozen=True)
class StableV8:
    milestone: int
    branch: str
    commit: str
    version: str | None
    state: Literal["current", "quarantined", "candidate", "release"]
    quarantine_reason: str | None = None
```

`discover_stable_v8` accepts decoded Chrome release/milestone documents plus exact `git ls-remote` output. It requires one Linux Stable release with an integer milestone, one matching milestone record with a `N.N` V8 branch, one exact `refs/branch-heads/<branch>` SHA, and zero or one non-PGO semantic V8 tag pointing at that SHA. It compares the result with `deps/v8_hash` and `deps/bad_v8_hashes`; any ambiguity is an error rather than a best-effort selection. The CLI adapter fetches only fixed HTTPS Chromium/Google origins with bounded retries/timeouts and writes validated scalar outputs to `$GITHUB_OUTPUT`.

State transitions are:

```text
same released hash                         -> current -> no build
hash listed in deps/bad_v8_hashes          -> quarantined -> visible reason, no build
new branch tip without one official tag    -> candidate -> build/test, never publish
new branch tip with one official tag       -> release -> build/stage/test/promote/tag
```

One `v8-upgrade` issue is keyed by the full V8 hash in a hidden body marker. A successful untagged candidate records `candidate-passed` so the same unmodified hash is skipped until its tag state changes. A failed attempt updates the same issue and retries on the next schedule unless quarantined. When a newer tip supersedes an untagged candidate, the older issue is closed as superseded. A completed release closes its issue. Discovery failures without a trustworthy hash use one singleton automation-health issue.

### Candidate, staging, and publication algorithm

1. Capture `github.sha` on `master` as `expected_base_sha`; the workflow-level concurrency group prevents overlapping upgrade runs.
2. Build common headers and the four native variants from the validated V8 commit. Restore caches only on an exact key containing V8 hash, build-script hashes, OS, and architecture; do not use cross-hash prefix restore keys. Pin macOS Python build requirements to exact versions/hashes.
3. Each build artifact carries a sorted SHA-256 manifest. Assembly verifies manifests before extracting and uploads one candidate tree. Artifact names include the full V8 hash so outputs from different attempts cannot mix.
4. Run the normal Go workspace tests against the assembled candidate. Untagged candidates stop here after updating their tracking issue.
5. For a tagged candidate, create `automation/v8-<short-hash>` from `expected_base_sha`. Commit generated libraries/includes, V8/depot_tools refs, `deps/v8_hash`, cgo flags, and the unreleased V8 entry with plain Git. Push that staging branch so generated platform-module commits are addressable, update the main `go.mod`/`go.sum` to their exact pseudo-versions, convert the changelog with the existing `+0.1.0` policy, and commit the complete release state.
6. Invoke `test.yml` and `leakcheck.yml` with the exact final staging SHA. These checks run again after candidate-tree validation because module synchronization and release metadata are now part of the commit that consumers will resolve.
7. The promotion job fetches `origin/master`, requires it to equal `expected_base_sha`, requires the candidate SHA to descend from that base, verifies the staged hash/version again, and performs a normal fast-forward push of the tested SHA to `master`. It never force-pushes.
8. After the administrator has verified the GitHub immutable-release setting, require that the target tag/release does not point elsewhere and create the GitHub release with `gh release create` at the tested SHA. The workflow token is not given repository-administration access merely to inspect that setting. If master promotion succeeded but release creation failed, the next run recognizes the release heading/current hash without a matching release and resumes only the idempotent tag/release step.
9. Delete the staging branch only after release success. On failure it remains available for bounded diagnosis and is replaced only by the same hash's retry logic.

The staged branch is an internal transaction boundary, not a downstream PR. Human review protects changes to the trusted workflow itself; native blobs are promoted automatically only after the exact staged commit is green. This satisfies the approved fast-release boundary without granting a feed parser or build matrix a direct unvalidated write to `master`.

### Chrome security-intelligence contract

The parser returns concise public facts rather than copied article bodies:

```python
@dataclass(frozen=True)
class ChromeSecurityNotice:
    entry_id: str
    updated_at: str
    source_url: str
    chrome_versions: tuple[str, ...]
    cves: tuple[ChromeCVE, ...]
    explicitly_engine_related: bool
    review_required: bool
    exploited_cves: tuple[str, ...]
```

Only the official `chromereleases.googleblog.com` Stable JSON feed and HTTPS alternate links on that host are accepted. The standard-library HTML parser extracts text; title/body/CVE text is length-bounded, written to files, and never interpolated into shell commands. Direct engine classification requires an explicit V8/WebAssembly component in the public CVE description. Every Critical/High Desktop Stable security post is retained for review even when details are embargoed. Known Chrome versions are matched against bounded Chromium Dash Stable results to add V8 hashes; a missing mapping is recorded as unknown, not guessed.

The workflow pages all existing `upstream-security` issues and matches a hidden Blogger entry-ID marker locally rather than relying on eventually consistent GitHub search. A new entry creates one public issue assigned to `@Stumble`; an updated timestamp/content fingerprint updates the body and adds a comment so assignees are notified. Labels distinguish explicit engine signal, review required, and exploitation in the wild. All copied facts link to the official post.

The issue checklist requires a maintainer to establish fix-commit/version ancestry across v8go release tags, publish the fixed v8go release first, and then create a repository GHSA using the existing upstream CVE plus affected/patched ranges for the primary Go module and relevant platform modules. The watcher has no `security-advisories` permission and cannot automate this judgment.

### Errors, security, and observability

| Boundary/failure | Handling | Operator-visible result |
|---|---|---|
| Chromium/Chrome timeout, invalid JSON, schema/ref/tag ambiguity | Bounded retry, then fail closed without changing accepted state | Failed job summary plus singleton automation-health issue on default-branch runs |
| Quarantined hash | Skip build but continue polling later tips | Hash/reason in summary and hash-keyed issue |
| Build, checksum, test, leak, patch, or module-sync failure | No master push/tag/release; preserve diagnostic branch/artifacts for bounded retention; retry same issue next poll | Failed checks and updated issue with run URL/stage, no secrets |
| Master advances during build | Reject non-fast-forward promotion | Tracking issue says stale base; next poll rebuilds from new trusted base |
| Tag/release conflict | Refuse publication; never update an existing release | Release-stage failure and actionable issue |
| Immutable releases not enabled | Administrator preflight blocks activation of default-branch publication; do not add administration permission to the workflow | Unmet rollout checklist item |
| Feed unavailable/malformed or unsafe link | No incident classification; do not execute content | Watcher failure/health issue |
| Chrome issue is browser-only or details are restricted | Keep `review required`; no GHSA | Public triage issue with source and unknowns |
| GitHub advisory curation delay | No custom bypass | Routine Dependabot version PR remains the earlier repair lane; GHSA supplies later formal alert |

Permissions remain job-scoped: discovery/build/test use `contents: read`; candidate issue reporting uses `issues: write`; staging/promotion/release alone use `contents: write`; the announcement watcher uses `contents: read` and `issues: write`; attestations come from immutable GitHub Releases. No workflow receives `security-advisories: write`, a downstream PAT, or production credentials. Logs and issues contain public version/CVE/hash/run metadata only.

### Compatibility, rollout, recovery, and documentation

- No database, API, JavaScript runtime surface, Kubernetes, staging, or production schema/config migration is involved. A later Dependabot merge is the normal consumer rollout boundary.
- First land the deterministic profiler/compiler/action hardening and the new discovery/watcher logic with publication disabled on non-default refs. A real topic-branch candidate run proves the matrix without changing `master` or issues. Only after the GitHub settings preflight is satisfied does the default-branch schedule gain automatic promotion.
- An administrator enables GitHub Issues, Private Vulnerability Reporting, immutable releases, dependency graph/alerts/security updates, and a ruleset that protects `master`, tags, workflow files, and release-critical paths while allowing only the final v8go automation identity to fast-forward the tested commit. `@Stumble` is CODEOWNER and the documented private-report recipient. Default-branch publication fails closed while Issues remain disabled; topic-branch dry runs remain available.
- Forward recovery is preferred: retry discovery/build, resume a missing release for an already-promoted exact SHA, or quarantine a broken upstream hash with a reason. Published immutable releases and tags are never rewritten; a bad published version receives a newer fixed release and, when applicable, an advisory update.
- `SECURITY.md` is authoritative for supported versions/reporting. A security-release runbook owns Chrome triage, ancestry evidence, GHSA fields, downstream verification, and rollback/forward-fix policy. The main README links the policy; workflow comments document trusted inputs and permission boundaries.

### Serial Implementation Checklist

- [x] Add table-driven offline tests and pure implementations for Stable V8 discovery/state classification and Chrome Stable security-notice parsing, including hostile/malformed fixtures and Chrome-to-V8 mapping behavior (B1, B7, F1, F3, F6; test-first).
- [x] Replace the probabilistic profiler sibling test with one sustained stack and prove it repeatedly; remove the whole-suite retry only after the focused regression is stable (B2, F2; test-first).
- [x] Add the signed local Clang 21 setup, exact Python build inputs, external-action SHA pins, exact-only build caches, and artifact checksum/assembly checks (B2, B5, F2; tests alongside plus workflow structural checks).
- [x] Refactor V8 upgrade/build/release into candidate, staged exact commit, repeated validation, fast-forward promotion, immutable release, retry/recovery, and hash-keyed issue states with least-privilege jobs (B1, B2, F1–F3; tests alongside plus GitHub candidate E2E).
- [x] Add the hourly Chrome security watcher, safe issue upsert/rendering, assignment/labels, Chromium Dash mapping, and manual GHSA checklist with no advisory permission (B4, B7, F5, F6; test-first plus dry-run E2E).
- [ ] Add `SECURITY.md`, CODEOWNERS, v8go Actions Dependabot, security-release/settings documentation, and changelog/README links; verify all required GitHub administrator settings before enabling default-branch publication (B5, D3, R5).
- [ ] Add and validate jagent's grouped four-hour v8go Dependabot configuration, then document/verify the organization private-dependency and Dependabot-secret prerequisites without changing runtime code (B3, B4, B6, F4).
- [ ] Prepare v8runner's compatible Clang/Go CI and grouped Dependabot configuration locally; after access returns, re-fetch and reconcile PR #2 before testing or publication (B3, B4, B6, F4, R6).
- [ ] Run local/focused/full verification, inspect final diffs, and execute real topic-branch V8 candidate and Chrome watcher dry runs proving no default-branch, release, issue, downstream-merge, staging, or production side effects.

## 5. Verification and E2E Design

### Testability and representative tests

Pure parsers receive fixtures/adapters rather than opening the network in tests. Representative cases include:

```python
def test_tagged_stable_tip_is_releasable(self):
    result = classify_stable_v8(
        release=stable_release(milestone=152),
        milestone=milestone(v8_branch="15.2"),
        branch_refs="9a9eec... refs/branch-heads/15.2\n",
        tag_refs="9a9eec... refs/tags/15.2.124.24\n",
        current_hash="432349...",
        quarantined={},
    )
    self.assertEqual("release", result.state)
    self.assertEqual("15.2.124.24", result.version)

def test_browser_only_high_cve_requires_review_not_engine_confirmation(self):
    notice = parse_notice(stable_post_with_high_cve(component="WebGL"))
    self.assertTrue(notice.review_required)
    self.assertFalse(notice.explicitly_engine_related)
```

The first test fails if implementation falls back to Chrome's pinned V8 hash, accepts a tag for another commit, or ignores the official branch head. Additional cases independently assert malformed/empty documents, wrong milestone, invalid SHA/branch, PGO-only tag, multiple conflicting tags, same hash, quarantine reason, unsafe feed URL, HTML/script content as inert text, edited entry identity, direct V8/WebAssembly classification, exploit-in-wild association, and missing Dash mapping.

`TestCPUProfileNode` uses a dedicated script with one sustained `start -> foo -> delay -> loop` stack. It locates the sampled leaf and walks its ancestors, asserting the wrapper's parent/child and metadata contract without requiring mutually exclusive sibling paths to all receive samples. Repeated focused execution is the flake regression; existing complete tests remain the functional boundary.

Workflow structure is checked with a pinned `actionlint`; Python syntax/unit tests and `git diff --check` run locally and in CI. Candidate SHA-256 manifests are deliberately corrupted in a small script-level fixture/check to prove assembly rejects mismatched artifacts before native tests.

### E2E Required: no — Alva service E2E is outside this change

This change does not alter an Alva API, service call, runtime behavior, database, or deployed environment, so the `aldev:e2e` local service stack is not a relevant release gate. Hosted integration validation is still mandatory because the changed contract spans live Chromium endpoints, four GitHub-hosted runner platforms, GitHub artifacts, staged Git refs, reusable workflows, repository settings, releases, and Issues; local/unit tests cannot prove those integrations.

Before merge, the relevant pull request automatically invokes the read-only candidate path in `v8upgrade.yml`. It must discover the then-current Stable tip (observed as `15.2.124.26` during implementation on 2026-09-07), build all four variants, validate the candidate, and stop because the ref is not `master`. Postconditions: no write-scoped build call, master movement, staging branch, v8go tag/release, or tracking issue; only bounded workflow artifacts/caches. The Chrome watcher also runs as a pull-request dry run when its workflow/parser changes; it must parse the 2026-09-03 Stable post, identify both public V8 CVEs and the in-the-wild signal, render the intended issue preview, and create no issue.

After merge and administrator preflight, one default-branch manual upgrade run is the controlled production test of repository automation. It may update/release v8go but does not touch jagent, v8runner, staging, or production. Consumer Dependabot jobs are verified only after their configuration reaches each default branch; success is one grouped PR, and failure remains visible without merge.

### Exact verification commands

From v8go:

```bash
python3 -m unittest discover -s tools -p 'test_*.py'
CC=clang-21 CXX=clang++-21 CGO_CXXFLAGS=-nostdinc++ go test -run '^TestCPUProfileNode$' -count=20 .
CC=clang-21 CXX=clang++-21 CGO_CXXFLAGS=-nostdinc++ go test -count=1 ./...
CC=clang-21 CXX=clang++-21 CGO_CXXFLAGS=-nostdinc++ go test -c --tags leakcheck
./v8go.test
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
git diff --check
```

External action SHAs are resolved and recorded during implementation rather than using moving tags; actionlint is fixed at `v1.7.12`. v8go defines no `make lint-fix`; actionlint and language-native checks are recorded as the available scoped lint paths.

From jagent, with its documented Clang/private-module environment and initialized submodules:

```bash
make lint-fix
make build
git diff --check
```

From v8runner, only after remote/PR reconciliation:

```bash
CC=clang-21 CXX=clang++-21 CGO_CXXFLAGS=-nostdinc++ make lint-fix
CC=clang-21 CXX=clang++-21 CGO_CXXFLAGS=-nostdinc++ make build
CC=clang-21 CXX=clang++-21 CGO_CXXFLAGS=-nostdinc++ make test
git diff --check
```

| Behavior/failure | Evidence |
|---|---|
| B1, F1, F3 | Offline discovery table tests plus topic-branch live discovery summary |
| B2, F2 | SHA-manifest negative check, four-platform topic candidate run, focused repeated profiler test, complete Go test/leakcheck |
| B3, B6, F4 | Reviewed Dependabot YAML, consumer compiler/build checks, then one real grouped Dependabot PR/job per accessible downstream |
| B4, F5 | Security runbook field review; after a real confirmed advisory, GitHub advisory/Dependabot alert evidence is operational postflight, not fabricated test data |
| B5 | Pinned-reference scan, permissions review, signed LLVM key check, immutable-release/ruleset/private-reporting administrator postflight |
| B7, F6 | Feed parser tests plus watcher dry run against the official feed with zero issue mutations |

Intentionally excluded: no exploit is reproduced; no synthetic GHSA is published because it would pollute the public advisory ecosystem; no downstream PR is auto-merged; no staging/production service E2E is run because these changes do not alter a deployed runtime until a separately reviewed dependency PR is merged and released.

## 6. Human Decisions and Interaction

- The user identified v8go as a high-trust security repository after a real server compromise investigation and requested rapid upstream Stable absorption plus forced downstream update proposals.
- The user approved Stable branch tip rather than main/Canary, four-hour build polling, tagged-only public releases, native Dependabot rather than downstream custom workflows, routine version updates plus confirmed GHSA security updates, and no automatic downstream merge/deployment.
- The user approved automatic upstream v8go publication after the exact staged commit passes complete gates; there is no human merge pause for native V8 refreshes. Human review instead protects changes to the publication workflow and performs advisory applicability judgment.
- The user approved GitHub Private Vulnerability Reporting as the documented private channel and `@Stumble` as CODEOWNER/notification owner, without inventing a security email address.
- The user approved preparing v8runner locally but withholding publication until repository access returns and the independent hardening PR #2 is freshly reconciled.
- The user identified the missing Chrome-security-notification path and approved an hourly official-feed watcher that creates assigned triage issues, followed by human confirmation, v8go GHSA publication, and GitHub-native downstream Dependabot security alerts/PRs.
- GitHub administrator actions remain explicit operational prerequisites. During review, Issues, vulnerability alerts/Dependabot security updates, Private Vulnerability Reporting, and immutable releases were enabled in `Stumble/v8go` and read back successfully. Protected-branch/tag/workflow rulesets and repository-wide Actions SHA enforcement wait until the new workflow is merged so the old default-branch automation is not locked out. Consumer private-dependency access and Dependabot secret context remain separate owner work.
- Implementation evidence before review: `python3 -m unittest discover -s tools -p 'test_*.py'` passed 16 tests; live `tools/v8_update.py` resolved Stable `15.2.124.26` at `ba3bbc83...`; a live watcher dry run produced four recent concise notices and flagged the 2026-09-03 in-the-wild V8 event without mutating Issues; `actionlint v1.7.12`, 20 consecutive profiler runs, the complete Go suite, and the leakcheck binary passed. The real four-platform topic-branch workflow runs remain for review.

## 7. Outcome and Evidence

- Result: the v8go security automation, repository policy, Stable discovery, staged release path, Chrome security watcher, deterministic profiler regression, native-runner validation matrix, and downstream configurations are implemented locally. Review is not complete because the required hosted workflow validation has not run; nothing has been pushed yet.
- Review fixes: the complete diff review corrected ancestor-tag release recovery, macOS checksum generation, stale native archive cleanup, propagation of generated platform linker metadata, bounded/untrusted feed rendering, namespaced/assigned issue state, exact build-input cache identity, successful-only cache publication, and disabled depot_tools self-update. Relevant parser/actionlint checks were rerun after every product/config fix.
- Hosted-CI fixes: the first current-head PR run proved that V8 `15.2.124.27` requires depot_tools support for `dep_type: gcs`, so the pinned submodule moved from `5e5802d7...` to upstream `ed9c87f6...` while self-update remains disabled. The clean leakcheck runner also proved `lsan_interface.h` belongs to `libclang-rt-21-dev`, which the authenticated Clang setup now installs explicitly.

| ID | Implementation/evidence | Status |
|---|---|---|
| B1 | `tools/v8_update.py` plus four-hour discovery workflow; 17 offline tests and live resolution of `15.2.124.26`/`ba3bbc83...` | DONE |
| B2 | Four-target build, exact checksums, candidate overlays, staged commit, repeated test/leakcheck, promotion/recovery workflow | PARTIAL — local/static checks pass; hosted four-platform candidate is pending branch publication |
| B3 | Native four-hour grouped Dependabot files in jagent/v8runner with cooldown exclusion | PARTIAL — repository files pass structural checks; GitHub jobs require default-branch publication |
| B4 | GHSA policy/runbook and separate security-update groups | PARTIAL — no synthetic advisory; requires a real confirmed vulnerability and GitHub review |
| B5 | SECURITY, CODEOWNERS, signed LLVM setup, immutable action refs, pinned Python input, permission-scoped workflows, immutable-release runbook | PARTIAL — Issues, alerts/security updates, private reporting, and immutable releases are enabled; ruleset/SHA enforcement awaits merge |
| B6 | Main/platform modules grouped; jagent builds on `v0.36.0`; v8runner passes on both `v0.33.1` and a temporary coherent `v0.36.0` set | DONE locally |
| B7 | Hourly watcher, safe parser/renderer/upsert, assignee/labels, Dash mapping; live dry run identified current V8/in-the-wild notices | DONE locally; hosted workflow dry run pending |
| F1 | Strict schema/ref/tag/size validation, bounded retries, fail-closed parser tests, automation-health path | DONE locally |
| F2 | Read-only build jobs, SHA manifests, no cross-hash cache restore, exact staged revalidation, blocked issue state | PARTIAL — hosted failure/promotion behavior pending E2E |
| F3 | Quarantine parser/state/reason tests and visible workflow branch | DONE locally |
| F4 | Dependabot failures remain visible; private access/secrets documented | UNVERIFIABLE until organization settings/default-branch job |
| F5 | Routine update lane remains independent; advisory requires fixed version; 72-hour risk documented | DONE as design/config; real curation is external |
| F6 | Feed origin/size/schema/identity validation, inert HTML, escaped Markdown/mentions, update fingerprint, no advisory token | DONE locally |
| D1–D2 | Stable branch tip and candidate/tag split implemented exactly | DONE locally |
| D3 | Trusted build inputs, staged exact SHA, least privilege, checksums, no master force push | PARTIAL — GitHub ruleset/immutable setting and hosted flow pending |
| D4–D6 | Consumer-native Dependabot, two lanes, human-only consumer merge/deploy | DONE in repository configuration; external execution pending |
| D7 | Existing API and `+0.1.0` automation retained; dry changelog render produced `v0.37.0` | DONE |
| D8 | Primary/local records exist; v8runner access is restored and closed/unmerged PR #2 has no file overlap with this automation branch | DONE |
| D9 | Official-feed issue bridge implemented with human-only GHSA decision | DONE locally |
| R1 | GitHub advisory review latency remains external; routine lane mitigates | ACCEPTED/UNVERIFIABLE |
| R2 | jagent private dependency/Dependabot secret access remains administrator work | OPEN |
| R3 | Strict upstream validation and fail-closed behavior implemented | MITIGATED locally |
| R4 | Profiler passed 20 consecutive runs; exact cache/concurrency implemented; hosted native build cost remains | PARTIAL |
| R5 | Issues, alerts/security updates, private reporting, and immutable releases were enabled/read back with admin access; ruleset/SHA enforcement remains deferred until merge | PARTIAL/MITIGATED |
| R6 | v8runner access is restored; PR #2 is closed/unmerged and does not overlap the prepared CI/Dependabot files | MITIGATED |
| R7 | Human applicability/GHSA gate retained; no speculative advisory created | MITIGATED |
| R8 | Official feed is polled/reprocessed with source links and conservative review classification | MITIGATED; feed SLA remains external |

- Final local verification completed before the E2E blocker:

  - v8go: `python3 -m unittest discover -s tools -p 'test_*.py'` — 17 passed; `python3 -m py_compile tools/v8_update.py tools/chrome_security.py` — passed; `go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12` — passed; live `python3 tools/v8_update.py` — Stable `15.2.124.26`; live `python3 tools/chrome_security.py --dry-run --since-days 14` — four notices, no mutation; focused profiler `-count=20`, complete `go test -count=1 ./...`, and leakcheck executable — passed.
  - jagent: YAML parse, `make lint-fix` (zero issues/no diff), and `make build` with Clang 21/`-nostdinc++` — passed.
  - v8runner: YAML/actionlint and Clang 21 lint/build/test — passed on current `v0.33.1`; a temporary detached worktree upgraded v8go plus four platform modules to `v0.36.0`, and lint/build/test passed before cleanup.
  - Secret scan: `gitleaks git --redact` over every task commit in all three repositories — no leaks.
- Non-gating Alva diagnostic run caused by the earlier scope error:

  - Initial `go run . -d start` failed because the existing local-dev jagent installer selected `g++`; retry with `CC=clang-21 CXX=clang++-21 CGO_CXXFLAGS=-nostdinc++` reached full readiness.
  - The first `make e2e-go-v` omitted those variables from test-owned backend restart subprocesses and was invalidated after the reproduced g++ failure.
  - Clean restart plus `CC=clang-21 CXX=clang++-21 CGO_CXXFLAGS=-nostdinc++ make e2e-go-v` ran the full core suite for 220 seconds. Account deletion (including backend restart), conversation durability, filesystem/auth isolation, managed runtime API keys, gateway/inline V8/net/http/ALFS probes, sandbox execution, task lifecycle, and guest viewer behavior passed.
  - `TestProbe_RunRequireSDK` failed only for `@alva/jstat` and `@alva/algorithm`; `@test/suite` passed. `TestProbe_RunFeedImport` also failed. Each failure is `artifactregistry: not found: no release matches selector "^1.0.0"` for the current local ALFS registry. `docs/ENV_AS_CODE.md` says user/home/API-key setup is sufficient and contains no seed for those packages, confirming current fixture/test drift outside this task's three repository diffs. This observation is unrelated and is not a v8go release blocker.
  - `go run . stop` completed after both attempts; final status confirms all local services stopped.
- Required hosted integration validation: not run. The branch was not pushed while the irrelevant Alva E2E was mistakenly treated as blocking; the next step is to publish the topic branch and run the four-platform candidate and Chrome watcher dry-run without default-branch mutations.
- Migration/docs: no data/service migration. Authoritative v8go README, SECURITY policy, security-release runbook, CODEOWNERS, and all three living changelogs were updated.
- PR/CI/review outcome: no branch or PR has been published yet. Review remains pending the hosted workflow checks and current GitHub settings postflight.

## 8. Remaining Work

- Observe the required hosted read-only `v8upgrade.yml` candidate plus `chrome-security-watch.yml` PR dry-run with no master/staging-branch/tag/release/issue mutation.
- After the new workflow merges, apply the approved master/tag/workflow rulesets and repository-wide Actions SHA-pinning enforcement; the other v8go security settings are already enabled.
- Configure jagent dependency graph/alerts/security updates, private-repository access, and Dependabot secret context; then verify its first grouped PR after merge.
- PR #2 is now verified closed and unmerged with no workflow/Dependabot overlap. Publish the prepared v8runner branch after a final current-head check, without reviving or overwriting that runtime-hardening work.
