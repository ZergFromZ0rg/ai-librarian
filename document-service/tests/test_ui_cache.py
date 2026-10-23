from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient


def test_ui_pages_are_rechecked_and_hashed_assets_are_kept(service, tmp_path):
    """An update replaces the hashed scripts, so index.html must never be reused
    without asking the server, while the hashed files themselves never change."""
    module, _client, _indexed = service
    (tmp_path / "ui" / "assets").mkdir(parents=True)
    (tmp_path / "ui" / "pdfjs" / "cmaps").mkdir(parents=True)
    (tmp_path / "ui" / "index.html").write_text("<html></html>")
    (tmp_path / "ui" / "assets" / "index-abc123.js").write_text("console.log(1)")
    (tmp_path / "ui" / "pdfjs" / "cmaps" / "a.bcmap").write_bytes(b"\x00")
    client = TestClient(Starlette(routes=[Mount("/", app=module.UiFiles(directory=tmp_path / "ui", html=True))]))

    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/index.html").headers["cache-control"] == "no-cache"
    assert client.get("/pdfjs/cmaps/a.bcmap").headers["cache-control"] == "no-cache"
    asset = client.get("/assets/index-abc123.js")
    assert asset.status_code == 200
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

    # Revalidation stays cheap: the same page answers 304 to its own ETag.
    page = client.get("/")
    assert client.get("/", headers={"If-None-Match": page.headers["etag"]}).status_code == 304
    # A file that is not there is still a plain 404, not a page.
    assert client.get("/assets/index-gone.js").status_code == 404
