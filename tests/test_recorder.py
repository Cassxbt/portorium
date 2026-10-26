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


def test_parallel_first_replays_all_find_their_recording(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    writer = Recorder("svc", tmp_path)
    for i in range(300):
        writer.record({"q": i}, {"a": i})
    for _ in range(20):
        reader = Recorder("svc", tmp_path)
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(lambda i: reader.replay({"q": i})["a"], [299, 0])) == [299, 0]
