# fix: make V8 candidate validation authoritative

## 1. Background and Current State

- Problem/outcome: the V8 15.3 release remained blocked after four Go 1.24
  candidate jobs and the leakcheck binary produced broad null/undefined/function
  failures. Go oldstable/stable candidate jobs passed, and staging/release stayed
  correctly disabled.
- Evidence: the green PR run and blocked publish run used identical source trees,
  Go 1.24.13 toolchains, V8 hash `7252dca56e5d90f58c624d776fb8f3bf4ea4ec73`,
  and byte-identical common/platform artifacts. The failures therefore do not
  come from a different V8 build.
- Existing hazards: `SetFlags` is process-global but can be called after V8
  initialization; its test mutates flags in the main test process. Test and
  leakcheck workflows also restore Go build caches whose keys do not name the
  overlaid native candidate, and they only validate a version-shaped string.

## 2. Problem Model and End-to-End Behavior

- Root cause boundary: Go 1.24 is not a reliable V8 15.3 runtime boundary on any
  supported platform. Post-initialization flag mutation and native-unaware Go
  caches make candidate results non-authoritative even on newer Go versions.
- Goal: require Go 1.26+, make flags immutable after initialization, rebuild the
  cgo bridge for every validation job, and prove that the executing V8 version
  equals the discovered official tag.
- Non-goals: do not serialize legitimate multi-isolate tests, retry failed
  validation, bypass leakcheck, or publish from an unvalidated commit.

## 3. Research, Findings, and Architecture Decision

- D1: set the tested support floor to Go 1.26 and test it alongside `stable`.
- D2: guard `SetFlags` and first initialization with one mutex; panic on calls
  after initialization and remove the flag that permitted post-init mutation.
- D3: test `SetFlags` in an isolated subprocess so process-global changes cannot
  contaminate the main test binary.
- D4: disable setup-go caching in test/leakcheck workflows and pass the official
  candidate version into `TestVersion` for exact runtime verification.
- Risk: v8runner's per-Runner heap flag must move to per-isolate resource
  constraints before it can safely create a second Runner in one process.

## 4. Implementation Design

- `v8go.go` owns the pre-initialization `SetFlags` contract.
- `v8go_test.go` owns subprocess flag coverage and exact runtime-version checks.
- Root/generated modules, generator, README, and CHANGELOG own the Go 1.26 floor.
- `test.yml`, `leakcheck.yml`, and `v8build.yml` own cache-free compilation and
  candidate-version propagation.

### Serial Implementation Checklist

- [x] Reproduce the old post-init flag behavior with a failing subprocess test.
- [x] Enforce the flag initialization boundary and raise the Go floor to 1.26.
- [ ] Validate released V8 15.2 and exact V8 15.3 with fresh Go build caches.
- [ ] Publish a reviewed PR and monitor every current-head hosted check.
- [ ] After merge, rerun the write-scoped V8 15.3 release workflow and verify the
  immutable release before closing the tracking issues.

## 5. Verification and E2E Design

- Run focused flag/version tests and the complete suite on Go 1.26 against the
  checked-in V8 release.
- Overlay the exact checksum-verified V8 15.3 artifacts in an isolated worktree,
  regenerate platform modules, use a fresh `GOCACHE`, and run focused stress plus
  the full suite and coverage.
- Run actionlint, generator idempotence, Python tests, diff checks, and gitleaks.
- E2E Required: no. Hosted four-platform V8 candidate and release workflows are
  the integration/release gates; no deployed Alva service changes here.

## 6. Human Decisions and Interaction

- The user explicitly does not require old Go support. Go 1.24 was first chosen
  to match direct consumers, then rejected by exact V8 15.3 evidence on all four
  platforms; Go 1.26 is the current supported oldstable boundary.

## 7. Outcome and Evidence

- Pending implementation verification and hosted current-head validation.

## 8. Remaining Work

- Complete local validation, publish the PR, obtain human merge, then rerun the
  blocked V8 15.3 release and update downstream consumers.
