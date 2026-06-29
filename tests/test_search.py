from __future__ import annotations

from search import parse_telegram_link


def test_parse_telegram_link_username():
    assert parse_telegram_link("https://t.me/PrahaChatik") == ("username", "PrahaChatik")
    assert parse_telegram_link("http://t.me/student_praga") == ("username", "student_praga")
    assert parse_telegram_link("t.me/kdovi_chat") == ("username", "kdovi_chat")
    assert parse_telegram_link("@friends_cz_chat") == ("username", "friends_cz_chat")
    assert parse_telegram_link("localno_praha") == ("username", "localno_praha")


def test_parse_telegram_link_hash():
    assert parse_telegram_link("https://t.me/joinchat/AAAAAFf5u9") == ("hash", "AAAAAFf5u9")
    assert parse_telegram_link("t.me/joinchat/AAAAAFf5u9") == ("hash", "AAAAAFf5u9")
    assert parse_telegram_link("https://t.me/+AAAAAFf5u9") == ("hash", "AAAAAFf5u9")
    assert parse_telegram_link("t.me/+AAAAAFf5u9") == ("hash", "AAAAAFf5u9")


def test_parse_telegram_link_invalid_and_empty():
    assert parse_telegram_link("") is None
    assert parse_telegram_link("   ") is None
    assert parse_telegram_link("https://t.me/") is None
    assert parse_telegram_link("invalid-characters$#@") is None
    assert parse_telegram_link("   https://t.me/PrahaChatik   ") == ("username", "PrahaChatik")
