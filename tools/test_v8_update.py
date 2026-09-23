import unittest
from pathlib import Path

from v8_update import DiscoveryError, classify_stable_v8, parse_quarantines


CURRENT = "4323497a6a73839e6d5260f6acd7ec0212cb3321"
LATEST = "9a9eec04284e48ca4cc1d42abc9da2adb1264431"


def stable_release():
    return [
        {
            "version": "152.0.7977.82",
            "milestone": 152,
            "channel": "Stable",
            "platform": "Linux",
            # This deliberately remains behind the branch tip. Discovery must
            # use it only to identify the Stable milestone.
            "hashes": {"v8": CURRENT},
        }
    ]


def milestone():
    return [{"milestone": 152, "v8_branch": "15.2"}]


def branch_refs(commit=LATEST):
    return f"{commit}\trefs/branch-heads/15.2\n"


def tag_refs(*tags):
    return "".join(f"{LATEST}\trefs/tags/{tag}\n" for tag in tags)


class StableV8DiscoveryTest(unittest.TestCase):
    def classify(self, **overrides):
        args = {
            "release_payload": stable_release(),
            "milestone_payload": milestone(),
            "branch_ref_output": branch_refs(),
            "tag_ref_output": tag_refs("15.2.124.24"),
            "current_hash": CURRENT,
            "bad_hashes_text": "# none\n",
        }
        args.update(overrides)
        return classify_stable_v8(**args)

    def test_tagged_branch_tip_is_release(self):
        result = self.classify()

        self.assertEqual(152, result.milestone)
        self.assertEqual("152.0.7977.82", result.chrome_version)
        self.assertEqual("15.2", result.branch)
        self.assertEqual(LATEST, result.commit)
        self.assertEqual("15.2.124.24", result.version)
        self.assertEqual("release", result.state)

    def test_chrome_release_hash_does_not_override_branch_tip(self):
        result = self.classify()

        self.assertNotEqual(stable_release()[0]["hashes"]["v8"], result.commit)
        self.assertEqual(LATEST, result.commit)

    def test_untagged_or_pgo_only_tip_is_candidate(self):
        for refs in ("", tag_refs("15.2.124.24-pgo")):
            with self.subTest(refs=refs):
                result = self.classify(tag_ref_output=refs)
                self.assertEqual("candidate", result.state)
                self.assertIsNone(result.version)

    def test_current_hash_is_noop_even_if_tagged(self):
        result = self.classify(current_hash=LATEST)

        self.assertEqual("current", result.state)

    def test_quarantined_hash_retains_reason(self):
        result = self.classify(
            bad_hashes_text=f"{LATEST} Linux arm64 compiler regression\n"
        )

        self.assertEqual("quarantined", result.state)
        self.assertEqual("Linux arm64 compiler regression", result.quarantine_reason)

    def test_conflicting_release_tags_fail_closed(self):
        with self.assertRaisesRegex(DiscoveryError, "multiple non-PGO"):
            self.classify(
                tag_ref_output=tag_refs("15.2.124.24", "15.2.125.1")
            )

    def test_invalid_release_or_ref_fails_closed(self):
        cases = [
            {"release_payload": []},
            {"release_payload": [{"milestone": 152, "channel": "Beta"}]},
            {"milestone_payload": [{"milestone": 152, "v8_branch": "main"}]},
            {"branch_ref_output": "not-a-sha\trefs/branch-heads/15.2\n"},
            {"branch_ref_output": branch_refs() + branch_refs(CURRENT)},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(DiscoveryError):
                    self.classify(**overrides)

    def test_annotated_tag_uses_peeled_commit(self):
        tag_object = "a" * 40
        refs = (
            f"{tag_object}\trefs/tags/15.2.124.24\n"
            f"{LATEST}\trefs/tags/15.2.124.24^{{}}\n"
        )

        result = self.classify(tag_ref_output=refs)

        self.assertEqual("15.2.124.24", result.version)


class QuarantineParserTest(unittest.TestCase):
    def test_ignores_comments_and_keeps_first_reason(self):
        quarantines = parse_quarantines(
            "# explanation\n"
            f"{LATEST} first reason\n"
            f"{LATEST} second reason\n"
            "malformed\n"
        )

        self.assertEqual({LATEST: "first reason"}, quarantines)


class QueuedWorkflowSafetyTest(unittest.TestCase):
    def test_stale_master_run_skips_build_and_report_side_effects(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/v8upgrade.yml"
        ).read_text()
        decision = workflow.split("- id: decision", 1)[1].split(
            "  build_candidate:", 1
        )[0]
        stale_check = decision.split("live_master_sha=", 1)[1].split(
            "should_publish=false\n", 1
        )[0]

        self.assertIn("git ls-remote origin refs/heads/master", decision)
        self.assertIn('[[ "$base_sha" != "$live_master_sha" ]]', decision)
        self.assertIn("echo 'stale=true'", stale_check)
        self.assertIn("echo 'should_build=false'", stale_check)
        self.assertIn("echo 'should_publish=false'", stale_check)
        self.assertIn("exit 0", stale_check)
        self.assertLess(
            decision.index("live_master_sha="),
            decision.index('if [[ "$UPSTREAM_STATE" == release ]]'),
        )

        report = workflow.split("  report:", 1)[1]
        self.assertIn("STALE_RUN: ${{ needs.discover.outputs.stale }}", report)
        self.assertLess(
            report.index('if [[ "$STALE_RUN" == true ]]'),
            report.index("gh label create v8-upgrade"),
        )


if __name__ == "__main__":
    unittest.main()
