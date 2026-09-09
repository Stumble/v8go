# fix: stabilize V8 15.3 candidate tests

## 1. Background and Current State

- Problem/outcome: the first scheduled V8 15.3.76.11 candidate completed all four native builds, checksum assembly, leakcheck, and eleven test matrix jobs, but Linux AMD64 with Go 1.19 failed once in termination and otherwise unrelated function-call tests. The release correctly remained blocked in Stumble/v8go#19 while an automatic retry began.
- Current implementation: `TestIsolateTerminateExecution` launches an unsynchronized goroutine inside a Go-backed V8 callback, performs a nested infinite `Function.Call`, ignores that inner call's error, and does not join the goroutine. The repository already has a bounded `startIsolateTerminationWatchdog` helper with a JavaScript-start handshake for newer termination tests. The root and generated platform modules declare Go 1.19, and the test matrix runs Go 1.19 plus `oldstable` and `stable`.
- Related changelog/code evidence: `docs/changelogs/2026-09-05-harden-v8-security-updates.md` owns the candidate-before-release design. Run 34297958260 is the observed failure; its exact V8 15.3 artifacts remain available for reproduction.
- Runtime evidence: the exact failed candidate passed the focused failure set 100 consecutive times and the complete shuffled suite 20 consecutive times locally with Go 1.19.13, Clang 21, and `GOMAXPROCS=2`; a separate exact full CI command also passed. A scheduled retry failed on Linux ARM64 with Go 1.19 across Object, Value, Function, and termination assertions. After moving the floor to Go 1.24, hosted Linux ARM64 reproduced the same broad corruption at the exact instant the parallel `TestSetFlag` mutated process-global V8 flags. This disproves a deterministic V8 15.3 Function API incompatibility and identifies an existing invalid cross-test concurrency boundary rather than a Go-version-specific defect.
- Constraints/assumptions/unknowns: do not retry or skip a failing release gate, do not change production Function/Isolate behavior, retain all four platforms, and keep a tested minimum Go version. jagent declares Go 1.24.7 and v8runner declares Go 1.24. External v8go users on Go 1.19-1.23 will need to upgrade.

## 2. Problem Model and End-to-End Behavior

- Root cause: `TestSetFlag` runs in parallel even though `SetFlags` mutates process-global V8 state; hosted V8 15.3 runs show unrelated isolate operations returning corrupt null/undefined/function results in the same millisecond as that mutation. Separately, the legacy termination test has no proof that JavaScript entered the infinite execution before the terminator runs and has no explicit goroutine join, making its failure signal unnecessarily timing-dependent. Go 1.19 is also long unsupported and no longer a useful compatibility gate.
- Goals/non-goals: make the termination test bounded and causally synchronized; move the supported Go floor to 1.24; preserve current production APIs and strict release gates. Do not add workflow retries, loosen assertions, remove a platform, or change V8 execution semantics.
- B1 — Deterministic termination proof: the nested JavaScript loop signals that execution has begun, a bounded watchdog terminates that isolate, the execution-thread callback records the active termination state and inner termination error, and the test joins the watchdog before disposing the isolate.
- B2 — Supported Go floor: the root module, every generated platform module, documentation, generator default, and CI matrix consistently require and test Go 1.24 or newer.
- F1 — Missing execution handshake: if JavaScript never signals readiness, the watchdog terminates after the existing two-second bound and the test fails with an explicit timeout result rather than hanging or leaking a goroutine.
- F2 — Candidate regression: any genuine candidate test or leakcheck failure continues to block staging and release without an automatic test retry masking it.
- Idempotency/compatibility: rerunning the generator preserves Go 1.24 module directives. The test change is test-only. Raising the Go directive is an intentional compatibility change for Go 1.19-1.23 consumers and is documented for the next pre-1.0 minor release.

## 3. Research, Findings, and Architecture Decision

