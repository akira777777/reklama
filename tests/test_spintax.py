from __future__ import annotations

from reklama.spintax import resolve_spintax


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


def test_escaped_spintax():
    text = "Hello \\{world\\} with \\| pipe and \\\\ backslash."
    assert resolve_spintax(text) == "Hello {world} with | pipe and \\ backslash."

    spintax = "Choose {\\{one\\}|\\{two\\}}"
    results = {resolve_spintax(spintax) for _ in range(20)}
    assert results == {"Choose {one}", "Choose {two}"}


def test_unmatched_braces():
    # Unmatched open braces should not crash and should resolve gracefully
    assert resolve_spintax("Hello {world") == "Hello world"
    # Unmatched close braces should be treated as literal
    assert resolve_spintax("Hello world}") == "Hello world}"
    # Unmatched pipes outside braces should be treated as literal
    assert resolve_spintax("Hello | world") == "Hello | world"
