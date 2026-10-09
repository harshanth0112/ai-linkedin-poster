"""
Offline tests for post.py and trending.py.

Rules:
- No real network calls; all I/O is mocked.
- No test mocks the function under test.
- Every test runs in a fresh temp dir with an empty posted.json.
- Python 3.10 compatible (no match-case, no 3.11+ features).
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import requests

from trending import (
    FEED_DOMAINS,
    _get_domain,
    enrich,
    normalise_link,
    rank,
    record_posted,
    unrecord_posted,
)
import post


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _TempDir(unittest.TestCase):
    """Change cwd to a fresh temp dir for each test."""

    def setUp(self):
        self.orig_dir = os.getcwd()
        self._td = tempfile.TemporaryDirectory()
        os.chdir(self._td.name)
        Path("posted.json").write_text("[]", encoding="utf-8")

    def tearDown(self):
        os.chdir(self.orig_dir)
        self._td.cleanup()


def _make_http_error(status_code: int) -> requests.HTTPError:
    """Return a real requests.HTTPError with a mock response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.reason = "Error"
    resp.url = "https://api.linkedin.com/"
    err = requests.HTTPError(response=resp)
    err.response = resp
    return err


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

class TestRanking(_TempDir):
    def test_offtopic_and_thin_penalty(self):
        from datetime import datetime, timezone
        items = [{
            "title": "Just a thing",
            "summary": "short",
            "when": datetime.now(timezone.utc),
            "source": "f",
            "weight": 2.5,
            "feed_url": "f",
            "link": "http://example.com",
        }]
        ranked = rank(items)
        # off-topic (-2) + thin (-2) → score well below 4
        self.assertLess(ranked[0]["score"], 4.0)


# ---------------------------------------------------------------------------
# Link normalisation
# ---------------------------------------------------------------------------

class TestNormalisation(_TempDir):
    def test_utm_stripped(self):
        self.assertEqual(
            normalise_link("https://ex.com/a?utm_source=1&b=2"),
            "https://ex.com/a?b=2",
        )

    def test_trailing_slash_stripped(self):
        self.assertEqual(normalise_link("https://ex.com/a/"), "https://ex.com/a")


# ---------------------------------------------------------------------------
# History: record / unrecord / cap
# ---------------------------------------------------------------------------

class TestHistory(_TempDir):
    def test_record_unrecord_round_trip(self):
        record_posted("posted.json", {"link": "https://a.com", "title": "A"})
        record_posted("posted.json", {"link": "https://b.com", "title": "B"})
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(len(data), 2)

        unrecord_posted("posted.json", {"link": "https://a.com"})
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["link"], "https://b.com")


# ---------------------------------------------------------------------------
# enrich() — SSRF / redirect guard
# ---------------------------------------------------------------------------

# A feed domain that is definitely in FEED_DOMAINS:
_FEED_URL = "https://openai.com/news/some-article"
# A public IP for DNS mocking (openai.com)
_PUBLIC_IP = "52.202.62.202"


