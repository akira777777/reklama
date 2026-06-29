from __future__ import annotations

from run import resolve_spintax


def test_no_spintax():
    text = "Hello World! Standard text without curly braces."
    assert resolve_spintax(text) == text


def test_simple_spintax():
    options = {"hello", "hi", "hey"}
    spintax = "{hello|hi|hey}"
    
    # Run multiple times to verify randomness and correctness
    results = {resolve_spintax(spintax) for _ in range(50)}
    assert results.issubset(options)
    assert len(results) > 1  # Verify randomness exists


def test_multiple_spintax_blocks():
    spintax = "{hello|hi} {world|friends}!"
    results = {resolve_spintax(spintax) for _ in range(50)}
    
    expected = {
        "hello world!",
        "hello friends!",
        "hi world!",
        "hi friends!",
    }
    assert results.issubset(expected)
    assert len(results) > 1


def test_nested_spintax():
    spintax = "Привет, {мир|дорогие {друзья|коллеги}}!"
    results = {resolve_spintax(spintax) for _ in range(50)}
    
    expected = {
        "Привет, мир!",
        "Привет, дорогие друзья!",
        "Привет, дорогие коллеги!",
    }
    assert results.issubset(expected)
    assert len(results) > 1
