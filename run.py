"""Run the matador dashboard standalone.

uv run python seed.py            # populate test data
uv run uvicorn run:app --reload  # then open http://localhost:8000
"""

from matador import create_app

app = create_app(
    ["emails", "billing", "media", "notifications"],
    url="redis://localhost:6379",
)
