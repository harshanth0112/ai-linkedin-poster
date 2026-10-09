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
        payload = json.dumps({"post": "Hook line that is really long and very awesome to read.\n\nInsight is great and very insightful, so long that it passes the 100 char limit easily and flawlessly.\n\n#AI #ML #News", "image_prompt": "abstract circuits"})

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


if __name__ == "__main__":
    unittest.main()
