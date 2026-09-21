"""Data coordinator for AMB Dynamic Tariff."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
from typing import Any
from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from .const import API_URL,COLOR_HIGH,COLOR_LOW,NAME,TARIFF_HIGH,TARIFF_LOW,TARIFF_UNKNOWN,UPDATE_INTERVAL
_LOGGER=logging.getLogger(__name__)

def _tariff_from_color(color):
    if not color:return TARIFF_UNKNOWN
    color=color.upper()
    return TARIFF_LOW if color==COLOR_LOW else TARIFF_HIGH if color==COLOR_HIGH else TARIFF_UNKNOWN

def _parse_dt(value):
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    return parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)

def _dedupe_points(labels,colors):
    p={}
    for label,color in zip(labels,colors,strict=False):
        try:p[_parse_dt(label)]=_tariff_from_color(color)
        except (TypeError,ValueError):continue
    return [{"start":k,"tariff":v} for k,v in sorted(p.items())]

def _compress_periods(points):
    if not points:return []
    periods=[]; start=points[0]["start"]; tariff=points[0]["tariff"]; previous=start
    for point in points[1:]:
        when=point["start"]; new=point["tariff"]
        if new!=tariff:
            periods.append({"start":start,"end":when,"tariff":tariff}); start=when; tariff=new
        previous=when
    end=previous
    if len(points)==1 or end<=start:end=start+timedelta(minutes=15)
    periods.append({"start":start,"end":end,"tariff":tariff})
    return periods

class AmbTariffCoordinator(DataUpdateCoordinator[dict[str,Any]]):
    def __init__(self,hass):
        super().__init__(hass,_LOGGER,name=NAME,update_interval=UPDATE_INTERVAL)
        self._session=async_get_clientsession(hass)
    async def _fetch_day(self,day):
        request_dt=datetime(day.year,day.month,day.day,12,tzinfo=timezone.utc)
        payload={"date":request_dt.isoformat(timespec="milliseconds").replace("+00:00","Z")}
        try:
            async with self._session.post(API_URL,json=payload,headers={"Accept":"application/json","Content-Type":"application/json","User-Agent":"HomeAssistant-AMB-Dynamic-Tariff/0.1.2"},timeout=15) as response:
                response.raise_for_status(); data=await response.json(content_type=None)
        except (ClientError,TimeoutError,ValueError) as err: raise UpdateFailed(f"Error communicating with AMB: {err}") from err
        labels=data.get("labels"); colors=data.get("bgColors")
        if not isinstance(labels,list) or not isinstance(colors,list):raise UpdateFailed("Unexpected response from AMB: labels/bgColors missing")
        return _dedupe_points(labels,colors)
    async def _async_update_data(self):
        today=dt_util.now().date(); days=[today-timedelta(days=1),today,today+timedelta(days=1)]
        fetched=await self._async_fetch_days(days); all_points=[]
        for pts in fetched.values():all_points.extend(pts)
        merged={p["start"]:p["tariff"] for p in all_points}
        points=[{"start":k,"tariff":v} for k,v in sorted(merged.items())]
        now=dt_util.now(); current=TARIFF_UNKNOWN; current_start=None; next_change=None; next_tariff=TARIFF_UNKNOWN
        for idx,point in enumerate(points):
            if point["start"]<=now:
                current=point["tariff"]; current_start=point["start"]
                for later in points[idx+1:]:
                    if later["tariff"]!=current: next_change=later["start"]; next_tariff=later["tariff"]; break
        return {"current_tariff":current,"current_since":current_start,"next_tariff":next_tariff,"next_change":next_change,"today_periods":self._periods_for_local_date(points,today),"tomorrow_periods":self._periods_for_local_date(points,today+timedelta(days=1)),"last_update":dt_util.utcnow()}
    async def _async_fetch_days(self,days):
        result={}
        for day in days:result[day]=await self._fetch_day(day)
        return result
    def _periods_for_local_date(self,points,local_date):
        tz=dt_util.DEFAULT_TIME_ZONE; start=datetime.combine(local_date,datetime.min.time(),tzinfo=tz); end=start+timedelta(days=1)
        relevant=[]; tariff=TARIFF_UNKNOWN
        for point in points:
            if point["start"]<=start:tariff=point["tariff"]
            elif point["start"]<end:relevant.append(point)
        periods=_compress_periods([{"start":start,"tariff":tariff}]+relevant)
        if periods:periods[-1]["end"]=end
        return periods

def format_periods(periods):
    return [{"start":dt_util.as_local(p["start"]).isoformat(),"end":dt_util.as_local(p["end"]).isoformat(),"start_time":dt_util.as_local(p["start"]).strftime("%H:%M"),"end_time":dt_util.as_local(p["end"]).strftime("%H:%M"),"tariff":p["tariff"]} for p in periods]
