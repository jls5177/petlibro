"""Helpers for normalizing PETLIBRO work records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from homeassistant.util import dt as dt_util


@dataclass(frozen=True, slots=True)
class WorkRecord:
    """Normalized PETLIBRO work record."""

    record_id: str | None
    record_type: str
    event_type: str | None
    timestamp: datetime | None
    raw: dict[str, Any]

    @property
    def timestamp_ms(self) -> int:
        """Return the source timestamp in milliseconds for sorting."""
        if self.timestamp is None:
            return 0
        return int(self.timestamp.timestamp() * 1000)


def _timestamp_from_record(record: dict[str, Any]) -> datetime | None:
    """Return the first valid record timestamp as UTC."""
    for key in ("recordTime", "createTime", "videoStartTime", "startTime"):
        value = record.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if value <= 0:
            continue
        return dt_util.utc_from_timestamp(value / 1000)
    return None


def normalize_work_records(raw: object) -> tuple[WorkRecord, ...]:
    """Flatten and sort work records without rejecting unknown record types."""
    if isinstance(raw, dict):
        wrapped = raw.get("data")
        if isinstance(wrapped, (dict, list)):
            raw = wrapped
        elif isinstance(raw.get("workRecords"), list):
            raw = [raw]

    if not isinstance(raw, list):
        return ()

    records: list[WorkRecord] = []
    for day_entry in raw:
        if not isinstance(day_entry, dict):
            continue
        day_records = day_entry.get("workRecords")
        if day_records is None and isinstance(day_entry.get("data"), dict):
            day_records = day_entry["data"].get("workRecords")
        if not isinstance(day_records, list):
            continue
        for record in day_records:
            if not isinstance(record, dict):
                continue
            record_type = record.get("type")
            if not isinstance(record_type, str) or not record_type:
                record_type = "UNKNOWN"
            event_type = record.get("eventType")
            records.append(
                WorkRecord(
                    record_id=record.get("id") if isinstance(record.get("id"), str) else None,
                    record_type=record_type,
                    event_type=event_type if isinstance(event_type, str) else None,
                    timestamp=_timestamp_from_record(record),
                    raw=record,
                )
            )

    records.sort(key=lambda record: record.timestamp_ms, reverse=True)
    return tuple(records)


def first_record(
    records: tuple[WorkRecord, ...],
    *record_types: str,
) -> WorkRecord | None:
    """Return the newest record matching any requested type."""
    wanted = set(record_types)
    return next(
        (
            record
            for record in records
            if record.record_type in wanted or record.event_type in wanted
        ),
        None,
    )


def grain_quantity(record: WorkRecord | None) -> int | None:
    """Return a validated grain quantity from legacy or mixed records."""
    if record is None:
        return None

    quantity = _nonnegative_int(record.raw.get("actualGrainNum"))
    if quantity is not None:
        return quantity

    params = record.raw.get("params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            return None
    if not isinstance(params, dict):
        return None

    quantity = _nonnegative_int(params.get("grain"))
    if quantity is not None:
        return quantity

    left = _nonnegative_int(params.get("leftGrain"))
    right = _nonnegative_int(params.get("rightGrain"))
    if left is None and right is None:
        return None
    return (left or 0) + (right or 0)


def _nonnegative_int(value: object) -> int | None:
    """Return a non-negative whole number without accepting booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 and value.is_integer() else None
    if isinstance(value, str):
        try:
            numeric = float(value)
        except ValueError:
            return None
        return int(numeric) if numeric >= 0 and numeric.is_integer() else None
    return None


def meal_pet_names(record: WorkRecord | None) -> tuple[str, ...]:
    """Return bounded, de-duplicated pet names from a meal record."""
    if record is None:
        return ()

    names: list[str] = []

    pets_info = record.raw.get("petsInfo")
    if isinstance(pets_info, list):
        for pet in pets_info:
            if not isinstance(pet, dict):
                continue
            name = pet.get("petName", pet.get("name"))
            if isinstance(name, str):
                names.append(name)

    pet_name = record.raw.get("petName")
    if isinstance(pet_name, str):
        names.append(pet_name)

    params = record.raw.get("params")
    if isinstance(params, str):
        try:
            params = json.loads(params)
        except json.JSONDecodeError:
            params = None
    if isinstance(params, dict):
        param_names = params.get("petName")
        if isinstance(param_names, str):
            names.extend(param_names.split(","))

    return tuple(dict.fromkeys(name.strip() for name in names if name.strip()))[:10]
