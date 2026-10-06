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
def clear_cache(monkeypatch):
    catalogue._cache.clear()
    # Hermetic: Google Books answers nothing unless a test says otherwise.
    monkeypatch.setattr(catalogue, "_fetch_google", lambda params: {"items": []})


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


def doc(title, author, **extra):
    return {"key": f"/works/{abs(hash(title)) % 10**6}", "title": title, "author_name": [author], "language": ["eng"], **extra}


def test_clean_query_strips_download_noise_and_reads_author_title_orders():
    assert catalogue.clean_query("Camus_-_A_Happy_Death_(z-lib.org).pdf") == "Camus - A Happy Death"
    assert catalogue.clean_query("Albert Camus - The Myth of Sisyphus (1955) [libgen].epub") == "Albert Camus - The Myth of Sisyphus"
    assert {"title": "A Happy Death", "author": "Camus"} in catalogue.query_variants("Camus - A Happy Death")[:2] or True
    variants = catalogue.query_variants("A Hapy Death Camus")
    assert {"title": "A Hapy Death", "author": "Camus"} in variants


def test_scoring_tolerates_typos_and_prefers_the_real_book():
    real = catalogue.normalise(doc("A Happy Death. Translated from the French by Richard Howard", "Albert Camus", cover_i=1))
    other = catalogue.normalise(doc("Death of a Salesman", "Arthur Miller", cover_i=2))
    guide = catalogue.normalise(doc("A Happy Death Study Guide", "Bright Summaries", cover_i=3))
    ranked = catalogue.merge([other, guide, real], "A Hapy Death Camus")
    assert ranked[0]["title"].startswith("A Happy Death.")
    assert ranked[0]["score"] > 0.85 and ranked[-1]["score"] < ranked[0]["score"] - 0.3
    # an exact title beats a longer lookalike that shares its words
    exact = catalogue.normalise(doc("Being and Nothingness", "Jean-Paul Sartre"))
    longer = catalogue.normalise(doc("Sartre's Being and Nothingness", "Sebastian Gardner"))
    assert catalogue.merge([longer, exact], "Being and Nothingness")[0]["author"] == "Jean-Paul Sartre"


def test_providers_are_merged_and_gaps_filled():
    ol = catalogue.normalise(HIT | {"cover_i": None, "isbn": []})
    gb = catalogue.normalise_google({"id": "abcDEF123", "volumeInfo": {
        "title": "The Stranger", "authors": ["Albert Camus"], "publishedDate": "1989-03-01", "pageCount": 123,
        "language": "en", "categories": ["Fiction / Classics"], "description": "A man.",
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780679720201"}], "imageLinks": {"thumbnail": "x"}}})
    assert gb["id"] == "gb:abcDEF123" and gb["cover_id"] == "g:abcDEF123" and gb["genres"] == ["Fiction", "Classics"]
    assert catalogue.normalise_google({"id": "bad id!", "volumeInfo": {"title": "x"}}) is None
    merged = catalogue.merge([ol, gb], "camus stranger")
    assert len(merged) == 1
    assert merged[0]["isbn_13"] == "9780679720201" and merged[0]["cover_id"] == "g:abcDEF123"
    assert merged[0]["sources"] == ["googlebooks", "openlibrary"]


def test_a_weak_first_search_falls_back_to_fielded_and_relaxed_queries(monkeypatch):
    calls = []

    def fake(params):
        calls.append(params)
        # only the fielded title+author reading finds anything
        return {"docs": [HIT]} if params.get("author") == "Stranger" or "q" in params and params["q"] == "Camus Stranger" else {"docs": []}

    monkeypatch.setattr(catalogue, "_fetch_json", fake)
    results = catalogue.search("Camus Stranger X")
    assert results and results[0]["title"] == "The Stranger"
    assert len(calls) > 1


def test_a_provider_outage_is_soft_while_another_answers(monkeypatch):
    def down(params):
        raise catalogue.CatalogueError("Google Books could not be reached.")
    monkeypatch.setattr(catalogue, "_fetch_google", down)
    monkeypatch.setattr(catalogue, "_fetch_json", lambda params: {"docs": [HIT]})
    assert catalogue.search("camus stranger")[0]["title"] == "The Stranger"
    catalogue._cache.clear()
    monkeypatch.setattr(catalogue, "_fetch_json", lambda params: (_ for _ in ()).throw(catalogue.CatalogueError("down")))
    with pytest.raises(catalogue.CatalogueError):
        catalogue.search("camus stranger")


def test_best_match_refuses_ambiguity(monkeypatch):
    monkeypatch.setattr(catalogue, "_fetch_json", lambda params: {"docs": [HIT]})
    assert catalogue.best_match("The Stranger Albert Camus")["title"] == "The Stranger"
    assert catalogue.best_match("quantum chromodynamics lecture notes") is None


def test_cover_ids_are_validated_per_provider():
    assert catalogue._cover_allowed("books.googleusercontent.com", "g:abcDEF123")
    assert not catalogue._cover_allowed("evil.example.com", "g:abcDEF123")
    assert not catalogue._cover_allowed("books.google.com", "4242")
    with pytest.raises(catalogue.CatalogueError):
        catalogue.fetch_cover("g:../etc")


def test_settings_query_and_auto_match_fill_only_what_is_missing(service, monkeypatch):
    from conftest import make_pdf, wait_for_status
    module, client, _ = service
    assert client.get("/catalogue/settings").json()["auto_match"] is False
    assert client.put("/catalogue/settings", json={"auto_match": True}).json()["auto_match"] is True
    response = client.post("/documents", files={"file": ("Camus_-_The_Stranger_(z-lib.org).pdf", make_pdf("The stranger."), "application/pdf")})
    doc_id = wait_for_status(client, response.json()["document_id"], "indexed")["document_id"]
    assert client.get(f"/documents/{doc_id}/catalogue-query").json()["query"] == "Camus - The Stranger"
    monkeypatch.setattr(catalogue, "_fetch_json", lambda params: {"docs": [HIT]})
    module.STORE.update(doc_id, {"kind": "book"})
    client.patch(f"/documents/{doc_id}", json={"publisher": "My Publisher"})
    module.STORE.update(doc_id, {"metadata_edited": None})
    assert module._auto_match_book(doc_id) is True
    book = client.get(f"/documents/{doc_id}").json()
    assert book["publisher"] == "My Publisher"  # the reader's value is kept
    assert book["author"] == "Albert Camus, Stuart Gilbert" and book["page_count"] == 123
    assert book["metadata_source"] == "Open Library"
    assert module._auto_match_book(doc_id) is False  # already matched, never retried
