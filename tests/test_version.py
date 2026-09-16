import fc_telemetry


def test_version_is_semver_string():
    parts = fc_telemetry.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
