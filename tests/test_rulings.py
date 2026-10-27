from portorium.rulings import scrub


def test_scrub_removes_personal_details_and_keeps_the_product():
    facts = ("Dear Mr. Acker: The sample is a knitted cardigan of 60% cotton. "
             "Contact Ms. Wang at wang@example.com or (212) 555-0100. Ship to 80 Voice Road, Carle Place, NY 11514. "
             "It has five buttons.")
    out = scrub(facts)
    assert "knitted cardigan" in out and "five buttons" in out
    for leak in ("Acker", "Wang", "@", "555", "11514"):
        assert leak not in out


def test_facts_read_back_exactly_as_first_extracted(tmp_path, monkeypatch):
    from portorium import rulings
    monkeypatch.setattr(rulings, "FACTS", tmp_path)
    monkeypatch.setattr(rulings, "FULLTEXT", tmp_path / "none")
    first = rulings.save_facts("N1", "Dear Sir:\r\nThe merchandise is a shirt.\r\nIt is knitted.\rThe applicable subheading is 6105.")
    assert "\r" not in first
    assert rulings.stored_facts("N1") == first