class TestEnrich(_TempDir):

    def test_http_scheme_blocked_no_request(self):
        """http:// is rejected before any network call."""
        with patch("trending.requests.get") as mock_get:
            result = enrich({"link": "http://openai.com/news/a", "title": "A"})
        mock_get.assert_not_called()
        self.assertIsNone(result.get("summary"))

    def test_unknown_domain_blocked_no_request(self):
        """Domain not in FEED_DOMAINS is rejected before any network call."""
        with patch("trending.requests.get") as mock_get:
            result = enrich({"link": "https://attacker.evil/a", "title": "A"})
        mock_get.assert_not_called()
        self.assertIsNone(result.get("summary"))

    def test_redirect_to_non_feed_domain_blocked(self):
        """
        302 pointing to a non-feed domain must:
        (a) return the article unchanged,
        (b) call requests.get exactly once (the redirect target is never fetched),
        (c) raise_for_status() is not called (real 3xx responses do not raise).

        This test deliberately does NOT use raise_for_status side_effect so we
        confirm the code checks status_code instead of relying on an exception.
        """
        redirect_resp = MagicMock()
        redirect_resp.status_code = 302
        redirect_resp.headers = {"Location": "https://evil.example/steal"}
        # raise_for_status must NOT raise for a 302 (real behaviour)
        redirect_resp.raise_for_status.return_value = None

        article = {"link": _FEED_URL, "title": "A", "summary": ""}

        with patch("trending.socket.gethostbyname", return_value=_PUBLIC_IP):
            with patch("trending.requests.get", return_value=redirect_resp) as mock_get:
                result = enrich(article)

        # (a) article returned unchanged
        self.assertEqual(result["summary"], "")
        # (b) exactly one HTTP call — to openai.com, never to evil.example
        self.assertEqual(mock_get.call_count, 1)
        called_url = mock_get.call_args[0][0]
        self.assertIn("openai.com", called_url)
        self.assertNotIn("evil.example", called_url)
        # (c) raise_for_status was never called (redirect handled by status check)
        redirect_resp.raise_for_status.assert_not_called()

    def test_size_cap_1500000_bytes(self):
        """Body read is capped at exactly 1 500 000 bytes."""
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status.return_value = None
        ok_resp.raw.read.return_value = b"<p>text</p>"

        with patch("trending.socket.gethostbyname", return_value=_PUBLIC_IP):
            with patch("trending.requests.get", return_value=ok_resp):
                enrich({"link": _FEED_URL, "title": "A", "summary": ""})

        ok_resp.raw.read.assert_called_once_with(1_500_000, decode_content=True)

    def test_private_ip_blocked(self):
        """Private IP (127.0.0.1) blocks the fetch before any HTTP call."""
        article = {"link": _FEED_URL, "title": "A", "summary": ""}
        with patch("trending.socket.gethostbyname", return_value="127.0.0.1"):
            with patch("trending.requests.get") as mock_get:
                result = enrich(article)
        mock_get.assert_not_called()
        # enrich() returns the original article unchanged — no new summary
        self.assertIs(result, article)


# ---------------------------------------------------------------------------
# write_post() — validation and retry
# ---------------------------------------------------------------------------

# A long, valid post: > 100 chars, < 1300 chars, no URL, exactly 3 hashtags.
_VALID_POST = (
    "AI language models are reshaping how professionals work and communicate.\n\n"
    "Insight 1: context windows now span entire codebases. "
    "Insight 2: reasoning models outperform humans on structured benchmarks. "
    "Insight 3: inference costs have dropped 100× in two years. "
    "Takeaway: ignoring this shift is no longer an option for knowledge workers. "
    "What workflow have you changed most because of AI? #AI #MachineLearning #FutureOfWork"
)

# A post containing a URL (should be rejected by validation).
_URL_POST = (
    "Check out https://openai.com for the latest AI news. "
    "This post is long enough to exceed the 100 character minimum requirement. "
    "#AI #Tech #ML"
)

# A post with 8 hashtags (> 5 → rejected).
_HASHTAG_POST = (
    "x" * 110 + " #AI #ML #LLM #GPT #DeepLearning #NeuralNet #OpenAI #DeepMind"
)


