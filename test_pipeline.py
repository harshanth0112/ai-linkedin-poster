"""Mocked ranking + dry-run tests. No network, no API keys."""
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from trending import _same_story, _tokens, is_marketing, rank, record_posted


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _item(title, hours, weight=2.5, summary="x" * 80, feed="a", link=None):
    return {
        "title": title,
        "link": link or f"https://example.com/{feed}",
        "summary": summary,
        "when": NOW - timedelta(hours=hours),
        "source": feed,
        "weight": weight,
        "feed_url": f"https://{feed}.example/rss",
    }


class RankTests(unittest.TestCase):
    def test_coverage_and_hn_beat_thin_and_old(self):
        covered = "OpenAI releases GPT model for enterprise coding agents"
        items = [
            _item(covered, 2, 2.5, feed="techcrunch"),
            _item("OpenAI releases GPT model for enterprise coding teams", 3, 2.5, feed="verge"),
            _item("OpenAI releases GPT model for enterprise coding research", 4, 3.0, feed="openai"),
            _item("A tiny note about widgets", 1, 1.5, summary="short blurb", feed="blog"),
            _item("Old recap of last year conference keynote talks", 40, 3.0, feed="mit"),
        ]
        hn = {items[0]["link"]: (120, 40)}
        ranked = rank(items, now=NOW, hn=hn)
        self.assertGreaterEqual(ranked[0]["covered_by"], 2)
        self.assertIn("OpenAI", ranked[0]["title"])
        last_titles = {ranked[-1]["title"], ranked[-2]["title"]}
        self.assertTrue(any("tiny note" in t for t in last_titles))
        self.assertTrue(any("Old recap" in t for t in last_titles))

    def test_marketing_headlines_only(self):
        marketing = [
            "How Oracle turns days of work into minutes with ChatGPT and Codex",
            "How Acme cuts weeks of analysis down to hours",
            "Customer story: a bank uses Copilot to save time",
        ]
        news = [
            "OpenAI releases GPT-5 for developers",
            "Google DeepMind announces new Gemini model for science",
            "How transformers work: a practical guide",
            "NVIDIA reports record data-center revenue",
        ]
        for title in marketing:
            self.assertTrue(is_marketing(title), title)
        for title in news:
            self.assertFalse(is_marketing(title), title)

    def test_marketing_penalty_loses_to_real_news(self):
        items = [
            _item(
                "How Oracle turns days of work into minutes with ChatGPT and Codex",
                2,
                3.0,
                feed="openai",
            ),
            _item("Labs ship a new open model for coding agents", 3, 2.5, feed="techcrunch"),
            _item("Labs ship a new open model for coding research", 3, 2.5, feed="verge"),
        ]
        ranked = rank(items, now=NOW)
        self.assertTrue(ranked[-1]["marketing"])
        self.assertIn("open model", ranked[0]["title"])

    def test_same_story_tokens(self):
        a = _tokens("Google DeepMind announces new Gemini model for science")
        b = _tokens("DeepMind announces a new Gemini model for science research")
        c = _tokens("NVIDIA reports quarterly earnings beat estimates")
        self.assertTrue(_same_story(a, b))
        self.assertFalse(_same_story(a, c))


class HistoryTests(unittest.TestCase):
    def test_record_posted_keeps_link_and_title(self):
        path = Path("posted_test.json")
        try:
            record_posted(str(path), {"link": "https://ex/a", "title": "Hello AI"})
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data[-1]["link"], "https://ex/a")
            self.assertEqual(data[-1]["title"], "Hello AI")
        finally:
            path.unlink(missing_ok=True)


