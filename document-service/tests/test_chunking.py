import pytest

from chunking import chunk_document


def test_chunks_never_exceed_max_size_for_long_unbroken_text():
    pages = [{"page": 1, "text": "x" * 2_500}]

    chunks = chunk_document(pages, max_size=800, overlap=100)

    assert len(chunks) == 4
    assert all(len(text) <= 800 for _, text in chunks)


def test_chunks_preserve_page_provenance_and_overlap():
    pages = [
        {"page": 1, "text": "First paragraph."},
        {"page": 2, "text": "Second paragraph is long enough to force another chunk."},
    ]

    chunks = chunk_document(pages, max_size=50, overlap=10)

    assert chunks[0][0] == 1
    assert chunks[-1][0] in {1, 2}
    assert all(len(text) <= 50 for _, text in chunks)


@pytest.mark.parametrize("max_size,overlap", [(0, 0), (100, -1), (100, 100)])
def test_invalid_chunk_configuration_is_rejected(max_size, overlap):
    with pytest.raises(ValueError):
        chunk_document([{"page": 1, "text": "text"}], max_size=max_size, overlap=overlap)
