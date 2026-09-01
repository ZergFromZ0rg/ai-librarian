import pytest

from chunking import (
    BLOCK_TYPES,
    _enforce_hard_budget,
    _regex_token_spans,
    chunk_document,
    count_tokens,
    lead_in_from,
    normalize_for_embedding,
    parse_typed_blocks,
)


def test_parser_emits_all_supported_block_types():
    pages = [
        {
            "page": 1,
            "text": """# Method

The following equation defines the model:

$$
Ax = b
$$

Table 1: Evaluation results

| Model | Score |
| --- | --- |
| Base | 0.8 |
""",
        }
    ]

    blocks = parse_typed_blocks(pages)

    assert {block.type for block in blocks} == BLOCK_TYPES
    assert [block.type for block in blocks] == [
        "heading",
        "paragraph",
        "equation",
        "caption",
        "table",
    ]


def test_equation_is_bound_to_intro_and_cross_page_explanation():
    pages = [
        {
            "page": 1,
            "text": "In this problem, the following equation applies:\n\n$$\nAx = b\n$$",
        },
        {"page": 2, "text": "This equation can be solved by elimination."},
    ]

    groups = chunk_document(pages)

    assert len(groups) == 1
    assert groups[0]["protected_type"] == "equation"
    assert groups[0]["block_types"] == ["paragraph", "equation", "paragraph"]
    assert groups[0]["page"] == 1
    assert groups[0]["page_end"] == 2
    assert "Ax = b" in groups[0]["text"]
    assert "solved by elimination" in groups[0]["text"]


def test_table_is_bound_to_introduction_caption_and_explanation():
    pages = [
        {
            "page": 3,
            "text": """The comparison is shown below:

Table 2: Accuracy by model

| Model | Accuracy |
| --- | --- |
| Base | 91% |

The proposed model performs best.
""",
        }
    ]

    groups = chunk_document(pages)

    assert len(groups) == 1
    assert groups[0]["protected_type"] == "table"
    assert groups[0]["block_types"] == ["paragraph", "caption", "table", "paragraph"]
    assert "| Model | Accuracy |" in groups[0]["text"]


def test_normal_groups_do_not_cross_completed_page_boundaries():
    pages = [
        {"page": 1, "text": "First page paragraph."},
        {"page": 2, "text": "Second page paragraph."},
    ]

    groups = chunk_document(pages)

    assert [(group["page"], group["page_end"]) for group in groups] == [(1, 1), (2, 2)]


def test_unfinished_paragraph_can_continue_across_pages():
    pages = [
        {"page": 1, "text": "The derivation continues with"},
        {"page": 2, "text": "the substitution used in the next step."},
    ]

    groups = chunk_document(pages)

    assert len(groups) == 1
    assert groups[0]["page"] == 1
    assert groups[0]["page_end"] == 2
    assert "continues with\n\nthe substitution" in groups[0]["text"]


def test_lead_in_is_the_sentence_tail_of_the_previous_group():
    long_tail = "Earlier discussion fills the first passage. " * 8
    pages = [
        {"page": 1, "text": long_tail + "It ends on a clean final sentence here."},
        {"page": 2, "text": "The next passage begins on a fresh page."},
    ]

    groups = chunk_document(pages)

    assert groups[0]["lead_in"] == ""
    assert groups[1]["lead_in"]
    assert groups[1]["lead_in"] in groups[0]["text"]
    assert len(groups[1]["lead_in"]) <= 220
    # trimmed to a sentence start, not a mid-word cut
    assert groups[1]["lead_in"][0].isupper()


def test_lead_in_from_returns_short_text_whole():
    assert lead_in_from("A single short sentence.") == "A single short sentence."


def test_retrieval_children_respect_token_budget_for_long_unbroken_text():
    pages = [{"page": 1, "text": "x" * 2_500}]

    groups = chunk_document(
        pages,
        target_tokens=40,
        soft_max_tokens=50,
        hard_max_tokens=60,
        overlap_tokens=10,
    )

    assert len(groups) == 1
    assert len(groups[0]["retrieval_units"]) > 1
    assert all(unit["token_count"] <= 60 for unit in groups[0]["retrieval_units"])


