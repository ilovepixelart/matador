"""matador - an async dashboard for toro queues."""

from importlib.metadata import version

from .app import create_app

__all__ = ["create_app"]
# Asked, not repeated: `pyproject.toml` holds the number and `uv version --bump`
# edits it there, so this module has nothing to keep in step.
__version__ = version("matador-dashboard")