- Repository evidence: the first two hosted attempts failed Go 1.19 jobs on different Linux architectures. A later Go 1.24 Linux ARM64 run reproduced the same broad failures: `TestSetFlag` resumed at 19:53:21.515, several value/function/isolate tests failed during that interval, and `TestSetFlag` completed in the same millisecond. Local exact-artifact stress could not reproduce a product failure before isolating this global mutation. Git history shows the old termination test's bare goroutine dates to a prior flake fix, while the newer watchdog/started-channel pattern already provides bounded coordination in the same file.
- External research: Go 1.27 and 1.26 are the current supported release lines under Go's two-newer-major release policy. Go 1.19 is long unsupported. The two direct Stumble consumers already set a Go 1.24 floor.
- Approaches: relying on retries would hide failures; deleting only Go 1.19 removes an obsolete matrix entry but leaves the global flag race; serializing the entire suite would hide valid multi-isolate concurrency coverage. Keep only the process-global flag mutation sequential, retain all other parallel tests, make the termination test deterministic, align the minimum toolchain to Go 1.24, and disable matrix fail-fast so future failures retain full evidence.
- D1 — Synchronized existing pattern: reuse the in-file watchdog and a JavaScript `started` callback, preserve the nested `Function.Call` only to observe termination on the execution thread, retain its error for assertion, and join the watchdog before cleanup.
- D2 — Go 1.24 minimum: replace the Go 1.19 matrix entry with 1.24 and update the generator-owned platform module directives plus the root contract and README.
- R1 — Hosted-only concurrency failure: the symptom cannot be forced locally, but the Go 1.24 hosted timestamps falsify the earlier Go-version-only hypothesis and place the broad failures inside the process-global flag mutation window. Falsification relies on keeping only `TestSetFlag` sequential, retaining all other parallel tests, running focused/full-suite Go 1.24 stress, and completing hosted candidate validation with fail-fast disabled.
- R2 — External compatibility: unknown external consumers may still build with Go 1.19-1.23; the requirement change must remain visible in README/changelog/release notes.
- Closest references: `isolate_test.go`, `deps/update_cgo.py`, `.github/workflows/test.yml`, root/platform `go.mod`, README Requirements, and the candidate workflow from the primary security-hardening changelog.

## 4. Implementation Design

- Change/ownership map: `isolate_test.go` owns deterministic termination coverage; `go.mod` owns the public minimum Go contract; `deps/update_cgo.py` owns generated platform `go.mod` files; `.github/workflows/test.yml` owns the minimum-version matrix; `.github/workflows/v8stage.yml` owns the exact toolchain used to stage module versions; `.github/actions/setup-clang-21/action.yml` owns compiler installation; `.github/scripts/apt-update.sh` owns deterministic hosted-runner APT metadata updates; README Requirements owns consumer-facing prerequisites.
- Critical interfaces/algorithms: the test registers a `started` callback inside the known infinite loop, lets the bounded watchdog terminate only after that signal, captures the nested call error and `IsExecutionTerminating` on the execution thread, joins the watchdog, and verifies both inner and outer `ExecutionTerminated` boundaries. The staging workflow installs the Go version declared by the trusted base `go.mod` before creating its workspace and resolving staged platform modules.
- Error/security/observability: a missing callback becomes an explicit bounded timeout assertion. Candidate test failure continues to create/update the blocked upgrade issue. Clang setup temporarily excludes the unrelated hosted-runner Chrome APT source and retries required index updates within a fixed bound, preventing third-party rollout metadata from cancelling the test matrix. The process-global `SetFlags` test is kept outside parallel isolate execution, its API documentation states the caller synchronization requirement, and CI keeps the rest of the matrix running after one failure so maintainers retain complete evidence. No production permission, secret, process isolation, or runtime surface changes.
- Migration/compatibility/rollout: regenerate four platform module files with Go 1.24, test both currently released V8 15.2 and the exact V8 15.3 candidate, then publish a normal reviewed PR. No database, service, Kubernetes, or production migration exists.
- Authoritative docs: add Go 1.24 to README Requirements, mark the compatibility change as breaking in the release changelog, and record the rationale in this task changelog.

### Serial Implementation Checklist

- [x] Rewrite the termination test around the existing bounded handshake and join, then run focused candidate stress.
- [x] Raise the root/generator/matrix Go floor to 1.24, regenerate platform modules, update README, and inspect generated diffs.
- [x] Run focused and complete suites on released V8 15.2 and exact candidate V8 15.3 with the documented Clang contract.
- [ ] Pin the staging workflow to the declared minimum Go toolchain, run workflow/static validation, complete review, publish the PR, and monitor hosted checks without merging.

## 5. Verification and E2E Design

- Testability: the exact failed artifact is locally overlaid with verified SHA-256 manifests. Focused repetitions falsify unsynchronized termination or secondary function-call corruption; shuffled full-suite repetitions exercise cross-test interference; the ordinary tree validates released V8 compatibility.
- Representative tests: run the rewritten `TestIsolateTerminateExecution` together with affected function-call tests at high count; run the complete suite with `GOMAXPROCS=2` and shuffle; run the normal complete suite against the checked-in V8 release.
- E2E Required: no — this changes the minimum compiler contract and a release-gate test, not a deployed service or user request flow. Hosted four-platform candidate CI is the required external integration evidence.
- Exact commands:

