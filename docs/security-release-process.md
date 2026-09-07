# V8 security release process

This repository uses two complementary update paths. Stable-tip automation
delivers code as quickly as the production V8 branch advances. Public security
triage later supplies severity, affected-version metadata, and downstream
Dependabot alerts.

## Stable V8 delivery

The V8 upgrade workflow resolves the current Chrome Stable milestone, reads the
tip of its V8 branch, and checks for one official non-PGO version tag at that
commit. It runs every four hours.

- An untagged tip is built and tested as a candidate but is not released.
- A tagged tip is built for Darwin/Linux on AMD64/ARM64, checksummed, staged,
  tested, and leak-checked before the exact commit can advance `master`.
- A changed `master`, invalid upstream metadata, checksum failure, test failure,
  or conflicting tag prevents publication and leaves a tracking issue.
- Published releases and tags are never rewritten. Recovery uses a newer release
  or resumes creation of a missing release at an already-tested commit.

The workflow intentionally tracks Stable rather than V8 main, Canary, or Beta.
Chromium recommends the Stable branch tip to embedders because security and
correctness fixes continue to be backmerged after an individual Chrome build.

## Public Chrome security triage

The Chrome Security Watch reads the official Chrome Releases Stable feed every
hour. Each recent Desktop Stable security post becomes one assigned triage issue.
The issue highlights explicit V8/WebAssembly CVEs, official in-the-wild statements,
and any Chrome-version-to-V8-hash mapping available from Chromium Dash.

Feed content is public but untrusted. It is parsed as inert, length-bounded data.
The watcher has Issues permission only and cannot publish releases or advisories.

For each direct engine signal or Critical/High review item:

1. Open the official Chrome post linked by the issue. Do not rely on copied text.
2. Determine whether the bug affects standalone V8 embedding. Browser-only
   components, renderer sandbox boundaries, and Chrome integrations do not imply
   v8go impact.
3. Identify the upstream fix commit when disclosure permits it.
4. For every potentially affected v8go tag, read its exact V8 hash:

   ```sh
   git show <v8go-tag>:deps/v8_hash
   ```

5. In a V8 checkout containing both commits, verify whether the fix is present
   using commit ancestry or the relevant Stable backmerge. Record the evidence;
   do not infer the affected range from version numbers alone.
6. Ensure the fixed v8go release exists and its four platform modules resolve.
7. Create a repository security advisory using the existing upstream CVE when
   applicable. Select the Go ecosystem and add affected/patched ranges for
   `github.com/stumble/v8go` and any affected platform modules:

   - `github.com/stumble/v8go/deps/darwin_amd64`
   - `github.com/stumble/v8go/deps/darwin_arm64`
   - `github.com/stumble/v8go/deps/linux_amd64`
   - `github.com/stumble/v8go/deps/linux_arm64`

8. Publish only after the fixed version is available. GitHub may take up to 72
   hours to review the advisory for the global database and downstream Dependabot
   alerts, so routine no-cooldown version PRs remain the first delivery path.
9. Verify the advisory, Dependabot alerts, and security PRs in known downstream
   repositories. Never auto-merge or deploy a consumer PR from this repository.

## Required GitHub settings

A repository administrator must verify these controls before default-branch V8
publication is enabled:

- Private Vulnerability Reporting is enabled.
- GitHub Issues are enabled so blocked upgrades and public security triage can
  notify the assigned maintainer.
- Release immutability is enabled.
- `master`, release tags, and workflow/security files are protected by rulesets.
- Only the release automation path can advance `master` after its required checks.
- Default workflow permissions are read-only; write permissions are job-scoped.
- Dependency graph, Dependabot alerts, and Dependabot security updates are enabled.
- CODEOWNERS review is required for release-critical workflow and policy changes.

Consumers separately enable Dependabot and any private-module access they need.
No cross-repository release credential is stored here.
