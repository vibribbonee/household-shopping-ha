"""Setup, the auto-served card, and Reconfigure inside Home Assistant."""
from __future__ import annotations

from homeassistant import config_entries
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
)

from custom_components.household_shopping import CARD_URL, DOMAIN

DATA = {"printer_entry": "epson-entry", "shopping_list": "todo.shopping", "satellite": "assist_satellite.kitchen"}


async def _setup(hass):
    mock_integration(hass, MockModule("epson_receipt_printer"))
    assert await async_setup_component(hass, "todo", {})
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data=dict(DATA), title="Household Shopping")
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_setup_serves_the_card_without_a_manual_resource(hass, hass_client):
    await _setup(hass)
    assert hass.states.get("sensor.household_shopping") is not None
    urls = hass.data[DATA_EXTRA_MODULE_URL].urls
    assert any(url.startswith(CARD_URL) for url in urls)
    client = await hass_client()
    response = await client.get(CARD_URL)
    assert response.status == 200
    assert "household-shopping-card" in await response.text()


async def test_reconfigure_replaces_settings_and_can_clear_optional_ones(hass):
    entry = await _setup(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"printer_entry": "epson-entry", "shopping_list": "todo.groceries"}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert dict(entry.data) == {"printer_entry": "epson-entry", "shopping_list": "todo.groceries"}
