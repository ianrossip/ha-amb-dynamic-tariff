"""AMB Dynamic Tariff integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import AmbTariffCoordinator

PLATFORMS = ["sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AMB Dynamic Tariff from a config entry."""
    coordinator = AmbTariffCoordinator(hass)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: AmbTariffCoordinator = hass.data[DOMAIN][entry.entry_id]
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await coordinator.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
