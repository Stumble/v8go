import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from v8_release import (
    ReleaseVersionError,
    main,
    parse_v8_header,
    release_increment,
)


def version_header(major=15, minor=3, build=76, patch=12):
    return f"""\
#define V8_MAJOR_VERSION {major}
#define V8_MINOR_VERSION {minor}
#define V8_BUILD_NUMBER {build}
#define V8_PATCH_LEVEL {patch}
"""


class V8ReleaseIncrementTest(unittest.TestCase):
    def test_patch_increment_within_same_v8_release_line(self):
        cases = [
            ("15.3.76.12", "15.3.76.13"),
            ("15.3.76.12", "15.3.77.0"),
        ]
        for current, target in cases:
            with self.subTest(current=current, target=target):
                self.assertEqual("+0.0.1", release_increment(current, target))

    def test_minor_increment_when_v8_release_line_changes(self):
        cases = [
            ("15.3.76.12", "15.4.1.0"),
            ("15.3.76.12", "16.0.1.0"),
        ]
        for current, target in cases:
            with self.subTest(current=current, target=target):
                self.assertEqual("+0.1.0", release_increment(current, target))

    def test_equal_or_older_target_fails_closed(self):
        for target in ("15.3.76.12", "15.3.76.11", "15.2.999.999"):
            with self.subTest(target=target):
                with self.assertRaises(ReleaseVersionError):
                    release_increment("15.3.76.12", target)

    def test_invalid_version_fails_closed(self):
        for invalid in ("15.3", "v15.3.76.13", "15.3.76.-1", "latest"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ReleaseVersionError):
                    release_increment("15.3.76.12", invalid)


class V8HeaderParserTest(unittest.TestCase):
    def test_reads_exact_current_v8_version(self):
        self.assertEqual("15.3.76.12", parse_v8_header(version_header()))

    def test_accepts_real_header_comments_and_spacing(self):
        header = """
// generated version
#define   V8_MAJOR_VERSION   15
#define V8_MINOR_VERSION 3
#define V8_BUILD_NUMBER 76
#define V8_PATCH_LEVEL 12
#define V8_IS_CANDIDATE_VERSION 0
"""
        self.assertEqual("15.3.76.12", parse_v8_header(header))

    def test_missing_duplicate_or_non_numeric_macro_fails_closed(self):
        cases = [
            version_header().replace("#define V8_PATCH_LEVEL 12\n", ""),
            version_header() + "#define V8_PATCH_LEVEL 13\n",
            version_header().replace("V8_BUILD_NUMBER 76", "V8_BUILD_NUMBER x"),
        ]
        for header in cases:
            with self.subTest(header=header):
                with self.assertRaises(ReleaseVersionError):
                    parse_v8_header(header)


class V8ReleaseCLITest(unittest.TestCase):
    def test_writes_validated_github_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "v8-version.h")
            output = Path(directory, "github-output")
            path.write_text(version_header(), encoding="utf-8")

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                result = main(
                    [
                        "--current-header",
                        str(path),
                        "--target",
                        "15.3.76.13",
                        "--github-output",
                        str(output),
                    ]
                )

            self.assertEqual(0, result)
            self.assertEqual("+0.0.1\n", stdout.getvalue())
            self.assertEqual(
                "current_v8_version=15.3.76.12\n"
                "target_v8_version=15.3.76.13\n"
                "increment=+0.0.1\n",
                output.read_text(encoding="utf-8"),
            )


class V8StageWorkflowContractTest(unittest.TestCase):
    def test_selects_increment_before_replacing_current_v8_header(self):
        root = Path(__file__).resolve().parents[1]
        workflow = Path(root, ".github/workflows/v8stage.yml").read_text(
            encoding="utf-8"
        )

        selector = "python3 tools/v8_release.py"
        replacement = "Remove previous generated release files"
        self.assertIn(selector, workflow)
        self.assertIn(replacement, workflow)
        self.assertLess(workflow.index(selector), workflow.index(replacement))
        self.assertIn(
            './tools/modifychangelog.py --release "$RELEASE_INCREMENT"', workflow
        )
        self.assertNotIn("--release '+0.1.0'", workflow)


if __name__ == "__main__":
    unittest.main()
