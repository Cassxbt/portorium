from portorium.nightshift import _parse

REQUIRED = {"match": ("full", "partial", "none"), "reason": None}


def test_parses_plain_and_fenced_json():
    assert _parse('{"match": "none", "reason": "x"}', REQUIRED)["match"] == "none"
    assert _parse('```json\n{"match": "full", "reason": "y"}\n```', REQUIRED)["match"] == "full"


def test_rejects_missing_keys_and_values_outside_the_enum():
    assert _parse('{"match": "maybe", "reason": "x"}', REQUIRED) is None
    assert _parse('{"reason": "x"}', REQUIRED) is None
    assert _parse("The user is asking for...", REQUIRED) is None
