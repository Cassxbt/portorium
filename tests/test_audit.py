from portorium.audit import _quote


def test_quotes_escape_apostrophes_and_backslashes():
    assert _quote("men's jacket") == "'men''s jacket'"
    assert _quote("a\\'; DROP") == "'a ''; DROP'"
    assert _quote(None) == "''"


def test_truncation_cannot_split_an_escaped_quote():
    quoted = _quote("A" * 1999 + "'")
    assert quoted.endswith("''") or quoted.endswith("A'")
    assert quoted[1:-1].replace("''", "").count("'") == 0
