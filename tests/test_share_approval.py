"""Focused share approval tests without a full Home Assistant installation."""

import asyncio
import importlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


_saved_modules = {}


def _module(name: str, **attrs):
    _saved_modules.setdefault(name, sys.modules.get(name))
    module = types.ModuleType(name)
    module.__dict__.update(attrs)
    sys.modules[name] = module
    return module


ROOT = Path(__file__).resolve().parents[1]
_module("custom_components", __path__=[str(ROOT / "custom_components")])
_module(
    "custom_components.petlibro",
    __path__=[str(ROOT / "custom_components" / "petlibro")],
)
_module("homeassistant", __path__=[])
_module("homeassistant.core", HomeAssistant=object, callback=lambda function: function)
_module("homeassistant.config_entries", ConfigEntry=object)
_module("homeassistant.exceptions", HomeAssistantError=Exception)
_module("homeassistant.helpers", __path__=[])
_module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=object)
_module(
    "homeassistant.helpers.event",
    async_call_later=lambda *args: lambda: None,
    async_track_time_interval=lambda *args: lambda: None,
)
_module("homeassistant.util", __path__=[])
_module("homeassistant.util.dt", utcnow=lambda: None)
_module("aiohttp", ClientSession=object, ClientError=OSError)

from enum import StrEnum


class IntegrationSetting(StrEnum):
    AUTO_ACCEPT_SHARES = "auto_accept_shares"

    @property
    def default(self):
        return False


_module(
    "custom_components.petlibro.const",
    DOMAIN="petlibro",
    CONF_EMAIL="email",
    CONF_REGION="region",
    CONF_API_TOKEN="api_token",
    IntegrationSetting=IntegrationSetting,
)
for name in (
    "custom_components.petlibro.api",
    "custom_components.petlibro.share_approval",
    "custom_components.petlibro.exceptions",
):
    _saved_modules[name] = sys.modules.get(name)
api_module = importlib.import_module("custom_components.petlibro.api")
share_module = importlib.import_module("custom_components.petlibro.share_approval")
errors = importlib.import_module("custom_components.petlibro.exceptions")
for _name, _original in _saved_modules.items():
    if _original is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _original


class Response:
    def __init__(self, data, status=200):
        self.data = data
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def json(self):
        return self.data


class Transport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs["json"], kwargs["headers"].copy()))
        return self.responses.pop(0)


class FakeEntry:
    entry_id = "entry-1"
    data = {"email": "private@example.test", "region": "US"}

    def __init__(self, enabled=True):
        self.options = {"auto_accept_shares": enabled}
        self.callbacks = []
        self.async_start_reauth = MagicMock()

    def async_on_unload(self, unsubscribe):
        self.callbacks.append(unsubscribe)


class FakeHass:
    def __init__(self):
        self.data = {}
        self.config_entries = types.SimpleNamespace(async_schedule_reload=MagicMock())

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class ShareAPI:
    def __init__(self, rows=None):
        self.list_incoming_shares = AsyncMock(return_value=rows or [])
        self.accept_incoming_share = AsyncMock()


