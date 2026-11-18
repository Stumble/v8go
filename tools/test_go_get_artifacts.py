import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "go_get_artifacts.sh"
SHA = "2e8c1d2f4ecdd75dde561a22c2d3c0073a70df8e"
PSEUDO_VERSION = "v0.0.0-20261117152957-2e8c1d2f4ecd"
PLATFORMS = ("darwin_amd64", "darwin_arm64", "linux_amd64", "linux_arm64")
MODULE = f"github.com/stumble/v8go/deps/linux_amd64@{SHA}"
CHECKSUM_ENV = ("GOSUMDB", "GONOSUMDB", "GONOSUMCHECK", "GOFLAGS", "GOINSECURE", "GOPRIVATE")


def module(platform):
    return f"github.com/stumble/v8go/deps/{platform}@{SHA}"


def progress(platform):
    return f"go: downloading github.com/stumble/v8go/deps/{platform} {PSEUDO_VERSION}\n"


def lookup_miss(platform):
    # The exact shape Go printed in the failed staging jobs 134147562049 and
    # 134305775612.
    pseudo = f"github.com/stumble/v8go/deps/{platform}@{PSEUDO_VERSION}"
    return (
        f"go: {module(platform)}: {pseudo}: verifying module: {pseudo}: reading "
        f"https://sum.golang.org/lookup/{pseudo}: 404 Not Found\n"
    )


def checksum_mismatch(platform):
    pseudo = f"github.com/stumble/v8go/deps/{platform}@{PSEUDO_VERSION}"
    return (
        f"go: {module(platform)}: {pseudo}: verifying module: checksum mismatch\n"
        "\tdownloaded: h1:AAAA=\n"
        "\tsum.golang.org: h1:BBBB=\n"
        "\n"
        "SECURITY ERROR\n"
    )


ALL_PROGRESS = "".join(progress(p) for p in PLATFORMS)
SINGLE_MISS = ALL_PROGRESS + lookup_miss("linux_arm64")
ALL_MISSES = ALL_PROGRESS + "".join(lookup_miss(p) for p in PLATFORMS)
MIXED = ALL_PROGRESS + lookup_miss("linux_arm64") + checksum_mismatch("darwin_amd64")
MISS_AND_UNKNOWN = SINGLE_MISS + "go: github.com/stumble/v8go: reading go.mod: permission denied\n"
ONLY_PROGRESS = ALL_PROGRESS

# A stand-in for `go` that records each call with the checksum-related
# environment it inherited, and fails with a chosen message for the first
# FAKE_GO_FAILURES calls.
FAKE_GO = """#!/usr/bin/env bash
set -euo pipefail
count_file="$FAKE_GO_DIR/count"
count=$(( $(cat "$count_file" 2>/dev/null || echo 0) + 1 ))
echo "$count" >"$count_file"
{
  printf 'GOPROXY=%s args=%s' "${GOPROXY-<unset>}" "$*"
  for name in GOSUMDB GONOSUMDB GONOSUMCHECK GOFLAGS GOINSECURE GOPRIVATE; do
    printf ' %s=%s' "$name" "${!name-<unset>}"
  done
  printf '\\n'
} >>"$FAKE_GO_DIR/calls"
if (( count <= FAKE_GO_FAILURES )); then
  cat "$FAKE_GO_DIR/error" >&2
  exit 1
fi
echo "go: added github.com/stumble/v8go/deps/linux_amd64 v0.0.0" >&2
"""

EXPECTED_ENV = (
    " GOSUMDB=sum.golang.org GONOSUMDB=<unset> GONOSUMCHECK=<unset>"
    " GOFLAGS=<unset> GOINSECURE=<unset> GOPRIVATE=<unset>"
)


def expected_call(*modules):
    return f"GOPROXY=direct args=get {' '.join(modules)}{EXPECTED_ENV}"


