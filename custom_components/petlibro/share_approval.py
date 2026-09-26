"""Opt-in approval of incoming device shares."""

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_time_interval

from .api import PetLibroAPI
from .const import CONF_EMAIL, CONF_REGION, DOMAIN, IntegrationSetting
from .exceptions import PetLibroAPIRejected, PetLibroInvalidAuth

_LOGGER = logging.getLogger(__name__)
_STATE_KEY = f"{DOMAIN}_share_auto_approval"
_INTERVAL = timedelta(minutes=5)
_MAX_ATTEMPTS = 3


@dataclass
class ShareApprovalState:
    """Runtime state retained across reloads of the same account entry."""

    account: tuple[str, str]
    accepted: set[int] = field(default_factory=set)
    rejected: set[int] = field(default_factory=set)
    attempts: dict[int, int] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reload_scheduled: bool = False
    reload_needed: bool = False
    reauth_started: bool = False
    active: bool = True


def _state_for(hass: HomeAssistant, entry: ConfigEntry) -> ShareApprovalState:
    states: dict[str, ShareApprovalState] = hass.data.setdefault(_STATE_KEY, {})
    account = (entry.data[CONF_REGION], entry.data[CONF_EMAIL])
    state = states.get(entry.entry_id)
    if state is None or state.account != account:
        state = states[entry.entry_id] = ShareApprovalState(account)
    return state


def _incoming_share(row: object) -> tuple[int, int] | None:
    if not isinstance(row, dict) or type(row.get("id")) is not int:
        return None
    if type(row.get("state")) is not int or row["state"] not in (1, 2, 3, 4, 6):
        return None
    for key in ("shareType", "share_type", "type"):
        if key in row and (type(row[key]) is not int or row[key] != 2):
            return None
    for key in ("direction", "shareDirection"):
        if key in row:
            direction = row[key]
            if not (
                (type(direction) is int and direction == 2)
                or (type(direction) is str and direction in ("incoming", "received"))
            ):
                return None
    return row["id"], row["state"]


async def async_check_incoming_shares(
    hass: HomeAssistant, entry: ConfigEntry, api: PetLibroAPI
) -> None:
    """Check shares once; never overlap checks for this account."""
    if not entry.options.get(
        IntegrationSetting.AUTO_ACCEPT_SHARES,
        IntegrationSetting.AUTO_ACCEPT_SHARES.default,
    ):
        return

    state = _state_for(hass, entry)
    if state.reauth_started or state.lock.locked():
        return
    async with state.lock:
        accepted = 0
        malformed = 0
        nonpending = 0
        seen: set[int] = set()
        try:
            rows = await api.list_incoming_shares()
            for row in rows:
                share = _incoming_share(row)
                if share is None:
                    malformed += 1
                    continue
                share_id, share_state = share
                if share_id in seen:
                    continue
                seen.add(share_id)
                if share_state != 1:
                    nonpending += 1
                    if (
                        share_state == 2
                        and share_id in state.attempts
                        and share_id not in state.rejected
                        and share_id not in state.accepted
                    ):
                        state.accepted.add(share_id)
                        state.attempts.pop(share_id, None)
                        accepted += 1
                    continue
                if (
                    share_id in state.accepted
                    or share_id in state.rejected
                    or state.attempts.get(share_id, 0) >= _MAX_ATTEMPTS
                ):
                    continue
                # Count before sending: a timeout/cancellation may be post-mutation.
                state.attempts[share_id] = state.attempts.get(share_id, 0) + 1
                try:
                    await api.accept_incoming_share(share_id)
                except PetLibroInvalidAuth:
                    state.attempts[share_id] -= 1
                    if not state.attempts[share_id]:
                        state.attempts.pop(share_id)
                    raise
                except PetLibroAPIRejected as err:
                    state.rejected.add(share_id)
                    _LOGGER.warning("Incoming share permanently rejected by API (code %s)", err.code)
                except Exception as err:
                    if state.attempts[share_id] == _MAX_ATTEMPTS:
                        _LOGGER.warning(
                            "Incoming share suppressed after %s transient attempts (%s)",
                            _MAX_ATTEMPTS, type(err).__name__,
                        )
                    else:
                        _LOGGER.warning(
                            "Incoming share acceptance failed transiently (%s)", type(err).__name__
                        )
                else:
                    state.accepted.add(share_id)
                    state.attempts.pop(share_id, None)
                    accepted += 1
            if malformed:
                _LOGGER.warning("Skipped %s malformed or nonincoming share rows", malformed)
            if nonpending:
                _LOGGER.debug("Ignored %s nonpending incoming share rows", nonpending)
        except PetLibroInvalidAuth:
            _LOGGER.warning("Incoming share approval requires PETLIBRO reauthentication")
            if not state.reauth_started:
                try:
                    entry.async_start_reauth(hass)
                except Exception as err:
                    _LOGGER.error("Could not start PETLIBRO reauthentication (%s)", type(err).__name__)
                else:
                    state.reauth_started = True
        except Exception as err:
            _LOGGER.warning("Unable to check incoming shares (%s)", type(err).__name__)
        finally:
            if accepted:
                state.reload_needed = True
            if state.active and state.reload_needed and not state.reload_scheduled:
                state.reload_scheduled = True
                try:
                    hass.config_entries.async_schedule_reload(entry.entry_id)
                except Exception as err:
                    state.reload_scheduled = False
                    _LOGGER.error("Could not schedule share reload (%s)", type(err).__name__)
                else:
                    _LOGGER.info("Accepted %s incoming device share(s); scheduled one reload", accepted)


def async_start_share_approval(hass: HomeAssistant, entry: ConfigEntry, api: PetLibroAPI) -> None:
    """Schedule the first check after setup, then check every five minutes."""
    if not entry.options.get(
        IntegrationSetting.AUTO_ACCEPT_SHARES,
        IntegrationSetting.AUTO_ACCEPT_SHARES.default,
    ):
        return
    state = _state_for(hass, entry)
    state.reload_scheduled = False
    state.reload_needed = False
    state.reauth_started = False
    state.active = True
    tasks: set[asyncio.Task] = set()

    @callback
    def schedule_check(_now) -> None:
        if state.lock.locked():
            return

        async def check_safely() -> None:
            try:
                await async_check_incoming_shares(hass, entry, api)
            except Exception as err:
                _LOGGER.error("Unexpected share approval failure (%s)", type(err).__name__)

        task = hass.async_create_task(check_safely())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    entry.async_on_unload(async_call_later(hass, 0, schedule_check))
    entry.async_on_unload(async_track_time_interval(hass, schedule_check, _INTERVAL))

    @callback
    def stop_checks() -> None:
        state.active = False
        for task in tasks:
            task.cancel()

    entry.async_on_unload(stop_checks)


def async_remove_share_approval(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Forget state when the config entry is permanently removed."""
    if states := hass.data.get(_STATE_KEY):
        states.pop(entry.entry_id, None)
        if not states:
            hass.data.pop(_STATE_KEY, None)
