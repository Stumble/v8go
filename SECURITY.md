# Security policy

`stumble/v8go` embeds native V8 libraries and must be treated as security-sensitive
infrastructure by maintainers and downstream users.

## Supported versions

Only the latest published v8go release receives security updates. Older releases
should be upgraded promptly, even when a V8 fix has not yet received a public CVE.

| Version | Supported |
|---|---|
| Latest release | Yes |
| Older releases | No |

## Reporting a vulnerability

Use GitHub's **Report a vulnerability** action on this repository's Security page.
It creates a private report visible to the repository security maintainers. Do not
open a public issue for an undisclosed vulnerability or include exploit material
in an ordinary pull request.

Reports should include the affected v8go/V8 versions, impact, reproduction
conditions, and any known upstream Chromium issue or CVE. Please omit secrets,
customer data, and access tokens.

The maintainers will validate whether the issue affects standalone V8 embedding,
coordinate a fixed v8go release, and publish a GitHub Security Advisory with an
accurate Go module version range when appropriate.

## Upstream V8 and Chrome vulnerabilities

All Chrome Stable V8 updates are absorbed through the automated Stable-branch
release path. Public Chrome security posts are separately triaged because some
Chrome vulnerabilities are browser-integration issues and do not affect v8go.
See [the security release process](docs/security-release-process.md).

Updating v8go is defense in depth, not a replacement for process, syscall,
container, network, and resource isolation around untrusted JavaScript.
