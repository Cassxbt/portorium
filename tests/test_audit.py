from portorium.audit import _quote


def test_quotes_escape_apostrophes_and_backslashes():
    assert _quote("men's jacket") == "'men''s jacket'"
    assert _quote("a\\'; DROP") == "'a ''; DROP'"
    assert _quote(None) == "''"
