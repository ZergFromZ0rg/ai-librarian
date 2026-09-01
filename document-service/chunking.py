import logging
import re
import threading
from dataclasses import dataclass, replace
from typing import Dict, Iterable, List, Sequence

logger = logging.getLogger("ai_librarian.chunking")

BLOCK_TYPES = {"paragraph", "heading", "equation", "table", "caption"}
TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
HEADING_PATTERN = re.compile(r"^\s{0,3}#{1,6}\s+\S")
CAPTION_PATTERN = re.compile(
    r"^\s*(?:\*{0,2})?(?:table|figure|fig\.?|equation|eq\.?)\s+"
    r"(?:[A-Z]?\d+(?:[.:-]\d+)*|[IVXLCDM]+)\b",
    re.IGNORECASE,
)
TABLE_SEPARATOR_PATTERN = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
EQUATION_OPENERS = ("$$", "\\[", "\\begin{equation", "\\begin{align", "\\begin{gather")
EQUATION_CLOSERS = {
    "$$": "$$",
    "\\[": "\\]",
    "\\begin{equation": "\\end{equation",
    "\\begin{align": "\\end{align",
    "\\begin{gather": "\\end{gather",
}
MATH_SYMBOL_PATTERN = re.compile(r"[=<>≤≥≠≈∑∏∫√∞±∓×÷∂∇^_{}\\]")
TERMINAL_PATTERN = re.compile(r"[.!?][\]\)\"'”’*]*\s*$")


@dataclass(frozen=True)
class Block:
    block_id: str
    type: str
    text: str
    page_start: int
    page_end: int

    def as_dict(self) -> Dict:
        return {
            "block_id": self.block_id,
            "type": self.type,
            "text": self.text,
            "page": self.page_start,
            "page_end": self.page_end,
        }


@dataclass(frozen=True)
class Unit:
    blocks: Sequence[Block]
    protected_type: str | None = None

    @property
    def text(self) -> str:
        return join_blocks(self.blocks)

    @property
    def page_start(self) -> int:
        return min(block.page_start for block in self.blocks)

    @property
    def page_end(self) -> int:
        return max(block.page_end for block in self.blocks)


_tokenizer = None
_tokenizer_lock = threading.Lock()
_tokenizer_unavailable = False


def _get_tokenizer():
    """The embedding model's own fast tokenizer, or None to fall back to the
    regex estimate. Only the tokenizer is loaded, never the model itself, so
    this stays cheap enough to run at upload time.
    """
    global _tokenizer, _tokenizer_unavailable
    if _tokenizer is not None or _tokenizer_unavailable:
        return _tokenizer
    with _tokenizer_lock:
        if _tokenizer is None and not _tokenizer_unavailable:
            try:
                from transformers import AutoTokenizer

                from embeddings import DEFAULT_MODEL

                tokenizer = AutoTokenizer.from_pretrained(DEFAULT_MODEL)
                if not getattr(tokenizer, "is_fast", False):
                    raise RuntimeError("a fast tokenizer is required for offsets")
                _tokenizer = tokenizer
            except Exception:
                _tokenizer_unavailable = True
                logger.warning(
                    "Embedding tokenizer unavailable; falling back to the regex "
                    "token estimate. Chunk sizes will be approximate.",
                    exc_info=True,
                )
    return _tokenizer


def _regex_token_spans(text: str) -> List[tuple[int, int]]:
    """Conservative, dependency-free token-like character spans.

    Used only when the real tokenizer cannot be loaded. Long alphanumeric runs
    (URLs, identifiers, damaged PDF words) are split so one of them is not
    mistaken for a single token.
    """
    spans: List[tuple[int, int]] = []
    for match in TOKEN_PATTERN.finditer(text):
        start, end = match.span()
        if match.group(0).isalnum() and end - start > 12:
            for part_start in range(start, end, 8):
                spans.append((part_start, min(part_start + 8, end)))
        else:
            spans.append((start, end))
    return spans


def _token_spans(text: str) -> List[tuple[int, int]]:
    """Character spans of the model's tokens, for splitting text on a token
    boundary. Exact when the tokenizer is available, estimated otherwise."""
    tokenizer = _get_tokenizer()
    if tokenizer is None:
        return _regex_token_spans(text)
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)[
        "offset_mapping"
    ]
    return [(start, end) for start, end in offsets if end > start]


