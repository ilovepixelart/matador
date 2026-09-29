"""Unit: the queue names create_app() can route."""

import pytest

from matador import create_app


def test_a_queue_name_with_a_slash_is_refused_up_front():
    """toro allows "/" in a queue name; matador routes a name as one path segment,
    and Starlette refuses to build a URL with a separator in it. The first page
    that drew the sidebar answered 500 for every queue, and /queues/a/b routed
    nowhere. The name is refused where the app is built, naming the queue."""
    with pytest.raises(ValueError, match="'a/b'"):
        create_app(["a/b"])


def test_plain_names_build_the_app():
    create_app(["emails", "video-transcode"])
