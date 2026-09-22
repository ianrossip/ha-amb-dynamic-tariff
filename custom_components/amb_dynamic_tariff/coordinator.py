"""Data coordinator for AMB Dynamic Tariff."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
import logging
from typing import Any

from aiohttp import ClientError

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_interval
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
FIXED_REFRESH_TIMES = (time(0, 5), time(12, 5))


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
        # Cloud refresh timing is managed explicitly below. This keeps hourly
        # polling independent from entity listeners and from tariff transitions.
        super().__init__(hass, _LOGGER, name=NAME, update_interval=None)
        self._session = async_get_clientsession(hass)
        self._points: list[dict[str, Any]] = []
        self._cancel_transition = None
        self._cancel_post_change_refresh = None
        self._cancel_hourly_refresh = None
        self._cancel_fixed_refresh = None

    async def async_shutdown(self) -> None:
        """Cancel scheduled callbacks when the config entry is unloaded."""
        self._cancel_transition_timer()
        self._cancel_post_change_refresh_timer()
        self._cancel_hourly_refresh_timer()
        self._cancel_fixed_refresh_timer()

    def _cancel_transition_timer(self) -> None:
        if self._cancel_transition is not None:
            self._cancel_transition()
            self._cancel_transition = None

    def _cancel_post_change_refresh_timer(self) -> None:
        if self._cancel_post_change_refresh is not None:
            self._cancel_post_change_refresh()
            self._cancel_post_change_refresh = None

    def _cancel_hourly_refresh_timer(self) -> None:
        if self._cancel_hourly_refresh is not None:
            self._cancel_hourly_refresh()
            self._cancel_hourly_refresh = None

    def _cancel_fixed_refresh_timer(self) -> None:
        if self._cancel_fixed_refresh is not None:
            self._cancel_fixed_refresh()
            self._cancel_fixed_refresh = None

    async def _fetch_day(self, day, reference_utc: datetime):
        # Match the AMB website: keep the current UTC clock time and change only
        # the requested calendar date for yesterday/today/tomorrow.
        request_dt = datetime(
            day.year,
            day.month,
            day.day,
            reference_utc.hour,
            reference_utc.minute,
            reference_utc.second,
            reference_utc.microsecond,
            tzinfo=timezone.utc,
        )
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
                    "User-Agent": "HomeAssistant-AMB-Dynamic-Tariff/0.1.5-dev.3",
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

    def _build_data(
        self, now: datetime, last_update: datetime, tomorrow_published: bool
    ) -> dict[str, Any]:
        state = self._derive_state(now)
        today = now.date()
        return {
            **state,
            "yesterday_periods": self._periods_for_local_date(
                self._points, today - timedelta(days=1)
            ),
            "today_periods": self._periods_for_local_date(self._points, today),
            "tomorrow_periods": (
                self._periods_for_local_date(self._points, today + timedelta(days=1))
                if tomorrow_published
                else None
            ),
            "tomorrow_published": tomorrow_published,
            "last_update": last_update,
        }

    def _schedule_next_transition(self) -> None:
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
        self._cancel_post_change_refresh_timer()
        refresh_at = transition_time + timedelta(seconds=POST_CHANGE_REFRESH_DELAY)
        self._cancel_post_change_refresh = async_track_point_in_time(
            self.hass, self._async_post_change_refresh, refresh_at
        )

    def _schedule_hourly_refresh(self) -> None:
        if self._cancel_hourly_refresh is None:
            self._cancel_hourly_refresh = async_track_time_interval(
                self.hass, self._async_hourly_refresh, UPDATE_INTERVAL
            )

    def _schedule_fixed_refresh(self) -> None:
        if self._cancel_fixed_refresh is not None:
            return
        now = dt_util.now()
        candidates = [
            datetime.combine(now.date(), refresh_time, tzinfo=dt_util.DEFAULT_TIME_ZONE)
            for refresh_time in FIXED_REFRESH_TIMES
        ]
        future = [candidate for candidate in candidates if candidate > now]
        refresh_at = min(future) if future else candidates[0] + timedelta(days=1)
        self._cancel_fixed_refresh = async_track_point_in_time(
            self.hass, self._async_fixed_refresh, refresh_at
        )

    async def _async_handle_transition(self, _now) -> None:
        self._cancel_transition = None
        now = dt_util.now()
        if self.data:
            self.async_set_updated_data(
                self._build_data(
                    now,
                    self.data["last_update"],
                    self.data.get("tomorrow_published", False),
                )
            )
        self._schedule_next_transition()
        self._schedule_post_change_refresh(now)

    async def _async_post_change_refresh(self, _now) -> None:
        self._cancel_post_change_refresh = None
        await self.async_request_refresh()

    async def _async_hourly_refresh(self, _now) -> None:
        await self.async_request_refresh()

    async def _async_fixed_refresh(self, _now) -> None:
        self._cancel_fixed_refresh = None
        try:
            await self.async_request_refresh()
        finally:
            self._schedule_fixed_refresh()

    async def _async_update_data(self):
        now_local = dt_util.now()
        today = now_local.date()
        days = [today - timedelta(days=1), today, today + timedelta(days=1)]
        reference_utc = datetime.now(timezone.utc)
        fetched = await self._async_fetch_days(days, reference_utc)

        # The AMB endpoint returns a single 00:00 low point for tomorrow before
        # the next-day schedule is published. A real daily schedule contains
        # many 15-minute points, even if the tariff never changes.
        tomorrow_published = len(fetched[today + timedelta(days=1)]) > 1

        all_points = []
        for day, points in fetched.items():
            if day == today + timedelta(days=1) and not tomorrow_published:
                continue
            all_points.extend(points)

        merged = {point["start"]: point["tariff"] for point in all_points}
        self._points = [
            {"start": key, "tariff": value} for key, value in sorted(merged.items())
        ]

        data = self._build_data(now_local, dt_util.utcnow(), tomorrow_published)

        self.hass.loop.call_soon(self._schedule_next_transition)
        self.hass.loop.call_soon(self._schedule_hourly_refresh)
        self.hass.loop.call_soon(self._schedule_fixed_refresh)
        return data

    async def _async_fetch_days(self, days, reference_utc):
        result = {}
        for day in days:
            result[day] = await self._fetch_day(day, reference_utc)
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
