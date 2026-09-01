import hashlib
import os
import re
import unicodedata
from collections import Counter
from typing import Dict, List, Tuple

TERM_PATTERN = re.compile(r"\w+(?:[’'-]\w+)*|[^\w\s]", re.UNICODE)
SPACE_GROUP_PATTERN = re.compile(r"\S+", re.UNICODE)

# BM25 parameters. k1 controls how fast term-frequency saturates; b controls how
# hard long passages are penalised. avgdl is the reference passage length (in
# summed term weight) — chunks are token-budgeted so a fixed value tracks the
# true corpus average closely; tune it if chunk sizes change a lot. The rarity
# weight (IDF) is supplied by Qdrant's sparse-vector IDF modifier at query time.
BM25_K1 = float(os.environ.get("LEXICAL_K1", "1.5"))
BM25_B = float(os.environ.get("LEXICAL_B", "0.75"))
BM25_AVGDL = float(os.environ.get("LEXICAL_AVGDL", "180"))


def lexical_terms(text: str) -> List[Tuple[str, float]]:
    """Return weighted lexical terms, retaining mathematical symbols.

    Words provide ordinary exact-match retrieval. Individual symbols and compact
    formula-like groups receive extra weight so queries such as ``∂f/∂x`` or
    ``x² ≥ 4`` survive even when a prose-oriented dense model underweights them.
    """
    normalized = unicodedata.normalize("NFKC", text).casefold()
    terms: List[Tuple[str, float]] = []
    base_tokens = TERM_PATTERN.findall(normalized)
    for token in base_tokens:
        if token.isalnum() or token.replace("_", "").isalnum():
            terms.append((f"term:{token}", 1.0))
        else:
            terms.append((f"symbol:{token}", 2.0))

    word_tokens = [token for token in base_tokens if any(character.isalnum() for character in token)]
    for left, right in zip(word_tokens, word_tokens[1:]):
        terms.append((f"bigram:{left}\u241f{right}", 0.5))

    for group in SPACE_GROUP_PATTERN.findall(normalized):
        if len(group) <= 80 and any(not character.isalnum() and character != "_" for character in group):
            terms.append((f"formula:{group}", 2.5))
    return terms


def term_index(term: str) -> int:
    return int.from_bytes(hashlib.blake2s(term.encode("utf-8"), digest_size=4).digest(), "big")


def _term_frequencies(text: str) -> Dict[int, float]:
    """Weighted occurrence count per hashed term (symbols/formulae count extra)."""
    counts: Counter = Counter()
    for term, weight in lexical_terms(text):
        counts[term_index(term)] += weight
    return counts


def bm25_document_vector(text: str) -> Tuple[List[int], List[float]]:
    """Sparse vector for a stored passage.

    Each value is the BM25 term-frequency component:
    ``tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avgdl))`` — frequency that
    saturates and is normalised for passage length. Qdrant's IDF modifier
    multiplies in the across-library rarity weight when a query hits the term.
    """
    counts = _term_frequencies(text)
    if not counts:
        return [], []
    doc_len = sum(counts.values())
    length_norm = BM25_K1 * (1.0 - BM25_B + BM25_B * doc_len / BM25_AVGDL)
    indices = sorted(counts)
    values = [counts[i] * (BM25_K1 + 1.0) / (counts[i] + length_norm) for i in indices]
    return indices, values


def bm25_query_vector(text: str) -> Tuple[List[int], List[float]]:
    """Sparse vector for a query: the weighted presence of each term. IDF is
    applied by the collection modifier; symbol and formula terms keep the extra
    weight that makes an exact ``∂f/∂x`` match count for more than a word."""
    counts = _term_frequencies(text)
    if not counts:
        return [], []
    indices = sorted(counts)
    return indices, [counts[i] for i in indices]
