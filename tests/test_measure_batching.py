from scripts.measure_batching import aggregate_report


def test_aggregate_flags_batching_win():
    serial = [{"run": {"wall_seconds": 10.0}}, {"run": {"wall_seconds": 10.0}}]
    concurrent = [{"run": {"wall_seconds": 11.0}}, {"run": {"wall_seconds": 11.0}}]
    vaayu = {"batches_run": 5, "gate1_refused": 0, "gate2_refused": 0}
    report = aggregate_report(serial, concurrent, vaayu)
    assert report["serial_wall_sum"] == 20.0
    assert report["concurrent_wall_max"] == 11.0
    assert report["batches_run"] == 5
    assert report["batching_helped"] is True  # 11 < 20, batches formed


def test_aggregate_no_batches_is_not_a_win():
    serial = [{"run": {"wall_seconds": 10.0}}]
    concurrent = [{"run": {"wall_seconds": 10.0}}]
    report = aggregate_report(serial, concurrent, {"batches_run": 0})
    assert report["batching_helped"] is False


def test_healthy_win_is_valid():
    serial = [{"run": {"wall_seconds": 10.0}}, {"run": {"wall_seconds": 10.0}}]
    concurrent = [{"run": {"wall_seconds": 11.0}}, {"run": {"wall_seconds": 11.0}}]
    report = aggregate_report(serial, concurrent, {"batches_run": 5})
    assert report["valid"] is True
    assert report["n_failed"] == 0


def test_all_concurrent_crashed_is_not_a_win():
    serial = [{"run": {"wall_seconds": 10.0}}, {"run": {"wall_seconds": 10.0}}]
    concurrent = [{"run": {"wall_seconds": 0.0}}, {}]
    report = aggregate_report(serial, concurrent, {"batches_run": 5})
    assert report["batching_helped"] is False
    assert report["valid"] is False
    assert report["n_failed"] == 2


def test_empty_concurrent_is_invalid_not_a_crash():
    serial = [{"run": {"wall_seconds": 10.0}}]
    report = aggregate_report(serial, [], {"batches_run": 5})
    assert report["batching_helped"] is False
    assert report["valid"] is False
