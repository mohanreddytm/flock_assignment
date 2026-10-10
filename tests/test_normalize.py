from datetime import datetime, timezone

import pytest

from app.errors import PortalError
from app.models import Export
from app.normalize import consumption, detail, export_meter, network, number


def reading(at, value):
    return {"timestamp": at, "kwh": value, "kvah": "0", "voltR": "230"}


@pytest.mark.parametrize(
    "fixture,meter_id,expected",
    [("legacy_html", "J100000", "HPL"), ("v2_html", "J100004", "Genus")],
)
def test_both_nameplate_formats(request, fixture, meter_id, expected):
    result = detail(
        request.getfixturevalue(fixture), meter_id, {"latitude": "0", "longitude": "0"}
    )
    assert result.make == expected
    assert result.location.latitude == 0
    assert result.serial_number
    assert len(result.hierarchy) == 7
    assert not result.warnings


def test_mismatched_meter_is_rejected(legacy_html):
    with pytest.raises(PortalError, match="different or missing"):
        detail(legacy_html, "WRONG", {})


def test_missing_layout_is_not_an_empty_success():
    with pytest.raises(PortalError):
        detail("<main>Maintenance</main>", "J100000", {})


def test_missing_hierarchy_does_not_shift_levels(legacy_html):
    html = legacy_html.replace("<span>Circle 1 (C-01)</span>", "")
    result = detail(html, "J100000", {})
    assert result.hierarchy[1].level == "division"
    assert len(result.warnings) == 2


@pytest.mark.parametrize(
    "value,expected",
    [("0", 0), ("1,200.50", 1200.5), ("—", None), ("", None), (None, None)],
)
def test_numeric_normalization(value, expected):
    assert number(value) == expected


@pytest.mark.parametrize("value", ["NaN", "inf", "twelve"])
def test_bad_measurements_are_rejected(value):
    with pytest.raises(PortalError):
        number(value)


def test_consumption_sorts_and_subtracts_registers():
    rows = [
        reading("24/06/2026 01:00", "10.5"),
        reading("24/06/2026 00:00", "0"),
        reading("24/06/2026 00:30", "10"),
    ]
    result = consumption("J1", rows)
    assert result.readings[0].kwh == 0
    assert [row.interval_kwh for row in result.readings] == [None, 10, 0.5]
    assert result.observed_usage_kwh == 10.5
    assert result.readings[0].timestamp.utcoffset().total_seconds() == 19800


def test_missing_reading_and_reset_are_not_bridged():
    rows = [
        reading("24/06/2026 00:00", "10"),
        reading("24/06/2026 00:30", ""),
        reading("24/06/2026 01:00", "15"),
        reading("24/06/2026 01:30", "2"),
    ]
    result = consumption("J1", rows)
    assert result.observed_usage_kwh is None
    assert all(row.interval_kwh is None for row in result.readings)
    assert any("decreased" in item for item in result.warnings)


def test_inclusive_filter_does_not_include_outside_interval():
    rows = [reading("24/06/2026 00:00", "10"), reading("24/06/2026 00:30", "12")]
    bound = datetime.fromisoformat("2026-06-24T00:30:00+05:30")
    result = consumption("J1", rows, bound, bound)
    assert result.count == 1
    assert result.observed_usage_kwh is None


def test_duplicate_timestamps_are_flagged():
    result = consumption(
        "J1", [reading("24/06/2026 00:00", "10"), reading("24/06/2026 00:00", "12")]
    )
    assert result.count == 2
    assert result.observed_usage_kwh is None
    assert any("Duplicate" in item for item in result.warnings)


def test_invalid_date_fails_explicitly():
    with pytest.raises(PortalError):
        consumption("J1", [reading("31/02/2026 00:00", "1")])


def test_network_retains_aliases_and_multiple_parents():
    records = []
    for identifier, feeder, name in [("J1", "F-1", "Old DT"), ("J2", "F-2", "New DT")]:
        records.append(
            export_meter(
                {
                    "meterId": identifier,
                    "dtCode": "DT-1",
                    "hierarchy": {
                        "feeder": {"code": feeder, "name": feeder},
                        "dt": {"code": "DT-1", "name": name},
                    },
                }
            )
        )
    graph = network(
        Export(
            data=records, total=2, fetched_at=datetime.now(timezone.utc), warnings=[]
        )
    )
    dt = next(node for node in graph.nodes if node.id == "transformer:DT-1")
    assert dt.names == ["New DT", "Old DT"]
    assert dt.parent_ids == ["feeder:F-1", "feeder:F-2"]
    assert dt.meter_ids == ["J1", "J2"]
    assert any("multiple parents" in value for value in graph.warnings)
