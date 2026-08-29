import hashlib
import math
import re
import unicodedata
from collections import Counter
from typing import List, Tuple

TERM_PATTERN = re.compile(r"\w+(?:[’'-]\w+)*|[^\w\s]", re.UNICODE)
SPACE_GROUP_PATTERN = re.compile(r"\S+", re.UNICODE)


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


def sparse_vector(text: str) -> Tuple[List[int], List[float]]:
    weighted_counts: Counter[int] = Counter()
    for term, weight in lexical_terms(text):
        weighted_counts[term_index(term)] += weight
    if not weighted_counts:
        return [], []

    weighted = {index: 1.0 + math.log(value) for index, value in weighted_counts.items()}
    norm = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
    indices = sorted(weighted)
    values = [weighted[index] / norm for index in indices]
    return indices, values