class TestShareAPI(unittest.IsolatedAsyncioTestCase):
    async def test_list_and_accept_request_bodies(self):
        transport = Transport(Response({"code": 0, "data": []}), Response({"code": 0, "data": None}))
        session = api_module.PetLibroSession("https://api.example/", transport, "email", "password", "US", "token")
        api = api_module.PetLibroAPI.__new__(api_module.PetLibroAPI)
        api.session = session
        self.assertEqual(await api.list_incoming_shares(), [])
        await api.accept_incoming_share(42)
        self.assertEqual(
            [(method, url, body) for method, url, body, _ in transport.requests],
            [
                ("POST", "https://api.example/device/deviceShare/myShareList", {"shareType": 2}),
                ("POST", "https://api.example/device/deviceShare/rec", {"shareId": 42, "rec": True}),
            ],
        )
        self.assertEqual(transport.requests[0][3]["token"], "token")
        self.assertNotIn("token", transport.requests[0][2])

    async def test_malformed_list_is_error(self):
        for data in (None, {}, {"records": []}):
            transport = Transport(Response({"code": 0, "data": data}))
            api = api_module.PetLibroAPI.__new__(api_module.PetLibroAPI)
            api.session = api_module.PetLibroSession("https://api.example/", transport, "email", "password", "US")
            with self.assertRaises(errors.PetLibroAPIError):
                await api.list_incoming_shares()

    async def test_retried_response_is_validated_for_all_callers(self):
        for response, exception in (
            ({"code": 42, "msg": "rejected"}, errors.PetLibroAPIRejected),
            ({"code": 1009}, errors.PetLibroAPIRejected),
            ({"unexpected": "format"}, errors.PetLibroAPIError),
        ):
            transport = Transport(Response({"code": 1009}), Response(response))
            session = api_module.PetLibroSession("https://api.example/", transport, "email", "password", "US")
            session.re_login = AsyncMock(return_value="new-token")
            with self.assertRaises(exception) as failure:
                await session.post("/test", json={})
            self.assertIs(type(failure.exception), exception)
            self.assertEqual(transport.requests[1][3]["token"], "new-token")
            self.assertEqual(session.re_login.await_count, 1)

    async def test_simultaneous_expired_requests_share_one_refresh(self):
        gate = asyncio.Event()

        class DelayedResponse(Response):
            async def json(self):
                await gate.wait()
                return self.data

        transport = Transport(
            DelayedResponse({"code": 1009}),
            DelayedResponse({"code": 1009}),
            Response({"code": 0, "data": {"ok": 1}}),
            Response({"code": 0, "data": {"ok": 2}}),
        )
        session = api_module.PetLibroSession(
            "https://api.example/", transport, "email", "password", "US", "old-token"
        )

        async def refresh():
            session.token = "new-token"
            return session.token

        session.re_login = AsyncMock(side_effect=refresh)
        requests = [
            asyncio.create_task(session.post("/device/deviceShare/rec", json={"shareId": share_id}))
            for share_id in (1, 2)
        ]
        await asyncio.sleep(0)
        self.assertEqual(len(transport.requests), 2)
        gate.set()
        self.assertEqual(await asyncio.gather(*requests), [{"ok": 1}, {"ok": 2}])
        session.re_login.assert_awaited_once()
        self.assertEqual(
            [headers["token"] for _, _, _, headers in transport.requests],
            ["old-token", "old-token", "new-token", "new-token"],
        )

    async def test_retry_rejects_http_error_and_login_failure(self):
        transport = Transport(Response({"code": 1009}), Response({"code": 0}, status=503))
        session = api_module.PetLibroSession("https://api.example/", transport, "email", "password", "US")
        session.re_login = AsyncMock(return_value="new-token")
        with self.assertRaises(errors.PetLibroAPIError) as failure:
            await session.post("/test", json={})
        self.assertIs(type(failure.exception), errors.PetLibroAPIError)

        transport = Transport()
        transport.post = MagicMock(return_value=Response({"code": 2}, status=200))
        session = api_module.PetLibroSession("https://api.example/", transport, "email", "password", "US")
        with self.assertRaises(errors.PetLibroAPIRejected) as failure:
            await session.re_login()
        self.assertEqual(failure.exception.code, 2)

    async def test_login_distinguishes_credentials_from_api_failures(self):
        for response, exception in (
            (Response({"code": 2}, status=401), errors.PetLibroInvalidAuth),
            (Response({"code": 42, "msg": "server error"}), errors.PetLibroAPIRejected),
            (Response({"code": 1009}), errors.PetLibroAPIRejected),
            (Response({"code": 0, "data": {}}), errors.PetLibroAPIError),
        ):
            transport = Transport(response)
            api = api_module.PetLibroAPI.__new__(api_module.PetLibroAPI)
            api.session = api_module.PetLibroSession(
                "https://api.example/", transport, "email", "password", "US"
            )
            api.region = "US"
            api.time_zone = "America/Chicago"
            with self.assertRaises(exception) as failure:
                await api.login("email", "password")
            self.assertIs(type(failure.exception), exception)
            if exception is errors.PetLibroAPIRejected:
                self.assertEqual(failure.exception.code, response.data["code"])
            self.assertEqual(len(transport.requests), 1)

        for response, exception in (
            (Response({"code": 2}, status=401), errors.PetLibroInvalidAuth),
            (Response({"code": 42}), errors.PetLibroAPIRejected),
            (Response({"invalid": "envelope"}), errors.PetLibroAPIError),
        ):
            transport = Transport()
            transport.post = MagicMock(return_value=response)
            session = api_module.PetLibroSession(
                "https://api.example/", transport, "email", "password", "US"
            )
            with self.assertRaises(exception) as failure:
                await session.re_login()
            self.assertIs(type(failure.exception), exception)

    async def test_ambiguous_write_network_failure_is_not_retried(self):
        transport = Transport()
        transport.request = MagicMock(side_effect=OSError("lost response"))
        session = api_module.PetLibroSession(
            "https://api.example/", transport, "email", "password", "US", "old-token"
        )
        session.re_login = AsyncMock(return_value="new-token")
        with self.assertRaises(OSError):
            await session.post("/device/deviceShare/rec", json={"shareId": 1})
        transport.request.assert_called_once()
        session.re_login.assert_not_awaited()

    async def test_relogin_persists_token_in_config_entry(self):
        transport = Transport()
        transport.post = MagicMock(return_value=Response({"code": 0, "data": {"token": "renewed"}}))
        session = api_module.PetLibroSession("https://api.example/", transport, "email", "password", "US")
        entry = types.SimpleNamespace(data={"api_token": "old"})
        config_entries = types.SimpleNamespace(async_update_entry=MagicMock())
        session.api = types.SimpleNamespace(
            config_entry=entry, hass=types.SimpleNamespace(config_entries=config_entries)
        )
        self.assertEqual(await session.re_login(), "renewed")
        config_entries.async_update_entry.assert_called_once_with(
            entry, data={"api_token": "renewed"}
        )

    async def test_retry_preserves_empty_list_and_null_compatibility(self):
        for data, expected in (([], []), (None, {})):
            transport = Transport(Response({"code": 1009}), Response({"code": 0, "data": data}))
            session = api_module.PetLibroSession("https://api.example/", transport, "email", "password", "US")
            session.re_login = AsyncMock(return_value="new-token")
            self.assertEqual(await session.post("/test", json={}), expected)