class DryRunTests(unittest.TestCase):
    def test_true_false_and_one_zero(self):
        import post

        with patch.dict(os.environ, {"DRY_RUN": "true"}, clear=False):
            self.assertTrue(post.is_dry_run())
        with patch.dict(os.environ, {"DRY_RUN": "false"}, clear=False):
            self.assertFalse(post.is_dry_run())
        with patch.dict(os.environ, {"DRY_RUN": "1"}, clear=False):
            self.assertTrue(post.is_dry_run())
        with patch.dict(os.environ, {"DRY_RUN": "0"}, clear=False):
            self.assertFalse(post.is_dry_run())
        with patch.dict(os.environ, {"DRY_RUN": ""}, clear=False):
            self.assertTrue(post.is_dry_run())
        with patch.dict(os.environ, {"DRY_RUN": "random"}, clear=False):
            self.assertTrue(post.is_dry_run())

    def test_limit_emojis_keeps_two(self):
        import post

        raw = "Hook 🔹 one 🔹 two 🔹 three 🔹 four"
        self.assertEqual(post.limit_emojis(raw).count("🔹"), 2)

    def test_min_score_skips_day(self):
        import post

        article = {
            "title": "Quiet vendor recap",
            "summary": "x" * 200,
            "link": "https://example.com/quiet",
            "score": 6.1,
            "source": "OpenAI",
            "covered_by": 0,
            "hn_points": 0,
        }
        with (
            patch.dict(os.environ, {"DRY_RUN": "1", "MIN_SCORE": "9"}, clear=False),
            patch.object(post, "get_trending", return_value=[article]),
            patch.object(post, "write_post") as write,
            patch.object(post, "make_poster") as poster,
        ):
            post.main()
            write.assert_not_called()
            poster.assert_not_called()

    def test_main_dry_run_writes_preview_and_does_not_publish(self):
        import post

        article = {
            "title": "Labs ship a new open model",
            "summary": "x" * 200,
            "link": "https://example.com/story",
            "score": 9.0,
            "source": "TechCrunch",
            "covered_by": 2,
            "hn_points": 10,
        }
        payload = json.dumps({"post": "Hook line that is really long and very awesome to read.\n\nInsight is great and very insightful, so long that it passes the 100 char limit easily and flawlessly. Which would you trust more?\n\n#AI #ML #News", "image_prompt": "abstract circuits"})

        with (
            patch.dict(os.environ, {"DRY_RUN": "1"}, clear=False),
            patch.object(post, "get_trending", return_value=[article]),
            patch.object(post, "enrich", return_value=article),
            patch.object(post, "call_gemini", return_value=payload),
            patch.object(post, "make_poster", return_value="image.jpg") as poster,
            patch.object(post, "publish") as publish,
            patch.object(post, "upload_image") as upload,
            patch.object(post, "record_posted") as recorded,
        ):
            post.main()
            poster.assert_called_once()
            publish.assert_not_called()
            upload.assert_not_called()
            recorded.assert_not_called()

        preview = Path("preview.txt")
        self.assertTrue(preview.exists())
        self.assertIn("https://example.com/story", preview.read_text(encoding="utf-8"))


