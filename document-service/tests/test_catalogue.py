"""Catalogue lookup: normalised candidates, safe apply, and graceful failure."""
import io

import pytest
from PIL import Image

import catalogue


def png(width=40, height=60):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "#336699").save(out, format="PNG")
    return out.getvalue()


HIT = {
    "key": "/works/OL1W", "title": "The Stranger", "author_name": ["Albert Camus", "Stuart Gilbert"],
    "first_publish_year": 1942, "publisher": ["Penguin"], "isbn": ["0679720200", "9780679720201"],
    "number_of_pages_median": 123, "language": ["eng"], "subject": ["Fiction", "fiction", "Philosophy"],
    "cover_i": 4242,
}


@pytest.fixture(autouse=True)
def clear_cache():
    catalogue._cache.clear()


def test_normalise_builds_a_safe_candidate_and_ignores_unreliable_fields():
    candidate = catalogue.normalise(HIT)
    assert candidate["author"] == "Albert Camus, Stuart Gilbert"
    assert candidate["isbn_13"] == "9780679720201" and candidate["isbn_10"] == "0679720200"
    assert candidate["language"] == "en" and candidate["genres"] == ["Fiction", "Philosophy"]
    assert candidate["cover_id"] == "4242" and candidate["page_count"] == 123
    # A work that lists several editions' languages gives no trustworthy one.
    assert catalogue.normalise({**HIT, "language": ["eng", "fre"]})["language"] is None
    assert catalogue.normalise({"key": "/works/x"}) is None
    assert catalogue.normalise({**HIT, "first_publish_year": 99999})["published_year"] is None


def test_noisy_catalogue_subjects_are_not_offered_as_genres():
    subjects = ["Dune (Imaginary place)", "Fiction", "Fiction, science fiction, general", "New York Times reviewed",
                "Science fiction", "Science-fiction", "nyt:mass-market-monthly=2021", "American literature"]
    assert catalogue.normalise({**HIT, "subject": subjects})["genres"] == ["Fiction", "Science fiction", "American literature"]


def test_search_caches_and_isbn_search_keeps_the_isbn_the_reader_holds(monkeypatch):
    calls = []
    monkeypatch.setattr(catalogue, "_fetch_json", lambda params: calls.append(params) or {"docs": [HIT]})
    assert catalogue.search("camus stranger")[0]["title"] == "The Stranger"
    catalogue.search("camus stranger")
    assert len(calls) == 1
    assert catalogue.by_isbn("9780140449136")[0]["isbn_13"] == "9780140449136"
    assert catalogue.search("x") == []


def test_offline_mode_never_touches_the_network(monkeypatch):
    monkeypatch.setenv("OFFLINE", "1")
    with pytest.raises(catalogue.CatalogueError):
        catalogue.search("anything at all")


def test_routes_validate_and_report_provider_outage(service, monkeypatch):
    _, client, _ = service
    monkeypatch.setattr(catalogue, "_fetch_json", lambda params: {"docs": [HIT]})
    assert client.get("/catalogue/search", params={"q": "camus"}).json()["results"][0]["id"] == "/works/OL1W"
    assert client.get("/catalogue/isbn/978-0-679-72020-1").json()["results"][0]["isbn_13"] == "9780679720201"
    assert client.get("/catalogue/isbn/9780679720202").status_code == 422
    assert client.get("/catalogue/cover", params={"id": "abc"}).status_code == 422

    def down(params):
        raise catalogue.CatalogueError("Open Library could not be reached.")
    catalogue._cache.clear()
    monkeypatch.setattr(catalogue, "_fetch_json", down)
    response = client.get("/catalogue/search", params={"q": "camus"})
    assert response.status_code == 502 and "could not be reached" in response.json()["detail"]


def test_cover_proxy_reencodes_and_rejects_bad_images(service, monkeypatch):
    _, client, _ = service
    monkeypatch.setattr(catalogue, "fetch_cover", lambda cover_id: png())
    response = client.get("/catalogue/cover", params={"id": "4242"})
    assert response.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(response.content)).format == "JPEG"
    monkeypatch.setattr(catalogue, "fetch_cover", lambda cover_id: b"<html>not an image</html>")
    assert client.get("/catalogue/cover", params={"id": "4242"}).status_code == 502


def test_apply_changes_only_the_ticked_fields_and_never_personal_ones(service, monkeypatch):
    module, client, _ = service
    book = client.post("/owned-books", json={"title": "Stranger", "author": "Me"}).json()["book"]
    doc_id = book["document_id"]
    client.patch(f"/documents/{doc_id}", json={"rating": 4, "review": "Loved it"})
    monkeypatch.setattr(catalogue, "fetch_cover", lambda cover_id: png())

    applied = client.post(f"/documents/{doc_id}/metadata-apply", json={
        "fields": {"title": "The Stranger", "publisher": "Penguin", "isbn_13": "9780679720201"},
        "cover_id": "4242", "provider_id": "/works/OL1W",
    })
    assert applied.status_code == 200
    saved = applied.json()
    assert saved["title"] == "The Stranger" and saved["publisher"] == "Penguin" and saved["isbn_13"] == "9780679720201"
    assert saved["author"] == "Me"  # not ticked, so untouched
    assert saved["rating"] == 4 and saved["review"] == "Loved it"
    assert saved["metadata_source"] == "Open Library" and saved["metadata_source_id"] == "/works/OL1W"
    assert (module.THUMBNAILS_DIR / f"{doc_id}-custom.jpg").exists()

    refused = client.post(f"/documents/{doc_id}/metadata-apply", json={"fields": {"rating": 1}})
    assert refused.status_code == 422
    assert client.post(f"/documents/{doc_id}/metadata-apply", json={"fields": {"isbn_13": "123"}}).status_code == 422
    assert client.get(f"/documents/{doc_id}").json()["rating"] == 4