def count_tokens(text: str) -> int:
    """The number of tokens the embedding model will see for `text`."""
    tokenizer = _get_tokenizer()
    if tokenizer is None:
        return len(_regex_token_spans(text))
    return len(tokenizer(text, add_special_tokens=False)["input_ids"])


def normalize_for_embedding(markdown: str) -> str:
    """Remove presentation-only Markdown while preserving words and symbols."""
    text = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "").replace("~~", "")
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.count("|") >= 2 and not stripped.startswith("$$")


def _display_math_opener(line: str) -> str | None:
    stripped = line.strip()
    return next((opener for opener in EQUATION_OPENERS if stripped.startswith(opener)), None)


def _looks_like_plain_equation(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 2_000:
        return False
    if any(marker in stripped for marker in ("$$", "\\[", "\\]", "\\begin{", "\\end{")):
        return True
    words = re.findall(r"[A-Za-z]{3,}", stripped)
    symbols = MATH_SYMBOL_PATTERN.findall(stripped)
    lines = [line for line in stripped.splitlines() if line.strip()]
    return bool(symbols) and len(lines) <= 12 and len(symbols) >= max(1, len(words) // 2)


def _classify_text_block(text: str) -> str:
    stripped = text.strip()
    if HEADING_PATTERN.match(stripped):
        return "heading"
    if CAPTION_PATTERN.match(stripped):
        return "caption"
    if _looks_like_plain_equation(stripped):
        return "equation"
    return "paragraph"


def _parse_page(page_number: int, markdown: str) -> List[Block]:
    lines = markdown.splitlines()
    raw_blocks: List[tuple[str, str]] = []
    paragraph: List[str] = []
    index = 0

    def flush_paragraph() -> None:
        text = "\n".join(paragraph).strip()
        paragraph.clear()
        if text:
            raw_blocks.append((_classify_text_block(text), text))

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            index += 1
            continue

        opener = _display_math_opener(line)
        if opener:
            flush_paragraph()
            equation_lines = [line]
            closer = EQUATION_CLOSERS[opener]
            if stripped != opener and closer in stripped[len(opener) :]:
                raw_blocks.append(("equation", line.strip()))
                index += 1
                continue
            index += 1
            while index < len(lines):
                equation_lines.append(lines[index])
                if closer in lines[index]:
                    index += 1
                    break
                index += 1
            raw_blocks.append(("equation", "\n".join(equation_lines).strip()))
            continue

        if stripped.lower().startswith("<table"):
            flush_paragraph()
            table_lines = [line]
            index += 1
            while index < len(lines):
                table_lines.append(lines[index])
                if "</table>" in lines[index].lower():
                    index += 1
                    break
                index += 1
            raw_blocks.append(("table", "\n".join(table_lines).strip()))
            continue

        if _looks_like_table_row(line):
            table_lines = []
            cursor = index
            while cursor < len(lines) and _looks_like_table_row(lines[cursor]):
                table_lines.append(lines[cursor])
                cursor += 1
            is_table = len(table_lines) >= 2 and (
                any(TABLE_SEPARATOR_PATTERN.match(candidate) for candidate in table_lines)
                or len(table_lines) >= 3
            )
            if is_table:
                flush_paragraph()
                raw_blocks.append(("table", "\n".join(table_lines).strip()))
                index = cursor
                continue

        if HEADING_PATTERN.match(stripped) or CAPTION_PATTERN.match(stripped):
            flush_paragraph()
            raw_blocks.append((_classify_text_block(stripped), stripped))
            index += 1
            continue

        paragraph.append(line)
        index += 1

    flush_paragraph()
    return [
        Block(
            block_id=f"p{page_number}-b{block_index}",
            type=block_type,
            text=text,
            page_start=page_number,
            page_end=page_number,
        )
        for block_index, (block_type, text) in enumerate(raw_blocks)
    ]


def _continues_on_next_page(previous: Block, current: Block) -> bool:
    if previous.type != "paragraph" or current.type != "paragraph":
        return False
    if current.page_start != previous.page_end + 1:
        return False
    previous_text = previous.text.rstrip()
    current_text = current.text.lstrip()
    if not previous_text or not current_text or TERMINAL_PATTERN.search(previous_text):
        return False
    if previous_text.endswith(("-", "—", ",", ";", ":")):
        return True
    return current_text[0].islower() or bool(
        re.match(r"^(?:and|or|but|because|which|where|therefore|thus|so)\b", current_text, re.I)
    )


def _merge_cross_page_paragraphs(blocks: Sequence[Block]) -> List[Block]:
    merged: List[Block] = []
    for block in blocks:
        if merged and _continues_on_next_page(merged[-1], block):
            previous = merged[-1]
            if previous.text.rstrip().endswith("-") and block.text.lstrip()[:1].islower():
                text = previous.text.rstrip()[:-1] + block.text.lstrip()
            else:
                text = f"{previous.text.rstrip()}\n\n{block.text.lstrip()}"
            merged[-1] = replace(previous, text=text, page_end=block.page_end)
        else:
            merged.append(block)
    return merged


def parse_typed_blocks(pages: Sequence[Dict]) -> List[Block]:
    """Parse page Markdown into typed blocks, retaining page provenance."""
    blocks: List[Block] = []
    for fallback_page, page in enumerate(pages, start=1):
        page_number = int(page.get("page") or fallback_page)
        blocks.extend(_parse_page(page_number, page.get("text", "")))
    return _merge_cross_page_paragraphs(blocks)


def join_blocks(blocks: Iterable[Block]) -> str:
    return "\n\n".join(block.text.strip() for block in blocks if block.text.strip()).strip()


def _is_structural_anchor(block: Block) -> bool:
    return block.type in {"equation", "table"}


def bind_structural_context(blocks: Sequence[Block]) -> List[Unit]:
    """Bind equations/tables to their adjacent prose and captions.

    Parsing happens across the whole document, so an equation at the bottom of
    one page can retain explanatory prose from the next page.
    """
    if not blocks:
        return []
    consumed = set()
    protected: Dict[int, Unit] = {}

    for anchor_index, anchor in enumerate(blocks):
        if anchor_index in consumed or not _is_structural_anchor(anchor):
            continue
        member_indices = [anchor_index]
        protected_type = anchor.type

        cursor = anchor_index - 1
        if cursor >= 0 and cursor not in consumed and blocks[cursor].type == "caption":
            member_indices.insert(0, cursor)
            cursor -= 1
        if cursor >= 0 and cursor not in consumed and blocks[cursor].type == "paragraph":
            member_indices.insert(0, cursor)

        cursor = anchor_index + 1
        while cursor < len(blocks) and cursor not in consumed and blocks[cursor].type == anchor.type:
            member_indices.append(cursor)
            cursor += 1
        if cursor < len(blocks) and cursor not in consumed and blocks[cursor].type == "caption":
            member_indices.append(cursor)
            cursor += 1
        if cursor < len(blocks) and cursor not in consumed and blocks[cursor].type == "paragraph":
            member_indices.append(cursor)

        member_indices = sorted(set(member_indices))
        unit = Unit(tuple(blocks[index] for index in member_indices), protected_type)
        protected[min(member_indices)] = unit
        consumed.update(member_indices)

    units: List[Unit] = []
    for index in range(len(blocks)):
        if index in protected:
            units.append(protected[index])
        elif index not in consumed:
            units.append(Unit((blocks[index],)))
    return units


def _split_token_windows(text: str, max_tokens: int, overlap_tokens: int) -> List[str]:
    spans = _token_spans(text)
    if not spans:
        return []
    if len(spans) <= max_tokens:
        return [text.strip()]
    windows = []
    step = max(1, max_tokens - overlap_tokens)
    for start in range(0, len(spans), step):
        end = min(start + max_tokens, len(spans))
        window = text[spans[start][0] : spans[end - 1][1]].strip()
        if window:
            windows.append(window)
        if end == len(spans):
            break
    return windows


def _split_table_windows(text: str, max_tokens: int, overlap_tokens: int) -> List[str]:
    """Split a large GFM table while repeating its header in every child."""
    lines = [line for line in text.splitlines() if line.strip()]
    if (
        len(lines) < 3
        or not _looks_like_table_row(lines[0])
        or not TABLE_SEPARATOR_PATTERN.match(lines[1])
    ):
        return _split_token_windows(text, max_tokens, overlap_tokens)

    header = "\n".join(lines[:2])
    header_tokens = count_tokens(header)
    if header_tokens >= max_tokens:
        return _split_token_windows(text, max_tokens, overlap_tokens)
    row_budget = max_tokens - header_tokens
    windows = []
    current_rows: List[str] = []
    current_tokens = 0

    def flush_rows() -> None:
        nonlocal current_rows, current_tokens
        if current_rows:
            windows.append(f"{header}\n" + "\n".join(current_rows))
        current_rows = []
        current_tokens = 0

    for row in lines[2:]:
        row_tokens = count_tokens(row)
        if row_tokens > row_budget:
            flush_rows()
            for piece in _split_token_windows(row, row_budget, min(overlap_tokens, row_budget - 1)):
                windows.append(f"{header}\n{piece}")
            continue
        if current_rows and current_tokens + row_tokens > row_budget:
            previous_row = current_rows[-1] if overlap_tokens and len(current_rows) > 1 else None
            flush_rows()
            if previous_row and count_tokens(previous_row) + row_tokens <= row_budget:
                current_rows.append(previous_row)
                current_tokens = count_tokens(previous_row)
        current_rows.append(row)
        current_tokens += row_tokens
    flush_rows()
    return _deduplicate_texts(windows)


def _split_block_windows(block: Block, max_tokens: int, overlap_tokens: int) -> List[str]:
    if block.type == "table":
        return _split_table_windows(block.text, max_tokens, overlap_tokens)
    return _split_token_windows(block.text, max_tokens, overlap_tokens)


def _deduplicate_texts(texts: Iterable[str]) -> List[str]:
    unique = []
    seen = set()
    for text in texts:
        cleaned = text.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)
    return unique


def _protected_bridge_units(
    blocks: Sequence[Block],
    anchor_type: str,
    target_tokens: int,
    overlap_tokens: int,
) -> List[str]:
    anchors = [block for block in blocks if block.type == anchor_type]
    if not anchors:
        return []
    anchor_text = join_blocks(anchors)
    anchor_tokens = count_tokens(anchor_text)
    if anchor_tokens >= target_tokens:
        if anchor_type == "table" and len(anchors) == 1:
            return _split_table_windows(anchor_text, target_tokens, overlap_tokens)
        return _split_token_windows(anchor_text, target_tokens, overlap_tokens)

    context_budget = max(1, target_tokens - anchor_tokens - 2)
    context_overlap = min(overlap_tokens, max(0, context_budget - 1))
    bridges: List[str] = [anchor_text]
    first_anchor = next(index for index, block in enumerate(blocks) if block.type == anchor_type)
    last_anchor = max(index for index, block in enumerate(blocks) if block.type == anchor_type)
    prefix = join_blocks(blocks[:first_anchor])
    suffix = join_blocks(blocks[last_anchor + 1 :])

    for context in _split_token_windows(prefix, context_budget, context_overlap):
        bridges.append(f"{context}\n\n{anchor_text}")
    for context in _split_token_windows(suffix, context_budget, context_overlap):
        bridges.append(f"{anchor_text}\n\n{context}")
    return _deduplicate_texts(bridges)


def _build_retrieval_units(
    blocks: Sequence[Block],
    protected_type: str | None,
    target_tokens: int,
    soft_max_tokens: int,
    overlap_tokens: int,
) -> List[Dict]:
    parent_text = join_blocks(blocks)
    candidate_texts: List[tuple[str, str]] = []
    if count_tokens(parent_text) <= soft_max_tokens:
        candidate_texts.append(("group", parent_text))

    for block in blocks:
        pieces = _split_block_windows(block, target_tokens, overlap_tokens)
        candidate_texts.extend(("block", piece) for piece in pieces)

    if protected_type:
        candidate_texts.extend(
            ("bridge", text)
            for text in _protected_bridge_units(
                blocks, protected_type, target_tokens, overlap_tokens
            )
        )

    units = []
    seen = set()
    for kind, text in candidate_texts:
        normalized = text.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        units.append(
            {
                "kind": kind,
                "text": normalized,
                "token_count": count_tokens(normalized),
            }
        )
    return units


def _make_group(
    blocks: Sequence[Block],
    protected_type: str | None,
    target_tokens: int,
    soft_max_tokens: int,
    overlap_tokens: int,
) -> Dict:
    text = join_blocks(blocks)
    return {
        "text": text,
        "page": min(block.page_start for block in blocks),
        "page_end": max(block.page_end for block in blocks),
        "token_count": count_tokens(text),
        "block_types": [block.type for block in blocks],
        "blocks": [block.as_dict() for block in blocks],
        "protected_type": protected_type,
        "retrieval_units": _build_retrieval_units(
            blocks,
            protected_type,
            target_tokens,
            soft_max_tokens,
            overlap_tokens,
        ),
    }


def build_semantic_groups(
    blocks: Sequence[Block],
    target_tokens: int = 180,
    soft_max_tokens: int = 220,
    hard_max_tokens: int = 240,
    overlap_tokens: int = 32,
) -> List[Dict]:
    """Pack protected structural units into token-budgeted parent groups."""
    if target_tokens < 1:
        raise ValueError("target_tokens must be positive")
    if soft_max_tokens < target_tokens:
        raise ValueError("soft_max_tokens must be at least target_tokens")
    if hard_max_tokens < soft_max_tokens:
        raise ValueError("hard_max_tokens must be at least soft_max_tokens")
    if overlap_tokens < 0 or overlap_tokens >= target_tokens:
        raise ValueError("overlap_tokens must be between zero and target_tokens - 1")

    units = bind_structural_context(blocks)
    groups: List[Dict] = []
    pending: List[Block] = []
    pending_tokens = 0
    pending_page_end = None

    def flush_pending() -> None:
        nonlocal pending, pending_tokens, pending_page_end
        if pending:
            groups.append(
                _make_group(
                    tuple(pending), None, target_tokens, soft_max_tokens, overlap_tokens
                )
            )
        pending = []
        pending_tokens = 0
        pending_page_end = None

    for unit in units:
        if unit.protected_type:
            flush_pending()
            groups.append(
                _make_group(
                    unit.blocks,
                    unit.protected_type,
                    target_tokens,
                    soft_max_tokens,
                    overlap_tokens,
                )
            )
            continue

        unit_tokens = count_tokens(unit.text)
        separator_tokens = 0 if not pending else 1
        crosses_page = pending_page_end is not None and unit.page_start > pending_page_end
        if pending and (crosses_page or pending_tokens + separator_tokens + unit_tokens > target_tokens):
            flush_pending()
            separator_tokens = 0
        pending.extend(unit.blocks)
        pending_tokens += separator_tokens + unit_tokens
        pending_page_end = unit.page_end
        if pending_tokens >= soft_max_tokens:
            flush_pending()

    flush_pending()

    _attach_lead_ins(groups)
    for group in groups:
        _enforce_hard_budget(group, hard_max_tokens, overlap_tokens)
    return groups


def _enforce_hard_budget(group: Dict, hard_max_tokens: int, overlap_tokens: int) -> None:
    """Guarantee every retrieval unit fits the hard token budget.

    A block that lands over budget (an undelimited table, a formula shredded by
    a broken font) must never be a reason to reject the whole document: split
    any such unit further instead.
    """
    fitted: List[Dict] = []
    seen = {unit["text"] for unit in group["retrieval_units"]}
    for unit in group["retrieval_units"]:
        if unit["token_count"] <= hard_max_tokens:
            fitted.append(unit)
            continue
        window_overlap = min(overlap_tokens, max(0, hard_max_tokens - 1))
        for piece in _split_token_windows(unit["text"], hard_max_tokens, window_overlap):
            if not piece or piece in seen:
                continue
            seen.add(piece)
            fitted.append(
                {"kind": unit["kind"], "text": piece, "token_count": count_tokens(piece)}
            )
    group["retrieval_units"] = fitted


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[A-Z(“\"'¿¡])")


def lead_in_from(text: str, max_chars: int = 220) -> str:
    """The tail of ``text`` — a sentence or two — for display as run-in context
    before the following passage, so a result never opens mid-thought."""
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    window = collapsed[-max_chars:]
    boundary = _SENTENCE_BOUNDARY.search(window)
    if boundary:
        window = window[boundary.end():]
    else:
        window = window.lstrip()
        space = window.find(" ")
        if 0 < space < 40:
            window = window[space + 1:]
    return window.strip()


def _attach_lead_ins(groups: List[Dict]) -> None:
    previous = ""
    for group in groups:
        group["lead_in"] = lead_in_from(previous) if previous else ""
        previous = group["text"]


def chunk_document(
    pages: Sequence[Dict],
    target_tokens: int = 180,
    soft_max_tokens: int = 220,
    hard_max_tokens: int = 240,
    overlap_tokens: int = 32,
) -> List[Dict]:
    blocks = parse_typed_blocks(pages)
    return build_semantic_groups(
        blocks,
        target_tokens=target_tokens,
        soft_max_tokens=soft_max_tokens,
        hard_max_tokens=hard_max_tokens,
        overlap_tokens=overlap_tokens,
    )
