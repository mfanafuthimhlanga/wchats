"""
#135: the api serves the widget bundle at /wchats (main.py mount).

    1. all four bundle files serve with content and no auth
    2. a file outside the bundle is 404
    3. the served loader is byte-identical to the SHA-gated folder the sync
       script maintains, so the mount points at the source of truth and not
       at some other copy
"""

from pathlib import Path

from httpx import ASGITransport, AsyncClient

# conftest.py sets required env vars before any app import
from app.main import app

STATIC_WCHATS = Path(__file__).parents[2] / "static" / "wchats"
BUNDLE_FILES = ("widget.js", "index.html", "widget.css", "widget.iife.js")


class TestWidgetStaticMount:
    async def test_the_four_bundle_files_serve(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for name in BUNDLE_FILES:
                response = await client.get(f"/wchats/{name}")
                assert response.status_code == 200, name
                assert len(response.content) > 0, name

    async def test_a_file_outside_the_bundle_is_404(self):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/wchats/not-a-bundle-file.js")
        assert response.status_code == 404

    async def test_served_loader_matches_the_synced_folder(self):
        expected = (STATIC_WCHATS / "widget.js").read_bytes()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/wchats/widget.js")
        assert response.content == expected
