import unittest

from chrome_security import (
    FeedError,
    MAX_ENTRY_CONTENT,
    map_chrome_versions_to_v8,
    parse_security_feed,
    render_issue,
)


ENTRY_ID = "tag:blogger.com,1999:blog-8982037438137564684.post-6452445440638395139"
SOURCE_URL = "https://chromereleases.googleblog.com/2026/09/stable-channel-update.html"


def entry(
    content,
    *,
    title="Stable Channel Update for Desktop",
    source=SOURCE_URL,
    entry_id=ENTRY_ID,
):
    return {
        "id": {"$t": entry_id},
        "title": {"$t": title},
        "updated": {"$t": "2026-09-03T12:30:00.000-07:00"},
        "published": {"$t": "2026-09-03T11:56:40.704-07:00"},
        "content": {"$t": content},
        "link": [{"rel": "alternate", "href": source}],
    }


def feed(*entries):
    return {"feed": {"entry": list(entries)}}


class ChromeSecurityFeedTest(unittest.TestCase):
    def test_extracts_v8_cves_versions_and_exploitation(self):
        payload = feed(
            entry(
                """
                <p>The Stable channel has been updated to 152.0.7977.82/.83
                for Windows and Mac and 152.0.7977.82 for Linux.</p>
                <h3>Security Fixes and Rewards</h3>
                <p>[$1,000][542403045] High CVE-2026-85046:
                Type confusion in V8. Reported by Researcher.</p>
                <p>[TBD][547819997] High CVE-2026-85045:
                Race condition in V8. Reported by Researcher.</p>
                <p>Google is aware that an exploit for CVE-2026-85046 exists
                in the wild.</p>
                """
            )
        )

        notices = parse_security_feed(payload)

        self.assertEqual(1, len(notices))
        notice = notices[0]
        self.assertEqual(ENTRY_ID, notice.entry_id)
        self.assertEqual(("152.0.7977.82",), notice.chrome_versions)
        self.assertEqual(
            ("CVE-2026-85046", "CVE-2026-85045"),
            tuple(cve.identifier for cve in notice.cves),
        )
        self.assertTrue(notice.explicitly_engine_related)
        self.assertTrue(notice.review_required)
        self.assertEqual(("CVE-2026-85046",), notice.exploited_cves)

    def test_browser_only_high_cve_requires_review_without_engine_claim(self):
        notices = parse_security_feed(
            feed(
                entry(
                    """
                    <p>Chrome 152.0.7977.82 contains security fixes.</p>
                    <p>[TBD] High CVE-2026-85050: Out of bounds write in
                    WebGL. Reported by Google.</p>
                    """
                )
            )
        )

        self.assertEqual(1, len(notices))
        self.assertTrue(notices[0].review_required)
        self.assertFalse(notices[0].explicitly_engine_related)

    def test_non_desktop_and_non_security_entries_are_ignored(self):
        notices = parse_security_feed(
            feed(
                entry("<p>Security Fixes: High CVE-2026-1: V8.</p>", title="Chrome for Android Update"),
                entry(
                    "<p>Performance and stability improvements.</p>",
                    entry_id="tag:blogger.com,1999:blog-8982037438137564684.post-2",
                ),
            )
        )

        self.assertEqual([], notices)

    def test_unsafe_or_malformed_entry_fails_closed(self):
        cases = [
            feed(entry("High CVE-2026-1: Type confusion in V8.", source="http://example.com/post")),
            {"feed": {"entry": "not-a-list"}},
            feed(entry("High CVE-2026-1: Type confusion in V8."), entry("High CVE-2026-2: Type confusion in V8.")),
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(FeedError):
                    parse_security_feed(payload)

    def test_oversized_entry_fails_closed(self):
        with self.assertRaisesRegex(FeedError, "content is too large"):
            parse_security_feed(
                feed(entry("x" * (MAX_ENTRY_CONTENT + 1)))
            )

    def test_rendered_issue_treats_article_text_as_data(self):
        payload = feed(
            entry(
                """
                <p>Chrome 152.0.7977.82 security fixes.</p>
                <p>High CVE-2026-85046: $(touch /tmp/not-executed)
                [untrusted](javascript:alert(1)) | @Stumble in V8.
                Reported by Researcher.</p>
                """
            )
        )
        notice = parse_security_feed(payload)[0]

        rendered = render_issue(notice, {"152.0.7977.82": "9a9eec" + "0" * 34})

        self.assertIn("$(touch /tmp/not-executed)", rendered.body)
        self.assertIn(f"<!-- chrome-release-id:{ENTRY_ID} -->", rendered.body)
        self.assertIn("v8-mentioned-upstream", rendered.labels)
        self.assertNotIn("`$(touch", rendered.body)
        self.assertIn(
            r"\[untrusted\](javascript:alert(1)) \| &#64;Stumble in V8",
            rendered.body,
        )
        self.assertNotIn("@Stumble in V8", rendered.body)

    def test_maps_only_valid_matching_stable_linux_versions(self):
        releases = [
            {
                "version": "152.0.7977.82",
                "channel": "Stable",
                "platform": "Linux",
                "hashes": {"v8": "9" * 40},
            },
            {
                "version": "153.0.1.2",
                "channel": "Beta",
                "platform": "Linux",
                "hashes": {"v8": "8" * 40},
            },
        ]

        result = map_chrome_versions_to_v8(
            releases, ("152.0.7977.82", "153.0.1.2")
        )

        self.assertEqual({"152.0.7977.82": "9" * 40}, result)

    def test_large_browser_release_renders_only_engine_or_exploitation_signals(self):
        browser_cves = "".join(
            f"<p>High CVE-2026-{10000 + index}: Use after free in WebGL. "
            "Reported by Google.</p>"
            for index in range(60)
        )
        payload = feed(
            entry(
                "<p>Chrome 152.0.7977.82 Security Fixes.</p>"
                + browser_cves
                + "<p>High CVE-2026-19999: Type confusion in V8. "
                "Reported by Google.</p>"
            )
        )
        notice = parse_security_feed(payload)[0]

        rendered = render_issue(notice, {})

        self.assertIn("Public CVEs in post: `61`", rendered.body)
        self.assertIn("CVE-2026-19999", rendered.body)
        self.assertNotIn("CVE-2026-10000", rendered.body)


if __name__ == "__main__":
    unittest.main()
