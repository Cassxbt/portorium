from portorium.eval import Item, Prediction, pending


def _item(n: str) -> Item:
    return Item(n, "2026-01-02", "2026-01-01", "6110202079", "sweater", set())


def test_a_failed_system_is_retried_and_finished_ones_are_not():
    items = [_item("N1"), _item("N2")]
    done = [Prediction("keyword_top1", "N1", "x"), Prediction("precedent_gate", "N1", "y"),
            Prediction("keyword_top1", "N2", "x"),
            Prediction("error", "N2", None, {"system": "precedent_gate", "error": "ZetarisError: HTTP 400"})]
    todo = pending(items, {"keyword_top1", "precedent_gate"}, done)
    assert [(i.ruling_number, names) for i, names in todo] == [("N2", {"precedent_gate"})]
