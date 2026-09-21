import aiohttp
from logging import getLogger

from ...exceptions import PetLibroAPIError
from .granary_smart_camera_feeder import GranarySmartCameraFeeder

_LOGGER = getLogger(__name__)


def _numeric_value(value: object) -> float | None:
    """Return an API number as a float without treating booleans as numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class Granary2VisionFeeder(GranarySmartCameraFeeder):
    """Represents the Granary 2 Vision feeder.

    Subclass of GranarySmartCameraFeeder so the device loads and
    reuses its entities. Can be split into a standalone class if desired.
    """

    async def refresh(self):
        """Refresh the device data from the API."""
        await super().refresh()
        try:
            free_feeding_setting = await self.api.device_get_free_feeding_setting(self.serial)
            self.update_data({
                "freeFeedingSetting": free_feeding_setting or {},
            })
        except PetLibroAPIError as err:
            _LOGGER.error(f"Error refreshing free feeding setting for Granary2VisionFeeder: {err}")

    @property
    def night_vision(self) -> str | None:
        """Return the current night vision mode.

        Unlike the Granary Smart Camera Feeder, this device reports night
        vision under getAttributeSetting.nightVisionMode; realInfo.nightVision
        is always null.
        """
        return super().night_vision

    @property
    def left_food_low(self) -> bool | None:
        """Return True if the left grain warehouse is low, or None if this unit doesn't report it."""
        if not self.supports_dual_bowl:
            return None
        value = self._data.get("realInfo", {}).get("leftWarehouseSurplusGrain")
        if value is None:
            return None
        return not bool(value)

    @property
    def right_food_low(self) -> bool | None:
        """Return True if the right grain warehouse is low, or None if this unit doesn't report it."""
        if not self.supports_dual_bowl:
            return None
        value = self._data.get("realInfo", {}).get("rightWarehouseSurplusGrain")
        if value is None:
            return None
        return not bool(value)

    @property
    def pet_detection_enabled(self) -> bool | None:
        """Return whether AI pet detection is enabled."""
        return super().pet_detection_enabled

    @property
    def human_detection_enabled(self) -> bool | None:
        """Return whether AI human detection is enabled."""
        return super().human_detection_enabled

    @property
    def talk_channel_active(self) -> bool | None:
        """Return whether a 2-way talk session is currently active."""
        return super().talk_channel_active

    @property
    def radar_sensing_level(self) -> str:
        """Return the current radar sensing/trigger level."""
        return self._data.get("realInfo", {}).get("radarSensingLevel", "unknown")

    @property
    def free_feeding_per_grain(self) -> float | None:
        """Return the amount dispensed per Free Feeding (Smart Feed) top-up."""
        value = self._data.get("freeFeedingSetting", {}).get("freePerGrainNum")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def free_feeding_daily_max(self) -> float | None:
        """Return the max number of Free Feeding top-ups allowed per day."""
        value = self._data.get("freeFeedingSetting", {}).get("freeDailyMaxNum")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def free_feeding_leftover_weight(self) -> float | None:
        """Return the minimum food-left threshold that pauses Free Feeding."""
        value = self._data.get("freeFeedingSetting", {}).get("freeLeftoverWeight")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def free_feeding_wait_seconds(self) -> float | None:
        """Return the wait time (seconds) Free Feeding uses between checks."""
        value = self._data.get("freeFeedingSetting", {}).get("freeWaitSeconds")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def feeding_mode(self) -> str | None:
        """Return the device's current feeding mode: 'FREE' (Smart Feed) or 'PLAN' (schedule-based)."""
        return self._data.get("dataRealInfo", {}).get("feedingMode")

    @property
    def bowl_mode(self) -> str | None:
        """Return the configured bowl mode."""
        value = self._data.get("bowlMode")
        if not isinstance(value, str):
            value = self._data.get("dataRealInfo", {}).get("bowlMode")
        return value if isinstance(value, str) else None

    @property
    def supports_single_bowl(self) -> bool:
        """Return whether this product uses combined single-bowl telemetry."""
        return self.bowl_mode == "SINGLE_BOWL"

    @property
    def supports_dual_bowl(self) -> bool:
        """Return whether this product reports separate left/right bowl telemetry."""
        return self.bowl_mode not in (None, "SINGLE_BOWL")

    @property
    def remaining_food_weight(self) -> float | None:
        """Return the food remaining in a single bowl, in grams."""
        if not self.supports_single_bowl:
            return None
        return _numeric_value(self._data.get("dataRealInfo", {}).get("remainingGrain"))

    @property
    def left_remaining_food_weight(self) -> float | None:
        """Return the food remaining in the left bowl, in grams."""
        if not self.supports_dual_bowl:
            return None
        return _numeric_value(self._data.get("dataRealInfo", {}).get("leftRemainingGrain"))

    @property
    def right_remaining_food_weight(self) -> float | None:
        """Return the food remaining in the right bowl, in grams."""
        if not self.supports_dual_bowl:
            return None
        return _numeric_value(self._data.get("dataRealInfo", {}).get("rightRemainingGrain"))

    @property
    def auto_feed_max_weight(self) -> float | None:
        """Return the configured automatic-feed bowl maximum, in grams."""
        return _numeric_value(self._data.get("dataRealInfo", {}).get("autoFeedMaxWeight"))

    @property
    def max_feedable(self) -> float | None:
        """Return the number of additional portions feedable into a single bowl."""
        if not self.supports_single_bowl:
            return None
        return _numeric_value(self._data.get("dataRealInfo", {}).get("maxFeedable"))

    @property
    def left_max_feedable(self) -> float | None:
        """Return the number of additional portions feedable into the left bowl."""
        if not self.supports_dual_bowl:
            return None
        return _numeric_value(self._data.get("dataRealInfo", {}).get("leftMaxFeedable"))

    @property
    def right_max_feedable(self) -> float | None:
        """Return the number of additional portions feedable into the right bowl."""
        if not self.supports_dual_bowl:
            return None
        return _numeric_value(self._data.get("dataRealInfo", {}).get("rightMaxFeedable"))

    @property
    def portion_to_gram_ratio(self) -> float | None:
        """Return the number of grams represented by one feeder portion."""
        return _numeric_value(self._data.get("dataRealInfo", {}).get("portionToGramRatio"))

    @property
    def auto_stop_feed_enabled(self) -> bool | None:
        """Return whether feeding stops at the configured bowl maximum."""
        value = self._data.get("dataRealInfo", {}).get("autoStopFeedSwitch")
        return value if isinstance(value, bool) else None

    async def set_free_feeding_mode(self) -> None:
        """Enable Free Feeding (Smart Feed) mode."""
        _LOGGER.debug(f"Enabling Free Feeding mode for {self.serial}")
        try:
            await self.api.set_feeding_mode(self.serial, "FREE")
            await self.refresh()
        except aiohttp.ClientError as err:
            _LOGGER.error(f"Failed to enable Free Feeding mode for {self.serial}: {err}")
            raise PetLibroAPIError(f"Error enabling Free Feeding mode: {err}")

    async def set_plan_feeding_mode(self) -> None:
        """Switch back to schedule-based (Feeding Plan) mode."""
        _LOGGER.debug(f"Enabling Plan feeding mode for {self.serial}")
        try:
            await self.api.set_feeding_mode(self.serial, "PLAN")
            await self.refresh()
        except aiohttp.ClientError as err:
            _LOGGER.error(f"Failed to enable Plan feeding mode for {self.serial}: {err}")
            raise PetLibroAPIError(f"Error enabling Plan feeding mode: {err}")