class PipelineEdgeCaseTests(unittest.TestCase):
    def test_extract_feed_summary_uses_alternative_content_fields(self):
        from trending import _extract_feed_summary

        entry = {
            "summary": "Short",
            "content": [{"value": "<p>Full &amp; detailed <b>RSS content:encoded</b> body text.</p>"}],
        }
        cleaned = _extract_feed_summary(entry)
        self.assertEqual(cleaned, "Full & detailed RSS content:encoded body text.")

    def test_enrich_failure_falls_back_to_existing_summary(self):
        import requests
        from trending import enrich

        article = {
            "title": "Short AI release",
            "link": "https://openai.com/news/short-release",
            "summary": "OpenAI released a faster model for coding.",
        }
        with patch("trending.socket.gethostbyname", return_value="93.184.216.34"), \
             patch("trending.requests.get", side_effect=requests.RequestException("timeout")):
            result = enrich(article)
        self.assertEqual(result["summary"], article["summary"])

    def test_short_rss_summary_reaches_content_generation(self):
        import post

        article = {
            "title": "New open-weights model released",
            "summary": "A 7B model with 2x faster inference.",  # < 120 chars
            "link": "https://example.com/short",
            "score": 8.5,
            "source": "TechCrunch",
        }
        payload = json.dumps({
            "post": "A new 7B model just shipped with 2x faster inference for local developers.\n\n• The Shift: Smaller weights.\n\nMy take: Worth testing.\n\nWould you run this locally?\n\n#AI #OpenSource",
            "image_prompt": "glowing microchip on dark desk",
        })
        with (
            patch.dict(os.environ, {"DRY_RUN": "1", "IMAGE_MODE": "never"}, clear=False),
            patch.object(post, "get_trending", return_value=[article]),
            patch.object(post, "enrich", return_value=article),
            patch.object(post, "call_gemini", return_value=payload) as gemini,
            patch.object(post, "record_preview_history"),
        ):
            rc = post.main()
            self.assertEqual(rc, 0)
            gemini.assert_called()

    def test_missing_summary_is_skipped_and_later_candidate_succeeds(self):
        import post

        bad = {"title": "No summary", "summary": "", "link": "https://example.com/1", "score": 9.0}
        failing = {"title": "Failing draft", "summary": "Valid summary text here.", "link": "https://example.com/2", "score": 8.5}
        good = {"title": "Fourth candidate", "summary": "Meaningful AI news summary.", "link": "https://example.com/4", "score": 8.0}

        with (
            patch.dict(os.environ, {"DRY_RUN": "1", "IMAGE_MODE": "never"}, clear=False),
            patch.object(post, "get_trending", return_value=[bad, failing, failing, good]),
            patch.object(post, "enrich", side_effect=lambda a: a),
            patch.object(post, "write_post_with_retry", side_effect=[ValueError("bad"), ValueError("bad"), ("Post body " + "x" * 100, "prompt")]),
            patch.object(post, "record_preview_history"),
        ):
            rc = post.main()
            self.assertEqual(rc, 0)

    def test_all_candidates_failing_returns_nonzero(self):
        import post

        candidates = [
            {"title": f"Story {i}", "summary": "Meaningful summary.", "link": f"https://example.com/{i}", "score": 8.0}
            for i in range(8)
        ]
        with (
            patch.dict(os.environ, {"DRY_RUN": "1", "IMAGE_MODE": "never"}, clear=False),
            patch.object(post, "get_trending", return_value=candidates),
            patch.object(post, "enrich", side_effect=lambda a: a),
            patch.object(post, "write_post_with_retry", side_effect=ValueError("LLM failed")),
        ):
            rc = post.main()
            self.assertEqual(rc, 1)

    def test_empty_feeds_returns_zero(self):
        import post

        with (
            patch.dict(os.environ, {"DRY_RUN": "0"}, clear=False),
            patch.object(post, "get_trending", return_value=[]),
        ):
            rc = post.main()
            self.assertEqual(rc, 0)

    def test_live_publish_success_with_mocks(self):
        import post

        article = {"title": "Live AI story", "summary": "Detailed summary " * 10, "link": "https://example.com/live", "score": 9.0}
        with (
            patch.dict(os.environ, {"DRY_RUN": "0", "IMAGE_MODE": "never", "LINKEDIN_TOKEN": "tok", "LINKEDIN_AUTHOR": "urn:li:person:1"}, clear=False),
            patch.object(post, "get_trending", return_value=[article]),
            patch.object(post, "enrich", return_value=article),
            patch.object(post, "write_post_with_retry", return_value=("Post body " + "x" * 100, "prompt")),
            patch.object(post, "preflight"),
            patch.object(post, "record_posted") as rec,
            patch.object(post, "publish", return_value="urn:li:share:123") as pub,
            patch.object(post, "add_first_comment"),
            patch.object(post, "log_post"),
            patch.object(post, "remind_first_hour"),
        ):
            rc = post.main()
            self.assertEqual(rc, 0)
            rec.assert_called_once()
            pub.assert_called_once()

    def test_linkedin_auth_failure_returns_nonzero(self):
        import post

        article = {"title": "Live AI story", "summary": "Detailed summary " * 10, "link": "https://example.com/live", "score": 9.0}
        with (
            patch.dict(os.environ, {"DRY_RUN": "0", "IMAGE_MODE": "never", "LINKEDIN_TOKEN": "", "LINKEDIN_AUTHOR": ""}, clear=False),
            patch.object(post, "get_trending", return_value=[article]),
            patch.object(post, "enrich", return_value=article),
            patch.object(post, "write_post_with_retry", return_value=("Post body " + "x" * 100, "prompt")),
            patch.object(post, "publish") as pub,
        ):
            with self.assertRaises(RuntimeError):
                post.main()
            pub.assert_not_called()

    def test_linkedin_publish_http_error_rolls_back_and_returns_nonzero(self):
        import requests
        import post

        article = {"title": "Live AI story", "summary": "Detailed summary " * 10, "link": "https://example.com/live", "score": 9.0}
        with (
            patch.dict(os.environ, {"DRY_RUN": "0", "IMAGE_MODE": "never", "LINKEDIN_TOKEN": "tok", "LINKEDIN_AUTHOR": "urn:li:person:1"}, clear=False),
            patch.object(post, "get_trending", return_value=[article]),
            patch.object(post, "enrich", return_value=article),
            patch.object(post, "write_post_with_retry", return_value=("Post body " + "x" * 100, "prompt")),
            patch.object(post, "preflight"),
            patch.object(post, "record_posted") as rec,
            patch.object(post, "unrecord_posted") as unrec,
            patch.object(post, "publish", side_effect=requests.exceptions.HTTPError("500")),
        ):
            rc = post.main()
            self.assertEqual(rc, 1)
            rec.assert_called_once()
            unrec.assert_called_once()


if __name__ == "__main__":
    unittest.main()
