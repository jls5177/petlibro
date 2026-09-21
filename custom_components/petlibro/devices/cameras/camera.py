"""Shared PETLIBRO cloud camera model."""

from __future__ import annotations

from datetime import datetime
from logging import getLogger
from typing import Any

from ...exceptions import PetLibroAPIError
from ..device import Device
from ..worklog import WorkRecord, first_record, meal_pet_names, normalize_work_records

_LOGGER = getLogger(__name__)


def _optional_bool(value: object) -> bool | None:
    """Return an API boolean without conflating missing values with false."""
    return value if isinstance(value, bool) else None


class CameraDevice(Device):
    """Base class for PETLIBRO devices with cloud camera capabilities."""

    async def refresh(self) -> None:
        """Refresh common device and camera data."""
        await super().refresh()

        await self._refresh_camera_endpoint(
            "realInfo",
            self.api.device_real_info(self.serial),
        )
        await self._refresh_camera_endpoint(
            "getAttributeSetting",
            self.api.device_attribute_settings(self.serial),
        )
        await self._refresh_camera_endpoint(
            "dataRealInfo",
            self.api.device_data_real_info(self.serial),
        )
        await self._refresh_camera_endpoint(
            "getDeviceEvents",
            self.api.device_events(self.serial),
        )
        await self._refresh_camera_endpoint(
            "getUpgrade",
            self.api.device_upgrade(self.serial),
        )
        await self._refresh_camera_endpoint(
            "workRecord",
            self.api.get_device_work_record(self.serial),
        )
        await self._refresh_camera_endpoint(
            "tutkInfo",
            self.api.tutk_info(),
        )

    async def _refresh_camera_endpoint(self, key: str, request) -> None:
        """Refresh one optional camera endpoint without breaking other data."""
        try:
            value = await request
        except PetLibroAPIError as err:
            _LOGGER.warning(
                "Failed to refresh %s for %s: %s",
                key,
                self.serial,
                err,
            )
            if key not in self._data:
                self.update_data({key: {} if key != "workRecord" else []})
            return
        self.update_data({key: value or ({} if key != "workRecord" else [])})

    def _camera_value(self, key: str) -> Any:
        """Return a camera value from the most specific available response."""
        for source in ("dataRealInfo", "realInfo", "getAttributeSetting"):
            data = self._data.get(source)
            if isinstance(data, dict) and key in data:
                return data[key]
        return self._data.get(key)

    @property
    def camera_id(self) -> str | None:
        """Return the per-device Kalay UID."""
        value = self._data.get("cameraId")
        return value if isinstance(value, str) and value else None

    @property
    def camera_auth_info(self) -> str | None:
        """Return the per-device Kalay authentication material."""
        value = self._camera_value("cameraAuthInfo")
        return value if isinstance(value, str) and value else None

    @property
    def tutk_user_token(self) -> str | None:
        """Return the account-scoped Kalay user token."""
        value = self._data.get("tutkInfo", {}).get("userToken")
        return value if isinstance(value, str) and value else None

    @property
    def tutk_app_url(self) -> str | None:
        """Return the account-scoped Kalay service URL."""
        value = self._data.get("tutkInfo", {}).get("appTutkUrl")
        return value if isinstance(value, str) and value else None

    @property
    def online(self) -> bool | None:
        """Return camera connectivity."""
        return _optional_bool(self._camera_value("online"))

    @property
    def wifi_ssid(self) -> str | None:
        """Return the camera Wi-Fi SSID."""
        value = self._camera_value("wifiSsid")
        return value if isinstance(value, str) and value else None

    @property
    def wifi_rssi(self) -> int | None:
        """Return camera Wi-Fi signal strength."""
        value = self._camera_value("wifiRssi")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def camera_switch(self) -> bool | None:
        return _optional_bool(self._camera_value("cameraSwitch"))

    @property
    def motion_detection_switch(self) -> bool | None:
        return _optional_bool(self._camera_value("motionDetectionSwitch"))

    @property
    def pet_detection_enabled(self) -> bool | None:
        return _optional_bool(self._camera_value("petDetectionSwitch"))

    @property
    def human_detection_enabled(self) -> bool | None:
        value = self._camera_value("enableHumanDetection")
        return _optional_bool(value)

    @property
    def sound_detection_switch(self) -> bool | None:
        return _optional_bool(self._camera_value("soundDetectionSwitch"))

    @property
    def motion_tracking_enabled(self) -> bool | None:
        return _optional_bool(self._camera_value("enableMotionTracking"))

    @property
    def talk_channel_active(self) -> bool | None:
        return _optional_bool(self._camera_value("talkChannelState"))

    @property
    def video_record_switch(self) -> bool | None:
        return _optional_bool(self._camera_value("videoRecordSwitch"))

    @property
    def resolution(self) -> str | None:
        value = self._camera_value("resolution")
        return value if isinstance(value, str) and value else None

    @property
    def night_vision(self) -> str | None:
        for key in ("nightVisionMode", "nightVision"):
            value = self._camera_value(key)
            if isinstance(value, str) and value:
                return value
        return None

    @property
    def video_record_mode(self) -> str | None:
        value = self._camera_value("videoRecordMode")
        return value if isinstance(value, str) and value else None

    @property
    def cloud_storage_state(self) -> str | int | None:
        for key in ("deviceAICloudStorageState", "deviceCloudStorageState"):
            value = self._camera_value(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return value
        return None

    @property
    def ptz_position(self) -> str | int | float | None:
        value = self._camera_value("ptzPosition")
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            return value
        return None

    @property
    def motion_detected(self) -> bool:
        """Return whether the current event response includes motion."""
        return self._has_event("MOTION_DETECTED")

    @property
    def sound_detected(self) -> bool:
        """Return whether the current event response includes sound."""
        return self._has_event("SOUND_DETECTED")

    def _has_event(self, event_key: str) -> bool:
        events_data = self._data.get("getDeviceEvents", {})
        if not isinstance(events_data, dict):
            return False
        if isinstance(events_data.get("data"), dict):
            events_data = events_data["data"]
        events = events_data.get("eventInfos")
        return isinstance(events, list) and any(
            isinstance(event, dict)
            and (event.get("eventKey") == event_key or event.get("eventType") == event_key)
            for event in events
        )

    @property
    def work_records(self) -> tuple[WorkRecord, ...]:
        """Return normalized newest-first work records."""
        return normalize_work_records(self._data.get("workRecord"))

    @property
    def latest_meal_record(self) -> WorkRecord | None:
        """Return the newest meal/eating record."""
        return first_record(
            self.work_records,
            "MEAL_RECORD",
            "PET_EATING_RECORD_EVENT",
        )

    @property
    def last_meal_time(self) -> datetime | None:
        record = self.latest_meal_record
        return record.timestamp if record else None

    @property
    def last_meal_intake(self) -> float | None:
        record = self.latest_meal_record
        if record is None:
            return None
        value = record.raw.get("intake")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @property
    def last_meal_duration(self) -> int | None:
        record = self.latest_meal_record
        if record is None:
            return None
        value = record.raw.get("duration")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def last_meal_pets(self) -> str | None:
        names = meal_pet_names(self.latest_meal_record)
        return ", ".join(names) if names else None

    @property
    def latest_thumbnail_url(self) -> str | None:
        """Return the newest temporary thumbnail URL."""
        record = next(
            (
                record
                for record in self.work_records
                if isinstance(record.raw.get("thumbnailSignedUrl"), str)
                and record.raw["thumbnailSignedUrl"]
            ),
            None,
        )
        return record.raw["thumbnailSignedUrl"] if record else None

    @property
    def latest_thumbnail_id(self) -> str | None:
        """Return a stable identity for the latest thumbnail record."""
        record = next(
            (
                record
                for record in self.work_records
                if isinstance(record.raw.get("thumbnailSignedUrl"), str)
                and record.raw["thumbnailSignedUrl"]
            ),
            None,
        )
        if record is None:
            return None
        return record.record_id or str(record.timestamp_ms)

    @property
    def update_release_notes(self) -> str | None:
        data = self._data.get("getUpgrade")
        return data.get("upgradeDesc") if isinstance(data, dict) else None

    @property
    def update_version(self) -> str | None:
        data = self._data.get("getUpgrade")
        return data.get("targetVersion") if isinstance(data, dict) else None

    @property
    def update_progress(self) -> float:
        data = self._data.get("getUpgrade")
        if not isinstance(data, dict):
            return 0.0
        value = data.get("progress")
        return float(value) if isinstance(value, (int, float)) else 0.0
