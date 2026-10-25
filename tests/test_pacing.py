from portorium.pacing import Pacer


def test_rejections_widen_and_successes_narrow_the_interval():
    p = Pacer(per_minute=60, ceiling_seconds=4)
    assert p.interval == 1.0
    p.rejected(); p.rejected()
    assert p.interval == 2.25
    for _ in range(100):
        p.succeeded()
    assert p.interval == 1.0
    for _ in range(20):
        p.rejected()
    assert p.interval == 4
