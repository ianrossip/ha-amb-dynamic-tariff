"""Data coordinator for AMB Dynamic Tariff."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import logging
from typing import Any

from aiohttp import ClientError

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    API_URL,
    COLOR_HIGH,
    COLOR_LOW,
    NAME,
    POST_CHANGE_REFRESH_DELAY,
    TARIFF_HIGH,
    TARIFF_LOW,
    TARIFF_UNKNOWN,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)
DAILY_REFRESH_TIME = time(0, 5)


def _tariff_from_color(color):
    if not color:
        return TARIFF_UNKNOWN
    color = color.upper()
    return (
        TARIFF_LOW
        if color == COLOR_LOW
        else TARIFF_HIGH
        if color == COLOR_HIGH
        else TARIFF_UNKNOWN
    )


def _parse_dt(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


def _dedupe_points(labels, colors):
    points = {}
    for label, color in zip(labels, colors, strict=False):
        try:
            points[_parse_dt(label)] = _tariff_from_color(color)
        except (TypeError, ValueError):
            continue
    return [{"start": key, "tariff": value} for key, value in sorted(points.items())]


def _compress_periods(points):
    if not points:
        return []
    periods = []
    start = points[0]["start"]
    tariff = points[0]["tariff"]
    previous = start
    for point in points[1:]:
        when = point["start"]
        new = point["tariff"]
        if new != tariff:
            periods.append({"start": start, "end": when, "tariff": tariff})
            start = when
            tariff = new
        previous = when
    end = previous
    if len(points) == 1 or end <= start:
        end = start + timedelta(minutes=15)
    periods.append({"start": start, "end": end, "tariff": tariff})
    return periods


class AmbTariffCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate cloud schedule refreshes and exact local tariff transitions."""

    def __init__(self, hass):
        super().__init__(hass, _LOGGER, name=NAME, update_interval=UPDATE_INTERVAL)
        self._session = async_get_clientsession(hass)
        self._points: list[dict[str, Any]] = []
        self._cancel_transition = None
        self._cancel_post_change_refresh = None
        self._cancel_daily_refresh = None

    async def async_shutdown(self) -> None:
        """Cancel scheduled callbacks when the config entry is unloaded."""
        self._cancel_all_scheduled_callbacks()

    def _cancel_all_scheduled_callbacks(self) -> None:
        self._cancel_transition_timer()
        self._cancel_post_change_refresh_timer()
        self._cancel_daily_refresh_timer()

    def _cancel_transition_timer(self) -> None:
        if self._cancel_transition is not None:
            self._cancel_transition()
            self._cancel_transition = None

    def _cancel_post_change_refresh_timer(self) -> None:
        if self._cancel_post_change_refresh is not None:
            self._cancel_post_change_refresh()
            self._cancel_post_change_refresh = None

    def _cancel_daily_refresh_timer(self) -> None:
        if self._cancel_daily_refresh is not None:
            self._cancel_daily_refresh()
            self._cancel_daily_refresh = None

    async def _fetch_day(self, day):
        request_dt = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
        payload = {
            "date": request_dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        }
        try:
            async with self._session.post(
                API_URL,
                json=payload,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "HomeAssistant-AMB-Dynamic-Tariff/0.1.5-dev.2",
                },
                timeout=15,
            ) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
        except (ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Error communicating with AMB: {err}") from err

        labels = data.get("labels")
        colors = data.get("bgColors")
        if not isinstance(labels, list) or not isinstance(colors, list):
            raise UpdateFailed("Unexpected response from AMB: labels/bgColors missing")
        return _dedupe_points(labels, colors)

    def _derive_state(self, now: datetime) -> dict[str, Any]:
        current = TARIFF_UNKNOWN
        current_start = None
        next_change = None
        next_tariff = TARIFF_UNKNOWN

        for idx, point in enumerate(self._points):
            if point["start"] <= now:
                current = point["tariff"]
                current_start = point["start"]
                for later in self._points[idx + 1 :]:
                    if later["tariff"] != current:
                        next_change = later["start"]
                        next_tariff = later["tariff"]
                        break

        return {
            "current_tariff": current,
            "current_since": current_start,
            "next_tariff": next_tariff,
            "next_change": next_change,
        }

    def _build_data(self, now: datetime, last_update: datetime) -> dict[str, Any]:
        state = self._derive_state(now)
        today = now.date()
        return {
            **state,
            "today_periods": self._periods_for_local_date(self._points, today),
            "tomorrow_periods": self._periods_for_local_date(
                self._points, today + timedelta(days=1)
            ),
            "last_update": last_update,
        }

    def _schedule_next_transition(self) -> None:
        """Schedule only the next local tariff transition."""
        self._cancel_transition_timer()

        if not self.data:
            return

        next_change = self.data.get("next_change")
        if next_change is None or next_change <= dt_util.now():
            return

        self._cancel_transition = async_track_point_in_time(
            self.hass, self._async_handle_transition, next_change
        )

    def _schedule_post_change_refresh(self, transition_time: datetime) -> None:
        """Schedule an independent AMB verification after a tariff change."""
        self._cancel_post_change_refresh_timer()
        refresh_at = transition_time + timedelta(seconds=POST_CHANGE_REFRESH_DELAY)
        self._cancel_post_change_refresh = async_track_point_in_time(
            self.hass, self._async_post_change_refresh, refresh_at
        )

    def _schedule_daily_refresh(self) -> None:
        """Schedule an independent AMB refresh every day at 00:05 local time."""
        self._cancel_daily_refresh_timer()

        now = dt_util.now()
        refresh_at = datetime.combine(
            now.date(), DAILY_REFRESH_TIME, tzinfo=dt_util.DEFAULT_TIME_ZONE
        )
        if refresh_at <= now:
            refresh_at += timedelta(days=1)

        self._cancel_daily_refresh = async_track_point_in_time(
            self.hass, self._async_daily_refresh, refresh_at
        )

    async def _async_handle_transition(self, _now) -> None:
        """Apply a known tariff change locally, then verify it with AMB."""
        self._cancel_transition = None
        now = dt_util.now()

        if self.data:
            self.async_set_updated_data(
                self._build_data(now, self.data["last_update"])
            )

        # These timers are independent: scheduling the next transition must
        # never cancel the pending post-change cloud verification.
        self._schedule_next_transition()
        self._schedule_post_change_refresh(now)

    async def _async_post_change_refresh(self, _now) -> None:
        """Verify the schedule with AMB shortly after a local transition."""
        self._cancel_post_change_refresh = None
        await self.async_request_refresh()

    async def _async_daily_refresh(self, _now) -> None:
        """Refresh AMB shortly after midnight and schedule the next day."""
        self._cancel_daily_refresh = None
        try:
            await self.async_request_refresh()
        finally:
            self._schedule_daily_refresh()

    async def _async_update_data(self):
        today = dt_util.now().date()
        days = [today - timedelta(days=1), today, today + timedelta(days=1)]
        fetched = await self._async_fetch_days(days)

        all_points = []
        for points in fetched.values():
            all_points.extend(points)

        merged = {point["start"]: point["tariff"] for point in all_points}
        self._points = [
            {"start": key, "tariff": value} for key, value in sorted(merged.items())
        ]

        now = dt_util.now()
        data = self._build_data(now, dt_util.utcnow())

        # DataUpdateCoordinator assigns the returned data after this method.
        # Re-arm only the transition and daily timers; neither may cancel the
        # independent post-change verification timer.
        self.hass.loop.call_soon(self._schedule_next_transition)
        self.hass.loop.call_soon(self._schedule_daily_refresh)
        return data

    async def _async_fetch_days(self, days):
        result = {}
        for day in days:
            result[day] = await self._fetch_day(day)
        return result

    def _periods_for_local_date(self, points, local_date):
        tz = dt_util.DEFAULT_TIME_ZONE
        start = datetime.combine(local_date, datetime.min.time(), tzinfo=tz)
        end = start + timedelta(days=1)
        relevant = []
        tariff = TARIFF_UNKNOWN
        for point in points:
            if point["start"] <= start:
                tariff = point["tariff"]
            elif point["start"] < end:
                relevant.append(point)
        periods = _compress_periods([{"start": start, "tariff": tariff}] + relevant)
        if periods:
            periods[-1]["end"] = end
        return periods


def format_periods(periods):
    return [
        {
            "start": dt_util.as_local(p["start"]).isoformat(),
            "end": dt_util.as_local(p["end"]).isoformat(),
            "start_time": dt_util.as_local(p["start"]).strftime("%H:%M"),
            "end_time": dt_util.as_local(p["end"]).strftime("%H:%M"),
            "tariff": p["tariff"],
        }
        for p in periods
    ]
