"""Choose the household's existing printers and communication endpoints."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

DOMAIN = "household_shopping"

class HouseholdShoppingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Household Shopping", data=user_input)
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("printer_entry"): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(integration="epson_receipt_printer")),
            vol.Optional("label_entry"): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(integration="brother_label_printer")),
            vol.Required("shopping_list", default="todo.shopping_list"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="todo")),
            vol.Optional("telegram_entity"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="notify", integration="telegram_bot")),
            vol.Optional("satellite"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="assist_satellite")),
        }))
