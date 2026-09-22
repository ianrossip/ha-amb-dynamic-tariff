"""Sensor platform for AMB Dynamic Tariff."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from .const import AMB_TARIFF_URL, DOMAIN, NAME
from .coordinator import AmbTariffCoordinator, format_periods

@dataclass(frozen=True, kw_only=True)
class AmbSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict[str, Any]], Any]
    attrs_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    available_fn: Callable[[dict[str, Any]], bool] | None = None

SENSORS = (
    AmbSensorDescription(key="current_tariff", translation_key="current_tariff", icon="mdi:transmission-tower", value_fn=lambda d:d["current_tariff"], attrs_fn=lambda d:{"current_since":dt_util.as_local(d["current_since"]).isoformat() if d["current_since"] else None,"next_tariff":d["next_tariff"],"next_change":dt_util.as_local(d["next_change"]).isoformat() if d["next_change"] else None}),
    AmbSensorDescription(key="next_tariff", translation_key="next_tariff", icon="mdi:clock-fast", value_fn=lambda d:d["next_tariff"]),
    AmbSensorDescription(key="next_change", translation_key="next_change", icon="mdi:clock-outline", device_class="timestamp", value_fn=lambda d:d["next_change"]),
    AmbSensorDescription(key="yesterday_schedule", translation_key="yesterday_schedule", icon="mdi:calendar-arrow-left", value_fn=lambda d:len(d["yesterday_periods"]), attrs_fn=lambda d:{"periods":format_periods(d["yesterday_periods"])}),
    AmbSensorDescription(key="today_schedule", translation_key="today_schedule", icon="mdi:calendar-today", value_fn=lambda d:len(d["today_periods"]), attrs_fn=lambda d:{"periods":format_periods(d["today_periods"])}),
    AmbSensorDescription(key="tomorrow_schedule", translation_key="tomorrow_schedule", icon="mdi:calendar-arrow-right", value_fn=lambda d:len(d["tomorrow_periods"]) if d["tomorrow_periods"] is not None else None, attrs_fn=lambda d:{"periods":format_periods(d["tomorrow_periods"])} if d["tomorrow_periods"] is not None else {}, available_fn=lambda d:d["tomorrow_published"]),
    AmbSensorDescription(key="last_update", translation_key="last_update", icon="mdi:update", device_class="timestamp", entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda d:d["last_update"]),
)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: AmbTariffCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(AmbTariffSensor(coordinator, entry, desc) for desc in SENSORS)

class AmbTariffSensor(CoordinatorEntity[AmbTariffCoordinator], SensorEntity):
    _attr_has_entity_name = True
    def __init__(self, coordinator, entry, description):
        super().__init__(coordinator)
        self.entity_description=description
        self._attr_unique_id=f"{entry.entry_id}_{description.key}"
        self._attr_device_info=DeviceInfo(identifiers={(DOMAIN,entry.entry_id)},name=NAME,manufacturer="Azienda Multiservizi Bellinzona (AMB)",model="Dynamic Tariff",configuration_url=AMB_TARIFF_URL)
    @property
    def available(self):
        return super().available and (
            self.entity_description.available_fn is None
            or self.entity_description.available_fn(self.coordinator.data)
        )
    @property
    def native_value(self): return self.entity_description.value_fn(self.coordinator.data)
    @property
    def extra_state_attributes(self):
        return None if self.entity_description.attrs_fn is None else self.entity_description.attrs_fn(self.coordinator.data)
