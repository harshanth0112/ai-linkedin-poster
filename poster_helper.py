"""
Poster helper — bridges the post.py interface to image generation in thumb.py,
supporting classic thumbnails, story cards, and relatable meme-style visuals.
"""
from image_helper import make_image
import thumb


def make_poster(article, image_prompt, path="image.jpg", captions=None):
    """
    Generate a poster/meme image for the given article and return the saved file path.
    If captions dict is provided (from write_poster_captions), passes it along to thumb.
    """
    headline = article.get("title")
    return thumb.make_image(image_prompt, headline=headline, path=path, captions=captions)
