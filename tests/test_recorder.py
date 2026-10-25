from portorium.recorder import MissingRecording, Recorder


def test_replay_returns_the_latest_live_response(tmp_path):
    rec = Recorder("svc", tmp_path)
    rec.record({"q": 1}, {"a": "old"})
    rec.record({"q": 1}, {"a": "new"})
    assert Recorder("svc", tmp_path).replay({"q": 1}) == {"a": "new"}


def test_unrecorded_requests_are_refused(tmp_path):
    rec = Recorder("svc", tmp_path)
    try:
        rec.replay({"q": 2})
    except MissingRecording:
        return
    raise AssertionError("expected MissingRecording")
