"""Тесты для emoji.py"""

from __future__ import annotations

import emoji


def test_parse_custom_emoji_no_emoji() -> None:
    text, entities = emoji.parse_custom_emoji("Hello world")
    assert text == "Hello world"
    assert entities == []


def test_parse_custom_emoji_one_emoji() -> None:
    text, entities = emoji.parse_custom_emoji("Hello [emoji:123456] world")
    assert text == "Hello \u2764 world"
    assert len(entities) == 1
    assert entities[0].offset == 6
    assert entities[0].length == 1
    assert entities[0].document_id == 123456


def test_parse_custom_emoji_multiple_emojis() -> None:
    text, entities = emoji.parse_custom_emoji("[emoji:111] и [emoji:222]")
    assert text == "\u2764 и \u2764"
    assert len(entities) == 2
    assert entities[0].offset == 0
    assert entities[0].length == 1
    assert entities[0].document_id == 111
    assert entities[1].offset == 4
    assert entities[1].length == 1
    assert entities[1].document_id == 222
