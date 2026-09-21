"""Binary sensor platform for AMB Dynamic Tariff."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import AMB_TARIFF_URL, DOMAIN, NAME, TARIFF_LOW
from .coordinator import AmbTariffCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: AmbTariffCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AmbLowTariffBinarySensor(coordinator, entry)])


class AmbLowTariffBinarySensor(CoordinatorEntity[AmbTariffCoordinator], BinarySensorEntity):
    """True while the AMB dynamic tariff is low."""
    _attr_has_entity_name = True
    _attr_translation_key = "low_tariff"
    _attr_icon = "mdi:currency-usd-off"

    def __init__(self, coordinator: AmbTariffCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_low_tariff"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=NAME,
            manufacturer="Azienda Multiservizi Bellinzona (AMB)",
            model="Dynamic Tariff", configuration_url=AMB_TARIFF_URL,
        )

    @property
    def is_on(self) -> bool:
        return self.coordinator.data["current_tariff"] == TARIFF_LOW