class TestWritePost(_TempDir):

    def test_retry_on_short_first_reply(self):
        """
        First call_gemini reply is too short → write_post raises.
        write_post_with_retry calls call_gemini a second time with a prompt
        that mentions the previous failure, and returns the valid result.
        """
        short = json.dumps({"post": "Too short.", "image_prompt": "img"})
        valid = json.dumps({"post": _VALID_POST, "image_prompt": "img2"})

        with patch("post.call_gemini", side_effect=[short, valid]) as mock_cg:
            text, img = post.write_post_with_retry(
                {"title": "T", "summary": "S", "link": "https://x.com/a"}
            )

        self.assertEqual(mock_cg.call_count, 2)
        self.assertEqual(img, "img2")
        self.assertIn("https://x.com/a", text)   # source link appended
        second_prompt = mock_cg.call_args_list[1][0][0]
        self.assertIn("PREVIOUS ATTEMPT FAILED", second_prompt)

    def test_both_replies_invalid_raises_valueerror(self):
        """Both replies fail validation → ValueError propagates, nothing recorded."""
        short = json.dumps({"post": "Too short.", "image_prompt": "img"})
        with patch("post.call_gemini", return_value=short):
            with self.assertRaises(ValueError):
                post.write_post_with_retry(
                    {"title": "T", "summary": "S", "link": "https://x.com/b"}
                )
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(data, [])

    def test_url_in_post_triggers_retry(self):
        """
        First reply contains a URL → rejected.
        Second reply is valid → returned successfully.
        Second prompt must mention the failure.
        """
        url_reply = json.dumps({"post": _URL_POST, "image_prompt": "img"})
        valid_reply = json.dumps({"post": _VALID_POST, "image_prompt": "img2"})

        with patch("post.call_gemini", side_effect=[url_reply, valid_reply]) as mock_cg:
            text, img = post.write_post_with_retry(
                {"title": "T", "summary": "S", "link": "https://x.com/c"}
            )

        self.assertEqual(mock_cg.call_count, 2)
        second_prompt = mock_cg.call_args_list[1][0][0]
        self.assertIn("PREVIOUS ATTEMPT FAILED", second_prompt)
        # Returned post must not contain the injected URL
        self.assertNotIn("https://openai.com", text)

    def test_url_both_retries_fail_raises(self):
        """Both replies contain URLs → ValueError raised."""
        url_reply = json.dumps({"post": _URL_POST, "image_prompt": "img"})
        with patch("post.call_gemini", return_value=url_reply):
            with self.assertRaises(ValueError) as ctx:
                post.write_post_with_retry(
                    {"title": "T", "summary": "S", "link": "https://x.com/d"}
                )
        self.assertIn("URL", str(ctx.exception))

    def test_eight_hashtags_rejected(self):
        """Post with 8 hashtags (> 5 limit) raises ValueError on both attempts."""
        hashtag_reply = json.dumps({"post": _HASHTAG_POST, "image_prompt": "img"})
        with patch("post.call_gemini", return_value=hashtag_reply):
            with self.assertRaises(ValueError) as ctx:
                post.write_post_with_retry(
                    {"title": "T", "summary": "S", "link": "https://x.com/e"}
                )
        self.assertIn("hashtag", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# Gemini 429 — no retry
# ---------------------------------------------------------------------------

class TestGemini429(_TempDir):
    @patch("gemini_helper.time.sleep")
    @patch("gemini_helper.requests.post")
    def test_429_breaks_immediately_no_sleep(self, mock_post, mock_sleep):
        import gemini_helper

        resp = MagicMock()
        resp.status_code = 429
        resp.text = "Too Many Requests"
        mock_post.return_value = resp

        with patch.dict(os.environ, {"GEMINI_API_KEY": "X", "GROQ_API_KEY": ""}):
            with self.assertRaises(RuntimeError):
                gemini_helper.call_gemini("hello")

        # One call per configured Gemini model, zero sleeps
        self.assertEqual(mock_post.call_count, len(gemini_helper.GEMINI_MODELS))
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# preflight()
# ---------------------------------------------------------------------------

class TestPreflight(_TempDir):
    @patch("post.requests.get")
    def test_401_raises(self, mock_get):
        mock_get.return_value.status_code = 401
        with patch.dict(os.environ, {"LINKEDIN_TOKEN": "A", "LINKEDIN_AUTHOR": "B"}):
            with self.assertRaises(RuntimeError):
                post.preflight()


# ---------------------------------------------------------------------------
# Publish lifecycle — real exception classes
# ---------------------------------------------------------------------------

_ARTICLE = {
    "title": "Big AI News",
    "summary": "X" * 200,
    "link": "https://example.com/story",
    "score": 10,
}


class TestPublishLifecycle(_TempDir):
    """
    Only helpers (upload_image, publish, preflight, get_trending,
    write_post_with_retry, make_poster) are mocked.
    The function under test is post.main().
    All exceptions are real requests exception classes.
    """

    def _live_run(self, extra_mocks, img_bytes: bytes = b"x" * 11_000):
        """
        Run post.main() in live mode (DRY_RUN=0).

        extra_mocks: dict of {patch_target: return_value_or_exception_instance}
        img_bytes: content written to image.jpg; pass b'' to skip file creation.
        """
        if img_bytes:
            Path("image.jpg").write_bytes(img_bytes)

        env = {
            "DRY_RUN": "0",
            "LINKEDIN_TOKEN": "tok",
            "LINKEDIN_AUTHOR": "urn:li:person:123",
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env))
            stack.enter_context(patch("post.get_trending", return_value=[_ARTICLE]))
            stack.enter_context(
                patch("post.write_post_with_retry", return_value=("Post " * 30, "img"))
            )
            stack.enter_context(patch("post.make_poster", return_value="image.jpg"))
            stack.enter_context(patch("post.preflight"))
            for target, effect in extra_mocks.items():
                m = stack.enter_context(patch(target))
                if isinstance(effect, BaseException):
                    m.side_effect = effect
                else:
                    m.return_value = effect
            post.main()

    # -- success path --

    def test_success_records_story(self):
        self._live_run({
            "post.upload_image": "urn:li:image:123",
            "post.publish": "post-id-xyz",
        })
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(len(data), 1)
        self.assertIn("example.com", data[0]["link"])

    # -- HTTPError unrecords --

    def test_publish_httperror_removes_record(self):
        """requests.HTTPError from publish() → record removed."""
        with self.assertRaises(requests.HTTPError):
            self._live_run({
                "post.upload_image": "urn:li:image:123",
                "post.publish": _make_http_error(422),
            })
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(len(data), 0)

    def test_upload_httperror_removes_record(self):
        """requests.HTTPError from upload_image() → record removed."""
        with self.assertRaises(requests.HTTPError):
            self._live_run({
                "post.upload_image": _make_http_error(400),
            })
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(len(data), 0)

    # -- RequestException (connection / timeout) keeps record --

    def test_publish_connection_error_keeps_record(self):
        """requests.ConnectionError from publish() → record kept, warning printed."""
        buf = io.StringIO()
        with self.assertRaises(requests.ConnectionError):
            with contextlib.redirect_stdout(buf):
                self._live_run({
                    "post.upload_image": "urn:li:image:123",
                    "post.publish": requests.ConnectionError("refused"),
                })
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(len(data), 1)
        self.assertIn("Warning", buf.getvalue())

    def test_publish_timeout_keeps_record(self):
        """requests.Timeout from publish() → record kept, warning printed."""
        buf = io.StringIO()
        with self.assertRaises(requests.Timeout):
            with contextlib.redirect_stdout(buf):
                self._live_run({
                    "post.upload_image": "urn:li:image:123",
                    "post.publish": requests.Timeout("timed out"),
                })
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(len(data), 1)
        self.assertIn("Warning", buf.getvalue())

    # -- missing image never recorded --

    def test_missing_image_nothing_recorded(self):
        """
        make_poster returns a path that does not exist on disk.
        The image pre-check must raise RuntimeError BEFORE record_posted is called.
        posted.json must stay empty.
        """
        with self.assertRaises(RuntimeError) as ctx:
            self._live_run(
                extra_mocks={},
                img_bytes=b"",   # don't create image.jpg
            )
        self.assertIn("Image file missing", str(ctx.exception))
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(len(data), 0)

    def test_small_image_nothing_recorded(self):
        """
        Image file exists but is < 10 KB → pre-check raises, nothing recorded.
        """
        with self.assertRaises(RuntimeError):
            self._live_run(
                extra_mocks={},
                img_bytes=b"x" * 100,   # 100 bytes, well below 10 KB
            )
        data = json.loads(Path("posted.json").read_text("utf-8"))
        self.assertEqual(len(data), 0)


if __name__ == "__main__":
    unittest.main()