def test_oversized_equation_group_repeats_anchor_in_context_bridges():
    prefix = " ".join(f"intro{i}" for i in range(35))
    suffix = " ".join(f"explain{i}" for i in range(35))
    pages = [
        {
            "page": 1,
            "text": f"{prefix}\n\n$$\nAx = b\n$$\n\n{suffix}",
        }
    ]

    group = chunk_document(
        pages,
        target_tokens=20,
        soft_max_tokens=24,
        hard_max_tokens=28,
        overlap_tokens=4,
    )[0]
    bridges = [unit for unit in group["retrieval_units"] if unit["kind"] == "bridge"]

    assert len(bridges) > 2
    assert all("Ax = b" in unit["text"] for unit in bridges)
    assert all(unit["token_count"] <= 28 for unit in bridges)


def test_large_table_repeats_header_in_each_table_child():
    rows = "\n".join(f"| Model {index} | {index}% |" for index in range(20))
    pages = [
        {
            "page": 1,
            "text": (
                "Table 1: Results\n\n"
                "| Model | Accuracy |\n| --- | --- |\n"
                f"{rows}"
            ),
        }
    ]

    group = chunk_document(
        pages,
        target_tokens=30,
        soft_max_tokens=35,
        hard_max_tokens=40,
        overlap_tokens=4,
    )[0]
    table_children = [
        unit["text"]
        for unit in group["retrieval_units"]
        if unit["kind"] in {"block", "bridge"} and "| Model" in unit["text"]
    ]

    assert len(table_children) > 1
    assert all("| Model | Accuracy |" in text for text in table_children)


def test_each_constituent_block_is_searchable_but_parent_text_is_preserved():
    pages = [
        {
            "page": 1,
            "text": "A paragraph about alpha.\n\nA separate paragraph about beta.",
        }
    ]

    group = chunk_document(pages)[0]
    child_texts = [unit["text"] for unit in group["retrieval_units"]]

    assert group["text"] == (
        "A paragraph about alpha.\n\nA separate paragraph about beta."
    )
    assert "A paragraph about alpha." in child_texts
    assert "A separate paragraph about beta." in child_texts


def test_markdown_normalization_keeps_mathematical_symbols():
    markdown = "## Result\n\n**Derivative:** $∂f/∂x ≥ 0$ and [proof](https://example.test)."

    normalized = normalize_for_embedding(markdown)

    assert normalized == "Result Derivative: $∂f/∂x ≥ 0$ and proof."


def test_enforce_hard_budget_splits_over_budget_units_instead_of_raising():
    long_text = " ".join(f"word{index}" for index in range(200))
    group = {
        "retrieval_units": [
            {"kind": "block", "text": long_text, "token_count": count_tokens(long_text)},
        ]
    }

    _enforce_hard_budget(group, hard_max_tokens=40, overlap_tokens=5)

    units = group["retrieval_units"]
    assert len(units) > 1
    assert all(unit["token_count"] <= 40 for unit in units)
    assert all(unit["kind"] == "block" for unit in units)


def test_regex_token_estimator_splits_long_damaged_words():
    # The dependency-free fallback used when the real tokenizer will not load.
    assert len(_regex_token_spans("ordinary words")) == 2
    assert len(_regex_token_spans("x" * 80)) == 10


def test_count_tokens_is_positive_and_grows_with_length():
    assert count_tokens("word") >= 1
    assert count_tokens("one two three four five six") > count_tokens("one two")
    assert count_tokens("") == 0


@pytest.mark.parametrize(
    "target,soft,hard,overlap",
    [(0, 10, 20, 0), (20, 10, 30, 0), (10, 20, 15, 0), (10, 20, 30, 10)],
)
def test_invalid_token_budgets_are_rejected(target, soft, hard, overlap):
    with pytest.raises(ValueError):
        chunk_document(
            [{"page": 1, "text": "text"}],
            target_tokens=target,
            soft_max_tokens=soft,
            hard_max_tokens=hard,
            overlap_tokens=overlap,
        )