```bash
gofmt -w isolate_test.go
./deps/update_cgo.py
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12 .github/workflows/test.yml
go test -count=100 -run '^(TestIsolateTerminateExecution|TestFunctionCall|TestFunctionCallToGoFunc|TestFunctionCallError|TestFunctionTemplateGetFunction)$' .
GOMAXPROCS=2 go test -count=20 -shuffle=on ./...
GOMAXPROCS=4 go test -count=30 -shuffle=on ./...
go test -count=1 ./...
git diff --check
```

| Behavior | Evidence |
|----------|----------|
| B1 | Focused 100-count candidate test and shuffled full candidate suite |
| B2 | Generator diff, Go 1.24 local/hosted job, oldstable/stable hosted jobs |
| F1 | Watchdog timeout branch remains bounded and asserted |
| F2 | Workflow inspection plus hosted candidate failure/success propagation |

## 6. Human Decisions and Interaction

- The user rejected maintaining Go 1.19 compatibility and approved raising the minimum to Go 1.24, which matches jagent and v8runner.
- The approved fix synchronizes the termination test instead of changing production APIs, weakening assertions, or adding CI retries.

## 7. Outcome and Evidence

- Result: the supported Go floor is consistently 1.24, the minimum-version matrix now tests Go 1.24, the legacy termination test uses a bounded started/watchdog/join protocol, and the release staging job installs the declared minimum toolchain before resolving staged modules. Linux CI package setup also isolates unrelated Google Chrome APT metadata and applies bounded package-index retries after two hosted attempts failed before executing tests, first in Clang setup and then in CCache setup. The process-global `SetFlags` test no longer races parallel isolate tests, and the matrix retains all results after a failure. Production v8go function/isolate behavior is unchanged; the existing global flag API now documents its caller synchronization contract.
- B/F/D/R reconciliation: B1/D1 and F1 are implemented by the synchronized test and passed exact-candidate stress; B2/D2 are implemented across the root/generated module contracts, generator, workflows, README, and release changelog. F2 remains enforced by the unchanged candidate-before-stage dependency chain, while matrix fail-fast is disabled to retain every independent result. R1 is addressed by serializing only the process-global mutation and retaining all other parallel coverage. R2 is documented as a breaking requirement change.
- Changes/deviations: the initial experiment attempted to observe `IsExecutionTerminating` from the watchdog thread; V8 15.3 consistently returned false there, so the final test preserves the nested call solely to observe termination on the execution thread, captures its error, and synchronizes its start/join. Review also found and fixed the previously implicit Go toolchain in `v8stage.yml` and added the breaking release-note entry. Hosted Go 1.24 then falsified the initial Go-version-only diagnosis and exposed `TestSetFlag` racing all parallel isolate tests, so the final design serializes only that process-global mutation and documents its API contract.
- Verification/E2E:
  - Exact failed V8 15.3 candidate, diagnostic Go 1.19.13: original focused set 100 passes, original full shuffled suite 20 passes, and one exact coverage suite passed locally; two hosted attempts nevertheless failed different Go 1.19 Linux architectures while newer Go jobs passed.
  - Released V8 15.2 with final code and Go 1.24.13/Clang 21: focused termination/function set `-count=100` passed; complete `go test -count=1 ./...` passed.
  - Exact V8 15.3 candidate with final code and Go 1.24.13/Clang 21: the hosted failure family plus `TestSetFlag` passed `-count=200`; `GOMAXPROCS=4 go test -count=30 -shuffle=on ./...` passed in 208 seconds; final coverage suite passed with 93.2% root-package statement coverage.
  - `./deps/update_cgo.py` was idempotent by before/after diff hash; `actionlint v1.7.12` passed every workflow; Python compilation and 17 tool tests passed; `git diff --check` passed.
  - E2E Required: no, as planned. The hosted four-platform candidate workflow is the external integration gate.
- Migration/docs: no service/data migration. README and the release changelog describe the Go 1.24 requirement.
- PR/CI/review outcome: local review is complete; PR publication and current-head hosted validation are pending.

## 8. Remaining Work

- Publish and monitor the v8go PR without merging it automatically.
- After human merge, allow or manually trigger the V8 15.3 upgrade, verify the immutable release, and close Stumble/v8go#19 and #18 only when the fixed release exists.
