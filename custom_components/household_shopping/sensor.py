"""A registered push sensor exposes current trips to the shopping card."""
from copy import deepcopy
from homeassistant.components.sensor import SensorEntity
from . import DOMAIN

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ShoppingStatus(hass.data[DOMAIN], entry)])

class ShoppingStatus(SensorEntity):
    _attr_name = "Household Shopping"
    _attr_icon = "mdi:cart-outline"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"trips", "stores", "store_cards"})

    def __init__(self, manager, entry):
        self.manager = manager
        self._attr_unique_id = entry.entry_id + "_status"

    async def async_added_to_hass(self):
        self.manager.listeners.append(self.async_write_ha_state)
        self.async_on_remove(lambda: self.manager.listeners.remove(self.async_write_ha_state))

    @property
    def native_value(self):
        return self.manager.data["message"][:250]

    @property
    def extra_state_attributes(self):
        trips = self.manager.data["trips"]
        # Keep all durable history; publish active/unpaid trips plus the 10 most recent.
        visible = [t for t in trips if t["status"] not in ("finished", "cancelled") or any(not p["paid"] for p in t["payback"].values()) or t in trips[-10:]]
        stores = self.manager.active_stores()
        return {"stores": stores, "store_cards": {name: self.manager.card_value(name) for name in stores}, "trips": deepcopy(visible), "label_available": bool(self.manager.entry.data.get("label_entry"))}
