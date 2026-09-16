# fix: follow the upstream V8 release line

## Background

The automated staging workflow previously incremented v8go by `+0.1.0` for
every validated V8 tag. That made a patch refresh within the same V8 release
line look like a new v8go feature line: V8 15.3.76.11 and 15.3.76.12 became
v8go v0.37.0 and v0.38.0 even though V8's `major.minor` pair did not change.

## Behavior

- A strictly newer V8 tag with the same `major.minor` pair increments the v8go
  patch version by `+0.0.1`.
- A strictly newer V8 tag with a different `major.minor` pair increments the
  v8go minor version by `+0.1.0`.
- An equal, older, malformed, or ambiguous V8 version fails before staging.
- Existing immutable releases remain unchanged; the policy applies forward from
  v8go v0.38.0 and V8 15.3.76.12.

## Implementation

`tools/v8_release.py` reads the current generated V8 version header before the
candidate artifacts replace it, validates the target four-part V8 tag, and
selects the only permitted changelog increment. `v8stage.yml` carries that
validated output into `modifychangelog.py` instead of using a fixed minor bump.

## Verification

Table-driven tests cover patch/build changes within V8 15.3, moves to V8 15.4
and 16.0, malformed headers/tags, non-forward transitions, and the exact GitHub
output contract. The existing V8 Upgrade pull-request path runs the complete
offline tool suite and hosted candidate validation without publishing from a
non-default ref.
