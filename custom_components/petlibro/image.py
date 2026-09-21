"""Support for PETLIBRO image entities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import asyncio
import logging
from urllib.parse import urlparse

import aiohttp
from homeassistant.components.image import ImageEntity, ImageEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.dt import utcnow

from .const import DOMAIN
from .devices import Device
from .devices.feeders.granary_smart_camera_feeder import GranarySmartCameraFeeder
from .entity import PetLibroEntity, PetLibroEntityDescription, _DeviceT
from .hub import PetLibroHub
from .pets.entity import PL_PetImageEntity

_LOGGER = logging.getLogger(__name__)
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _image_content_type(data: bytes) -> str | None:
    """Return an image type from trusted file signatures."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _trusted_media_url(url: str) -> bool:
    """Return whether a worklog media URL uses a known PETLIBRO host shape."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    if host == "petlibro.com" or host.endswith(".petlibro.com"):
        return True
    return (
        host.startswith("prod-pla")
        and "cloud-storage.s3." in host
        and host.endswith(".amazonaws.com")
        and parsed.path.startswith("/member/")
    )


@dataclass(frozen=True)
class PetLibroImageEntityDescription(
    ImageEntityDescription,
    PetLibroEntityDescription[_DeviceT],
):
    """Describe a PETLIBRO device image."""

    image_url_fn: Callable[[_DeviceT], str | None] = lambda _: None
    image_id_fn: Callable[[_DeviceT], str | None] = lambda _: None


class PetLibroImageEntity(PetLibroEntity[_DeviceT], ImageEntity):
    """PETLIBRO worklog image entity."""

    entity_description: PetLibroImageEntityDescription[_DeviceT]

    def __init__(self, device, hub, description) -> None:
        super().__init__(device, hub, description)
        ImageEntity.__init__(self, hub.hass)
        self._image_bytes: bytes | None = None
        self._image_id: str | None = None
        self._rejected_image_id: str | None = None
        self._fetching = False

    @property
    def available(self) -> bool:
        """Return whether an image has been fetched successfully."""
        return super().available and self._image_bytes is not None

    async def async_added_to_hass(self) -> None:
        """Start fetching the current worklog image."""
        await super().async_added_to_hass()
        self._schedule_image_update()

    async def async_image(self) -> bytes | None:
        """Return cached image bytes."""
        return self._image_bytes

    def _handle_coordinator_update(self) -> None:
        """Fetch a new image when the source worklog record changes."""
        self._schedule_image_update()
        super()._handle_coordinator_update()

    def _schedule_image_update(self) -> None:
        image_id = self.entity_description.image_id_fn(self.device)
        if self._fetching or image_id is None or image_id == self._rejected_image_id:
            return
        if image_id == self._image_id and self._image_bytes is not None:
            return
        self._fetching = True
        self.hass.async_create_task(self._async_update_image(image_id))

    async def _async_update_image(self, image_id: str) -> None:
        """Fetch a temporary signed image URL into memory."""
        try:
            url = self.entity_description.image_url_fn(self.device)
            if not isinstance(url, str) or not _trusted_media_url(url):
                _LOGGER.warning(
                    "Blocked untrusted PETLIBRO media host for %s",
                    self.device.serial,
                )
                return

            async with self.hub.api.session.websession.get(
                url,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    _LOGGER.warning(
                        "PETLIBRO media request failed for %s with status %s",
                        self.device.serial,
                        response.status,
                    )
                    return
                content_length = response.content_length
                if content_length is not None and content_length > _MAX_IMAGE_BYTES:
                    _LOGGER.warning(
                        "PETLIBRO media response for %s exceeded the size limit",
                        self.device.serial,
                    )
                    return

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    total += len(chunk)
                    if total > _MAX_IMAGE_BYTES:
                        _LOGGER.warning(
                            "PETLIBRO media response for %s exceeded the size limit",
                            self.device.serial,
                        )
                        return
                    chunks.append(chunk)

            image_bytes = b"".join(chunks)
            if not image_bytes:
                return
            content_type = _image_content_type(image_bytes)
            if content_type is None:
                self._rejected_image_id = image_id
                _LOGGER.warning(
                    "PETLIBRO media response for %s contained unsupported image data",
                    self.device.serial,
                )
                return
            self._image_bytes = image_bytes
            self._image_id = image_id
            self._rejected_image_id = None
            self._attr_content_type = content_type
            self._cached_image = None
            self._attr_image_last_updated = utcnow()
            self.async_write_ha_state()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.warning(
                "Failed to fetch PETLIBRO media for %s: %s",
                self.device.serial,
                err,
            )
        finally:
            self._fetching = False


DEVICE_IMAGE_MAP: dict[type[Device], list[PetLibroImageEntityDescription]] = {
    GranarySmartCameraFeeder: [
        PetLibroImageEntityDescription[GranarySmartCameraFeeder](
            key="latest_event_thumbnail",
            translation_key="latest_event_thumbnail",
            name="Latest Event",
            image_url_fn=lambda device: device.latest_thumbnail_url,
            image_id_fn=lambda device: device.latest_thumbnail_id,
        ),
    ],
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PETLIBRO images."""
    hub: PetLibroHub = hass.data[DOMAIN].get(entry.entry_id)
    if not hub:
        _LOGGER.error("Hub not found for entry: %s", entry.entry_id)
        return

    entities = [
        PetLibroImageEntity(device, hub, description)
        for device in hub.devices.values()
        for device_type, descriptions in DEVICE_IMAGE_MAP.items()
        if isinstance(device, device_type)
        for description in descriptions
    ]

    for pet in hub.pets.values():
        entities.extend(pet.entities(PL_PetImageEntity, hub))

    if entities:
        async_add_entities(entities)