class TestShareApproval(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = FakeHass()
        self.entry = FakeEntry()

    async def test_disabled_schedules_nothing_and_never_lists(self):
        self.entry.options = {}
        api = ShareAPI([{"id": 1, "state": 1}])
        share_module.async_start_share_approval(self.hass, self.entry, api)
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        api.list_incoming_shares.assert_not_awaited()
        self.assertEqual(self.entry.callbacks, [])
        self.assertEqual(self.hass.data, {})

    async def test_pending_filter_and_one_reload(self):
        rows = [
            {"id": 1, "state": 1}, {"id": 2, "state": 1, "shareType": 2},
            {"id": True, "state": 1}, {"id": "3", "state": 1},
            {"id": 4, "state": "1"}, {"id": 5, "state": True},
            {"id": 6, "state": 2}, {"id": 7, "state": 1, "shareType": 1},
            {"id": 8, "state": 1, "direction": "outgoing"},
            {"id": 9, "state": 1, "type": 2, "direction": "incoming"},
            {"id": 1, "state": 1}, None,
        ]
        api = ShareAPI(rows)
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        self.assertEqual(
            [args.args[0] for args in api.accept_incoming_share.await_args_list], [1, 2, 9]
        )
        self.hass.config_entries.async_schedule_reload.assert_called_once_with("entry-1")

    async def test_reload_preserves_success_and_allows_next_new_invite(self):
        api = ShareAPI([{"id": 11, "state": 1}])
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        api.list_incoming_shares.return_value = [{"id": 11, "state": 1}, {"id": 12, "state": 1}]
        share_module.async_start_share_approval(self.hass, self.entry, api)
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        self.assertEqual(
            [args.args[0] for args in api.accept_incoming_share.await_args_list], [11, 12]
        )
        self.assertEqual(self.hass.config_entries.async_schedule_reload.call_count, 2)

    async def test_transient_retry_cap_and_no_duplicate_attempt_in_pass(self):
        api = ShareAPI([{"id": 20, "state": 1}, {"id": 20, "state": 1}])
        api.accept_incoming_share.side_effect = TimeoutError()
        for _ in range(5):
            await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        self.assertEqual(api.accept_incoming_share.await_count, 3)
        self.hass.config_entries.async_schedule_reload.assert_not_called()

    async def test_lost_acceptance_response_reconciles_accepted_state(self):
        api = ShareAPI([{"id": 21, "state": 1, "shareType": 2}])
        api.accept_incoming_share.side_effect = TimeoutError()
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        self.hass.config_entries.async_schedule_reload.assert_not_called()
        api.list_incoming_shares.return_value = [{"id": 21, "state": 2, "shareType": 2}]
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        api.accept_incoming_share.assert_awaited_once_with(21)
        self.hass.config_entries.async_schedule_reload.assert_called_once_with("entry-1")
        state = share_module._state_for(self.hass, self.entry)
        self.assertIn(21, state.accepted)
        self.assertNotIn(21, state.attempts)

    async def test_incoming_history_rows_do_not_warn(self):
        api = ShareAPI([{"id": state, "state": state, "shareType": 2} for state in (2, 3, 4, 6)])
        with patch.object(share_module._LOGGER, "warning") as warning:
            await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        warning.assert_not_called()
        api.accept_incoming_share.assert_not_awaited()
        self.hass.config_entries.async_schedule_reload.assert_not_called()

    async def test_rejected_id_suppressed(self):
        api = ShareAPI([{"id": 30, "state": 1}])
        api.accept_incoming_share.side_effect = errors.PetLibroAPIRejected(19)
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        share_module.async_start_share_approval(self.hass, self.entry, api)
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        self.assertEqual(api.accept_incoming_share.await_count, 1)

    async def test_bad_list_is_visible_and_next_check_can_recover(self):
        api = ShareAPI()
        api.list_incoming_shares.side_effect = [
            errors.PetLibroAPIError("Invalid incoming share list response format"),
            [{"id": 35, "state": 1}],
        ]
        with self.assertLogs(share_module._LOGGER, level="WARNING") as logs:
            await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        self.assertIn("Unable to check incoming shares", logs.output[0])
        api.accept_incoming_share.assert_not_awaited()
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        api.accept_incoming_share.assert_awaited_once_with(35)

    async def test_background_auth_starts_reauth_once(self):
        api = ShareAPI()
        api.list_incoming_shares.side_effect = errors.PetLibroInvalidAuth("reauth")
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        self.entry.async_start_reauth.assert_called_once_with(self.hass)

    async def test_auth_failure_does_not_exhaust_invitation_attempts(self):
        api = ShareAPI([{"id": 40, "state": 1}])
        api.accept_incoming_share.side_effect = errors.PetLibroInvalidAuth("reauth")
        for _ in range(4):
            await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        self.assertEqual(api.accept_incoming_share.await_count, 1)
        self.assertEqual(
            share_module._state_for(self.hass, self.entry).attempts, {}
        )
        api.accept_incoming_share.side_effect = None
        share_module.async_start_share_approval(self.hass, self.entry, api)
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        self.assertEqual(api.accept_incoming_share.await_count, 2)

    async def test_single_flight(self):
        gate = asyncio.Event()
        api = ShareAPI()

        async def blocked_list():
            await gate.wait()
            return []

        api.list_incoming_shares.side_effect = blocked_list
        first = asyncio.create_task(share_module.async_check_incoming_shares(self.hass, self.entry, api))
        await asyncio.sleep(0)
        await share_module.async_check_incoming_shares(self.hass, self.entry, api)
        gate.set()
        await first
        api.list_incoming_shares.assert_awaited_once()

    async def test_scheduling_and_permanent_removal(self):
        callbacks = []

        def call_later(_hass, delay, callback):
            callbacks.append((delay, callback))
            return MagicMock()

        def track_interval(_hass, callback, interval):
            callbacks.append((interval, callback))
            return MagicMock()

        api = ShareAPI()
        with patch.object(share_module, "async_call_later", call_later), patch.object(
            share_module, "async_track_time_interval", track_interval
        ):
            share_module.async_start_share_approval(self.hass, self.entry, api)
        self.assertEqual(len(self.entry.callbacks), 3)
        self.assertEqual(callbacks[0][0], 0)
        self.assertEqual(callbacks[1][0].total_seconds(), 300)
        callbacks[0][1](None)
        await asyncio.sleep(0)
        for unsubscribe in self.entry.callbacks:
            unsubscribe()
        share_module.async_remove_share_approval(self.hass, self.entry)
        self.assertNotIn("petlibro_share_auto_approval", self.hass.data)


if __name__ == "__main__":
    unittest.main()
