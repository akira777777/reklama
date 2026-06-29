from __future__ import annotations

from emoji import PLACEHOLDER, parse_custom_emoji


def test_no_emoji_markers():
    text = "Hello world! Standard text without emoji markers."
    result, entities = parse_custom_emoji(text)
    assert result == text
    assert entities == []


def test_single_emoji_marker():
    text = "Hello [emoji:12345] world!"
    result, entities = parse_custom_emoji(text)
    assert result == f"Hello {PLACEHOLDER} world!"
    assert len(entities) == 1
    assert entities[0].offset == 6
    assert entities[0].length == len(PLACEHOLDER)
    assert entities[0].document_id == 12345


def test_multiple_emoji_markers():
    text = "[emoji:111] text [emoji:222]"
    result, entities = parse_custom_emoji(text)
    assert result == f"{PLACEHOLDER} text {PLACEHOLDER}"
    assert len(entities) == 2
    # First emoji is at offset 0
    assert entities[0].offset == 0
    assert entities[0].document_id == 111
    # Second emoji is at offset 7
    assert entities[1].offset == 7
    assert entities[1].document_id == 222


def test_emoji_at_end():
    text = "See this: [emoji:99999]"
    result, entities = parse_custom_emoji(text)
    assert result == f"See this: {PLACEHOLDER}"
    assert len(entities) == 1
    assert entities[0].offset == 10
    assert entities[0].document_id == 99999


def test_empty_text():
    text = ""
    result, entities = parse_custom_emoji(text)
    assert result == ""
    assert entities == []


def test_emoji_only():
    text = "[emoji:777]"
    result, entities = parse_custom_emoji(text)
    assert result == PLACEHOLDER
    assert len(entities) == 1
    assert entities[0].offset == 0
    assert entities[0].document_id == 777


def test_entities_sorted_by_offset():
    text = "[emoji:3] [emoji:2] [emoji:1]"
    result, entities = parse_custom_emoji(text)
    assert len(entities) == 3
    offsets = [e.offset for e in entities]
    assert offsets == sorted(offsets)
