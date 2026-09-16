import os

from fc_telemetry.proc import ProcStats


def test_rss_from_statm_file(tmp_path):
    statm = tmp_path / "statm"
    pages = 1000
    statm.write_text(f"5000 {pages} 300 1 0 700 0\n")
    stats = ProcStats(statm_path=str(statm), clock=lambda: 100.0)
    snap = stats.snapshot()
    expected_mb = pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    assert abs(snap["rssMb"] - round(expected_mb, 1)) < 0.11
    assert snap["threads"] >= 1


def test_cpu_pct_is_delta_based(monkeypatch, tmp_path):
    statm = tmp_path / "statm"
    statm.write_text("1 1 0 0 0 0 0\n")
    times = iter([(1.0, 0.5), (2.0, 1.0)])  # (user, system) je Aufruf
    clock = iter([10.0, 20.0])

    class T:
        def __init__(self, u, s): self.user, self.system = u, s

    monkeypatch.setattr("fc_telemetry.proc.os.times", lambda: T(*next(times)))
    stats = ProcStats(statm_path=str(statm), clock=lambda: next(clock))
    first = stats.snapshot()
    assert first["cpuPct"] == 0.0  # kein Vorwert
    second = stats.snapshot()
    # (2.0+1.0)-(1.0+0.5) = 1.5 CPU-Sekunden in 10 s Wanduhr = 15 %
    assert second["cpuPct"] == 15.0


def test_missing_statm_omits_rss(tmp_path):
    stats = ProcStats(statm_path=str(tmp_path / "nope"), clock=lambda: 1.0)
    assert "rssMb" not in stats.snapshot()


def test_page_size_falls_back_when_sysconf_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr("fc_telemetry.proc.os.sysconf", lambda x: (_ for _ in ()).throw(ValueError("unrecognized configuration name")))
    statm = tmp_path / "statm"
    statm.write_text("1 1000 0 0 0 0 0\n")
    stats = ProcStats(statm_path=str(statm), clock=lambda: 1.0)
    snap = stats.snapshot()
    assert snap["rssMb"] == round(1000 * 4096 / (1024 * 1024), 1)
