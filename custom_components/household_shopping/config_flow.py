"""Choose the household's existing printers and communication endpoints."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

DOMAIN = "household_shopping"
OPTIONAL_KEYS = ("label_entry", "telegram_entity", "satellite")


def _schema(current: dict | None = None) -> vol.Schema:
    """One form for setup and Reconfigure; optional fields can be cleared."""
    current = current or {}

    def suggested(key):
        return {"suggested_value": current.get(key)} if current.get(key) else {}

    return vol.Schema({
        vol.Required("printer_entry", default=current.get("printer_entry", vol.UNDEFINED)): selector.ConfigEntrySelector(
            selector.ConfigEntrySelectorConfig(integration="epson_receipt_printer")),
        vol.Optional("label_entry", description=suggested("label_entry")): selector.ConfigEntrySelector(
            selector.ConfigEntrySelectorConfig(integration="brother_label_printer")),
        vol.Required("shopping_list", default=current.get("shopping_list", "todo.shopping_list")): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="todo")),
        vol.Optional("telegram_entity", description=suggested("telegram_entity")): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="notify", integration="telegram_bot")),
        vol.Optional("satellite", description=suggested("satellite")): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="assist_satellite")),
    })


class HouseholdShoppingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Household Shopping", data=user_input)
        return self.async_show_form(step_id="user", data_schema=_schema())

    async def async_step_reconfigure(self, user_input=None):
        """Change printers, the list, or notifications without losing trip history."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            # Replace rather than merge, so a cleared optional field is removed.
            return self.async_update_reload_and_abort(entry, data=user_input)
        return self.async_show_form(step_id="reconfigure", data_schema=_schema(dict(entry.data)))
