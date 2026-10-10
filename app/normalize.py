import math
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from app.errors import PortalError
from app.models import (
    Consumption,
    HierarchyLevel,
    Location,
    Meter,
    MeterDetail,
    Reading,
    Transformer,
)

MISSING = {"", "-", "—", "n/a", "na", "null", "none"}
LEVELS = [
    "zone",
    "circle",
    "division",
    "subdivision",
    "substation",
    "feeder",
    "transformer",
]


def text(value) -> str | None:
    if value is None:
        return None
    result = " ".join(str(value).split())
    return None if result.casefold() in MISSING else result


def number(value) -> float | None:
    value = text(value)
    if value is None:
        return None
    try:
        result = float(value.replace(",", ""))
    except ValueError:
        raise PortalError(
            "invalid_upstream_data", "The portal returned a non-numeric measurement."
        ) from None
    if not math.isfinite(result):
        raise PortalError(
            "invalid_upstream_data", "The portal returned a non-finite measurement."
        )
    return result


def meter(raw: dict) -> Meter:
    meter_id = text(raw.get("meterId"))
    if not meter_id:
        raise PortalError("invalid_upstream_data", "A portal meter has no identifier.")
    return Meter(
        meter_id=meter_id,
        serial_number=text(raw.get("serialNo")),
        make=text(raw.get("make")),
        phase=text(raw.get("phaseType")),
        status=text(raw.get("installStatus")),
        transformer_code=text(raw.get("dtCode")),
    )


def transformer(raw: dict) -> Transformer:
    code = text(raw.get("code"))
    if not code:
        raise PortalError("invalid_upstream_data", "A portal transformer has no code.")
    return Transformer(
        code=code,
        name=text(raw.get("name")),
        feeder_code=text(raw.get("feederCode")),
        capacity_kva=number(raw.get("capacityKva")),
    )