class GoGetArtifactsTest(unittest.TestCase):
    def run_script(self, *, failures, error, attempts="3", args=(MODULE,)):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            go = tmp_path / "go"
            go.write_text(FAKE_GO, encoding="utf-8")
            go.chmod(go.stat().st_mode | stat.S_IXUSR)
            (tmp_path / "error").write_text(error, encoding="utf-8")
            env = dict(os.environ)
            for name in CHECKSUM_ENV + ("GOPROXY",):
                env.pop(name, None)
            env.update(
                PATH=f"{tmp}{os.pathsep}{env['PATH']}",
                FAKE_GO_DIR=tmp,
                FAKE_GO_FAILURES=str(failures),
                GOSUMDB="sum.golang.org",
                SUMDB_RETRY_ATTEMPTS=attempts,
                SUMDB_RETRY_DELAY_SECONDS="0",
            )
            # Run the helper as the workflow does, as an executable file.
            result = subprocess.run(
                [str(SCRIPT), *args],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            calls_file = tmp_path / "calls"
            calls = (
                calls_file.read_text(encoding="utf-8").splitlines()
                if calls_file.exists()
                else []
            )
            return result, calls

    def test_helper_is_executable(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK))

    def test_succeeds_without_retry(self):
        result, calls = self.run_script(failures=0, error="")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([expected_call(MODULE)], calls)
        self.assertNotIn("retrying", result.stderr)

    def test_retries_lookup_miss_until_published(self):
        result, calls = self.run_script(failures=2, error=SINGLE_MISS)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([expected_call(MODULE)] * 3, calls)
        self.assertIn("retrying in 0s (attempt 1/3)", result.stderr)
        self.assertIn("retrying in 0s (attempt 2/3)", result.stderr)
        self.assertIn("404 Not Found", result.stderr)

    def test_retries_when_every_module_misses(self):
        result, calls = self.run_script(failures=1, error=ALL_MISSES)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(2, len(calls))

    def test_fails_after_last_attempt_when_record_stays_missing(self):
        result, calls = self.run_script(failures=5, error=SINGLE_MISS)

        self.assertEqual(1, result.returncode)
        self.assertEqual(3, len(calls))
        self.assertIn("after 3 attempts", result.stderr)

    def test_does_not_retry_checksum_mismatch(self):
        result, calls = self.run_script(
            failures=5, error=ALL_PROGRESS + checksum_mismatch("linux_amd64")
        )

        self.assertEqual(1, result.returncode)
        self.assertEqual(1, len(calls))
        self.assertIn("checksum mismatch", result.stderr)
        self.assertNotIn("retrying", result.stderr)

    def test_does_not_retry_lookup_miss_mixed_with_mismatch(self):
        # Even if the next call would succeed, a mismatch reported for any
        # module in the same attempt must fail the step.
        result, calls = self.run_script(failures=1, error=MIXED)

        self.assertEqual(1, result.returncode)
        self.assertEqual(1, len(calls))
        self.assertIn("SECURITY ERROR", result.stderr)
        self.assertNotIn("retrying", result.stderr)

    def test_does_not_retry_lookup_miss_mixed_with_other_error(self):
        result, calls = self.run_script(failures=1, error=MISS_AND_UNKNOWN)

        self.assertEqual(1, result.returncode)
        self.assertEqual(1, len(calls))
        self.assertIn("permission denied", result.stderr)

    def test_does_not_retry_failure_without_diagnostic(self):
        result, calls = self.run_script(failures=1, error=ONLY_PROGRESS)

        self.assertEqual(1, result.returncode)
        self.assertEqual(1, len(calls))

    def test_passes_every_module(self):
        modules = tuple(module(p) for p in PLATFORMS)
        result, calls = self.run_script(failures=0, error="", args=modules)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([expected_call(*modules)], calls)

    def test_rejects_invalid_configuration(self):
        for attempts in ("0", "x", "-1"):
            with self.subTest(attempts=attempts):
                result, calls = self.run_script(failures=0, error="", attempts=attempts)
                self.assertEqual(2, result.returncode)
                self.assertEqual([], calls)

        result, calls = self.run_script(failures=0, error="", args=())
        self.assertEqual(2, result.returncode)
        self.assertEqual([], calls)


class V8StageUsesArtifactHelperTest(unittest.TestCase):
    def test_stage_resolves_artifacts_through_retrying_helper(self):
        workflow = (ROOT / ".github/workflows/v8stage.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn('./tools/go_get_artifacts.sh "${module_versions[@]}"', workflow)
        self.assertNotIn("GOPROXY=direct go get", workflow)
        for setting in ("GONOSUMDB", "GONOSUMCHECK", "GOINSECURE", "GOSUMDB=off", "GOFLAGS"):
            self.assertNotIn(setting, workflow)


if __name__ == "__main__":
    unittest.main()
