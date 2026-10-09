"""
Poster helper — bridges the new post.py interface to the existing image generation in thumb.py.

post.py calls:   make_poster(article, image_prompt) -> path
thumb.py offers:  make_image(prompt, headline=None, path="image.jpg") -> path
"""
from image_helper import make_image


def make_poster(article, image_prompt, path="image.jpg"):
    """Generate a poster image for the given article and return the saved file path."""
    headline = article.get("title")
    return make_image(image_prompt, headline=headline, path=path)
