from __future__ import annotations

from telethon.tl.types import MessageEntityBold, MessageEntityCustomEmoji

from import_message import prepare_message_for_saving


def test_no_entities():
    text = "Привет мир!"
    new_text, new_entities = prepare_message_for_saving(text, [])
    assert new_text == text
    assert new_entities == []


def test_non_emoji_entities_only():
    text = "Привет мир!"
    entities = [MessageEntityBold(offset=0, length=6)]
    new_text, new_entities = prepare_message_for_saving(text, entities)
    assert new_text == text
    assert len(new_entities) == 1
    assert isinstance(new_entities[0], MessageEntityBold)
    assert new_entities[0].offset == 0
    assert new_entities[0].length == 6


def test_custom_emoji_only():
    # "Привет ⭐!" — ⭐ (length 1)
    text = "Привет ⭐!"
    entities = [MessageEntityCustomEmoji(offset=7, length=1, document_id=12345)]
    new_text, new_entities = prepare_message_for_saving(text, entities)
    assert new_text == "Привет [emoji:12345]!"
    assert new_entities == []


def test_mixed_entities_alignment():
    # "Привет ⭐ мир!" — ⭐ (offset 7, length 1)
    # Bold covers "мир!" (offset 9, length 4)
    text = "Привет ⭐ мир!"
    entities = [
        MessageEntityCustomEmoji(offset=7, length=1, document_id=12345),
        MessageEntityBold(offset=9, length=4),
    ]
    new_text, new_entities = prepare_message_for_saving(text, entities)
    assert new_text == "Привет [emoji:12345] мир!"
    assert len(new_entities) == 1
    
    # Replacement string "[emoji:12345]" is 13 chars. Original emoji was 1 char.
    # diff = 13 - 1 = 12.
    # The Bold entity was at offset 9 (which is >= 7 + 1).
    # Its offset should be corrected: 9 + 12 = 21.
    # Length remains 4.
    bold = new_entities[0]
    assert bold.offset == 21
    assert bold.length == 4


def test_mixed_entities_wrapping():
    # "Привет ⭐!"
    # Bold covers "Привет ⭐" (offset 0, length 8)
    text = "Привет ⭐!"
    entities = [
        MessageEntityBold(offset=0, length=8),
        MessageEntityCustomEmoji(offset=7, length=1, document_id=12345),
    ]
    new_text, new_entities = prepare_message_for_saving(text, entities)
    assert new_text == "Привет [emoji:12345]!"
    assert len(new_entities) == 1
    
    # Bold starts before replacement (0 < 7) and ends after replacement (8 > 7).
    # Its length should grow by diff: 8 + 12 = 20.
    bold = new_entities[0]
    assert bold.offset == 0
    assert bold.length == 20


def test_utf16_surrogate_pairs():
    # Emojis like 😊 (U+1F60A) span 2 code units in UTF-16 but 1 character in Python.
    # Text: "😊⭐!" -> 😊 (2 units), ⭐ (1 unit), ! (1 unit). Total units = 4.
    # Offset of ⭐ in Telegram will be 2 (not 1!).
    # Bold covers "⭐!" -> offset 2, length 2.
    text = "😊⭐!"
    entities = [
        MessageEntityCustomEmoji(offset=2, length=1, document_id=54321),
        MessageEntityBold(offset=2, length=2),
    ]
    
    new_text, new_entities = prepare_message_for_saving(text, entities)
    
    # Replacement tag is "[emoji:54321]" (length 13).
    # diff = 13 - 1 = 12.
    # Bold started at 2 (which is equal to emoji offset).
    # It should remain at 2, but its length should grow by 12: 2 + 12 = 14.
    assert new_text == "😊[emoji:54321]!"
    assert len(new_entities) == 1
    bold = new_entities[0]
    assert bold.offset == 2
    assert bold.length == 14
