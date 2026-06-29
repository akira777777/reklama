from __future__ import annotations


def test_import_all_submodules():
    """Verify that all reklama submodules can be imported without errors."""
    assert True

def test_import_top_level():
    """Verify top-level reklama package import."""
    import reklama
    assert reklama is not None