def key(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", label.casefold())


def detail(html: str, meter_id: str, geo: dict) -> MeterDetail:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(
        "h2", string=lambda value: value and value.strip() == "Nameplate"
    )
    table = heading.find_next_sibling("dl") if heading else None
    if table is None:
        raise PortalError(
            "invalid_upstream_data", "The portal nameplate layout was not recognized."
        )
    fields = {}
    for label in table.find_all("dt"):
        value = label.find_next_sibling("dd")
        if value is not None:
            fields[label.get_text(" ", strip=True)] = text(
                value.get_text(" ", strip=True)
            )
    normalized = {key(label): value for label, value in fields.items()}
    actual_id = normalized.get("meterid") or normalized.get("meternumber")
    if actual_id != meter_id:
        raise PortalError(
            "invalid_upstream_data",
            "The portal returned a different or missing meter identifier.",
        )
    nav = soup.select_one("main > nav")
    hierarchy = []
    warnings = []
    if nav:
        values = [span.get_text(" ", strip=True) for span in nav.find_all("span")]
        values = [value for value in values if value and value != "›"]
        for value in values:
            match = re.fullmatch(r"(.*?)\s*\(([^()]+)\)", value)
            code = match.group(2).strip() if match else None
            prefix = code.split("-", 1)[0] if code else None
            level = {
                "Z": "zone",
                "C": "circle",
                "D": "division",
                "SD": "subdivision",
                "SS": "substation",
                "F": "feeder",
                "DT": "transformer",
            }.get(prefix, "unknown")
            hierarchy.append(
                HierarchyLevel(
                    level=level,
                    code=code,
                    name=match.group(1).strip() if match else value,
                )
            )
    if len(hierarchy) != 7 or {entry.level for entry in hierarchy} != set(LEVELS):
        warnings.append(
            "The portal hierarchy is incomplete or has an unrecognized level."
        )
    latitude, longitude = number(geo.get("latitude")), number(geo.get("longitude"))
    location = None
    if (
        latitude is not None
        and longitude is not None
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    ):
        location = Location(latitude=latitude, longitude=longitude)
    else:
        warnings.append(
            "The portal location is missing or outside valid coordinate ranges."
        )
    return MeterDetail(
        meter_id=meter_id,
        serial_number=normalized.get("serialno") or normalized.get("serialnumber"),
        make=normalized.get("make"),
        phase=normalized.get("phasetype"),
        status=normalized.get("installationstatus") or normalized.get("installstatus"),
        transformer_code=next(
            (entry.code for entry in hierarchy if entry.level == "transformer"), None
        ),
        installation_type=normalized.get("installationtype")
        or normalized.get("installtype"),
        location=location,
        hierarchy=hierarchy,
        nameplate=fields,
        warnings=warnings,
    )


def timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%d/%m/%Y %H:%M")
        except ValueError:
            raise PortalError(
                "invalid_upstream_data",
                "A portal reading has an unrecognized timestamp.",
            ) from None
    return (
        parsed.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
        if parsed.tzinfo is None
        else parsed
    )


def consumption(
    meter_id: str,
    raw: list[dict],
    start: datetime | None = None,
    end: datetime | None = None,
) -> Consumption:
    readings = []
    warnings = [
        "Timezone-less portal timestamps are interpreted as Asia/Kolkata.",
        "kWh and kVAh are treated as cumulative registers; this interpretation is not confirmed by the portal.",
    ]
    for row in raw:
        raw_time = text(row.get("timestamp"))
        if raw_time is None:
            raise PortalError(
                "invalid_upstream_data", "A portal reading has no timestamp."
            )
        at = timestamp(raw_time)
        if (start and at < start) or (end and at > end):
            continue
        readings.append(
            Reading(
                timestamp=at,
                kwh=number(row.get("kwh")),
                kvah=number(row.get("kvah")),
                voltage_r=number(row.get("voltR")),
                interval_kwh=None,
            )
        )
    readings.sort(key=lambda row: row.timestamp)
    total = 0.0
    intervals = 0
    for previous, current in zip(readings, readings[1:]):
        if previous.timestamp == current.timestamp:
            warnings.append(
                "Duplicate timestamps were retained; their interval differences were omitted."
            )
        elif previous.kwh is not None and current.kwh is not None:
            difference = round(current.kwh - previous.kwh, 6)
            if difference < 0:
                warnings.append(
                    "A register decreased; its interval difference was omitted."
                )
            else:
                current.interval_kwh = difference
                total += difference
                intervals += 1
    if any(row.kwh is None for row in readings):
        warnings.append(
            "Some kWh readings are missing; usage only includes adjacent valid readings."
        )
    return Consumption(
        meter_id=meter_id,
        readings=readings,
        count=len(readings),
        observed_usage_kwh=round(total, 6) if intervals else None,
        warnings=list(dict.fromkeys(warnings)),
    )


def export_meter(raw):
    from app.models import ExportMeter

    summary = meter(raw)
    raw_hierarchy = raw.get("hierarchy")
    if not isinstance(raw_hierarchy, dict):
        raw_hierarchy = {}
    hierarchy = []
    warnings = []
    for level in LEVELS:
        part = raw_hierarchy.get("dt" if level == "transformer" else level)
        if not isinstance(part, dict) or not text(part.get("code")):
            warnings.append(f"Missing {level} code.")
            continue
        hierarchy.append(
            HierarchyLevel(
                level=level,
                code=text(part["code"]),
                name=text(part.get("name")) or text(part["code"]),
            )
        )
    geo = raw.get("geo")
    geo = geo if isinstance(geo, dict) else {}
    lat, lng = number(geo.get("lat")), number(geo.get("lng"))
    location = None
    if lat is not None and lng is not None and -90 <= lat <= 90 and -180 <= lng <= 180:
        location = Location(latitude=lat, longitude=lng)
    else:
        warnings.append("Location is missing or outside valid coordinate ranges.")
    dt = next((node.code for node in hierarchy if node.level == "transformer"), None)
    if dt != summary.transformer_code:
        warnings.append("Transformer code differs between the meter and its hierarchy.")
    return ExportMeter(
        **summary.model_dump(),
        installation_type=text(raw.get("installType")),
        location=location,
        hierarchy=hierarchy,
        warnings=warnings,
    )


def network(export):
    from app.models import NetworkHierarchy, NetworkNode

    nodes = {}
    warnings = []
    unassigned = []
    for record in export.data:
        parts = {part.level: part for part in record.hierarchy}
        previous = None
        if "transformer" not in parts:
            unassigned.append(record.meter_id)
        warnings.extend(
            f"{record.meter_id}: {warning}"
            for warning in record.warnings
            if "Location" not in warning
        )
        for level in LEVELS:
            part = parts.get(level)
            if part is None:
                previous = None
                continue
            identifier = f"{level}:{part.code}"
            node = nodes.setdefault(
                identifier,
                NetworkNode(
                    id=identifier,
                    level=level,
                    code=part.code,
                    names=[],
                    parent_ids=[],
                    meter_ids=[],
                ),
            )
            if part.name not in node.names:
                node.names.append(part.name)
            if previous and previous not in node.parent_ids:
                node.parent_ids.append(previous)
            if record.meter_id not in node.meter_ids:
                node.meter_ids.append(record.meter_id)
            previous = identifier
    for node in nodes.values():
        node.names.sort()
        node.parent_ids.sort()
        node.meter_ids.sort()
        if len(node.names) > 1:
            warnings.append(f"{node.id} has multiple names; all names were retained.")
        if len(node.parent_ids) > 1:
            warnings.append(
                f"{node.id} appears under multiple parents; all relationships were retained."
            )
    return NetworkHierarchy(
        nodes=sorted(
            nodes.values(), key=lambda node: (LEVELS.index(node.level), node.code)
        ),
        unassigned_meter_ids=sorted(unassigned),
        total_meters=export.total,
        fetched_at=export.fetched_at,
        warnings=list(dict.fromkeys(warnings)),
    )
