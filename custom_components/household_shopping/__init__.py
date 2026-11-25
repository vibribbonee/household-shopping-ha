"""Persistent household trips; native HA automations supply NFC/Telegram inputs."""
from __future__ import annotations

import asyncio
from textwrap import fill
from datetime import timedelta
import logging
import hashlib
import secrets
import uuid

import voluptuous as vol
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import Context, HomeAssistant, ServiceCall, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .model import CARDS, STORES, TAGS, cents, loyalty_block, parse_item, receipt_lines, store_name

DOMAIN = "household_shopping"
CARD_URL = "/household_shopping/household-shopping-card.js"
CARD_VERSION = "1.1.0"
LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
SCHEMA = vol.Schema({
    vol.Required("operation"): vol.In(("start", "print_now", "reprint", "rollback", "finish", "payback", "add_item", "food_label", "telegram", "scan", "pair_telegram", "save_store", "remove_store")),
    vol.Optional("store", default=""): cv.string,
    vol.Optional("loyalty", default=""): cv.string,
    vol.Optional("guest_shopper", default=""): cv.string,
    vol.Optional("trip_id", default=""): cv.string,
    vol.Optional("item", default=""): cv.string,
    vol.Optional("requester", default=""): cv.string,
    vol.Optional("quantity", default=1): vol.All(cv.positive_int, vol.Range(min=1, max=100)),
    vol.Optional("amount", default=""): cv.string,
    vol.Optional("paid", default=True): cv.boolean,
    vol.Optional("date", default=""): cv.string,
    vol.Optional("user_id", default=""): cv.string,
    vol.Optional("tag_id", default=""): cv.string,
    vol.Optional("event_data", default=dict): dict,
})

async def async_setup(hass: HomeAssistant, config):
    """Serve the dashboard card so it works without a manual resource."""
    card = Path(__file__).parent / "frontend" / "household-shopping-card.js"
    await hass.http.async_register_static_paths([StaticPathConfig(CARD_URL, str(card), True)])
    add_extra_js_url(hass, f"{CARD_URL}?v={CARD_VERSION}")
    return True

async def async_setup_entry(hass: HomeAssistant, entry):
    manager = Trips(hass, entry)
    await manager.load()
    hass.data[DOMAIN] = manager
    async def request(call: ServiceCall):
        return await manager.request(dict(call.data), call.context) or {}
    hass.services.async_register(DOMAIN, "request", request, schema=SCHEMA, supports_response=SupportsResponse.OPTIONAL)
    await hass.config_entries.async_forward_entry_setups(entry, [Platform.SENSOR])
    if hass.is_running:
        manager.schedule()
    else:
        entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, lambda _: manager.schedule()))
    return True

async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, [Platform.SENSOR]):
        manager = hass.data.pop(DOMAIN)
        manager.close()
        hass.services.async_remove(DOMAIN, "request")
        return True
    return False

class Trips:
    # Closed trips stay visible for repayments and history, then are pruned so
    # storage and the card's state attributes do not grow forever.
    HISTORY_DAYS = 90
    MAX_TRIPS = 300

    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.storage = Store(hass, 1, DOMAIN + ".trips")
        self.data = {"trips": [], "message": "Ready", "telegram_links": {}, "pairing": {}, "store_settings": {}}
        self.lock = asyncio.Lock()
        self.timers = {}
        self.listeners = []

    async def load(self):
        self.data = await self.storage.async_load() or self.data
        self.data.setdefault("telegram_links", {})
        self.data.setdefault("pairing", {})
        self.data.setdefault("store_settings", {})
        for trip in self.data["trips"]:
            if trip["status"] == "printing":
                trip["status"] = "attention"
                trip["error"] = "HA restarted during printing. Check the paper before choosing Reprint or Undo."
                for job in trip["jobs"]:
                    if job["status"] == "sending":
                        job["status"] = "uncertain"
        self.prune()
        await self.storage.async_save(self.data)

    def prune(self):
        """Drop old closed trips; keep any trip with an unpaid repayment."""
        cutoff = dt_util.utcnow() - timedelta(days=self.HISTORY_DAYS)

        def removable(trip):
            closed = trip["status"] in ("finished", "cancelled")
            unpaid = any(not row.get("paid") for row in trip.get("payback", {}).values())
            return closed and not unpaid

        trips = [
            t for t in self.data["trips"]
            if not (removable(t) and dt_util.parse_datetime(t["created"]) < cutoff)
        ]
        excess = len(trips) - self.MAX_TRIPS
        if excess > 0:
            oldest_closed = [t["id"] for t in trips if removable(t)][:excess]
            trips = [t for t in trips if t["id"] not in oldest_closed]
        removed = len(self.data["trips"]) - len(trips)
        self.data["trips"] = trips
        return removed

    def close(self):
        for cancel in self.timers.values():
            cancel()
        self.timers.clear()

    @callback
    def schedule(self):
        self.close()
        for trip in self.data["trips"]:
            if trip["status"] == "collecting":
                when = max(dt_util.utcnow() + timedelta(seconds=1), dt_util.parse_datetime(trip["deadline"]))
                async def due(_, trip_id=trip["id"]):
                    async with self.lock:
                        t = self.find(trip_id)
                        if t["status"] == "collecting":
                            try:
                                await self.finalize(t, Context())
                            except Exception as err:
                                t["status"] = "attention"
                                t["error"] = str(err)
                                await self.save("Could not finalize the list; items preserved. Undo this trip and start again.")
                                await self.message(self.data["message"] + " " + str(err), t)
                self.timers[trip["id"]] = async_track_point_in_utc_time(self.hass, due, when)

    async def save(self, message=None):
        if message:
            self.data["message"] = message
        await self.storage.async_save(self.data)
        for listener in tuple(self.listeners):
            listener()

    def find(self, trip_id):
        for trip in self.data["trips"]:
            if trip["id"] == trip_id:
                return trip
        raise ServiceValidationError("That shopping trip does not exist.")

    def person(self, user_id="", telegram_id=""):
        if telegram_id:
            user_id = self.data["telegram_links"].get(str(telegram_id), user_id)
        for state in self.hass.states.async_all("person"):
            attrs = state.attributes
            if (user_id and attrs.get("user_id") == user_id) or (telegram_id and str(attrs.get("telegram_user_id", "")) == str(telegram_id)):
                return state.name
        raise ServiceValidationError("Your HA/Telegram user is not linked to a household person.")

    def select(self, trip_id, actor, include_finished=False):
        if trip_id:
            return self.find(trip_id)
        excluded = ("cancelled",) if include_finished else ("cancelled", "finished")
        matches = [t for t in self.data["trips"] if t.get("owner", t["shopper"]) == actor and t["status"] not in excluded]
        if not matches:
            raise ServiceValidationError("No current shopping trip for you.")
        return matches[-1]

    async def service(self, domain, action, data, context=None, response=False):
        return await self.hass.services.async_call(domain, action, data, blocking=True, context=context, return_response=response)

    async def items(self, context):
        entity = self.entry.data["shopping_list"]
        result = await self.service("todo", "get_items", {"entity_id": entity, "status": ["needs_action", "completed"]}, context, True)
        return result[entity]["items"]

    async def message(self, text, trip=None, announce=False):
        """Delivery failures remain visible and do not corrupt shopping state."""
        endpoint = self.entry.data.get("telegram_entity")
        if endpoint:
            data = {"entity_id": endpoint, "message": text, "parse_mode": "plain_text"}
            if trip:
                tid = trip["id"]
                if trip["status"] == "collecting":
                    data["inline_keyboard"] = [[["Print now", f"hs:print_now:{tid}"], ["Cancel trip", f"hs:rollback:{tid}"]]]
                elif trip["status"] in ("sent", "attention"):
                    data["inline_keyboard"] = [[["Reprint", f"hs:reprint:{tid}"], ["Undo trip", f"hs:rollback:{tid}"]], [["Shopping done", f"hs:finish:{tid}"]]]
            try:
                await self.service("telegram_bot", "send_message", data)
            except Exception:
                LOGGER.exception("Could not deliver shopping Telegram message")
                self.data["message"] = "Telegram delivery failed; check the shopping dashboard."
                await self.save()
        satellite = self.entry.data.get("satellite")
        if announce and satellite:
            state = self.hass.states.get(satellite)
            if state and state.state == "idle":
                try:
                    await self.service("assist_satellite", "announce", {"entity_id": satellite, "message": text, "preannounce": True})
                except Exception:
                    LOGGER.exception("Could not announce shopping trip")

    async def request(self, data, context):
        async with self.lock:
            try:
                op = data["operation"]
                if op == "telegram":
                    await self.telegram(data["event_data"], context)
                    return
                if context.user_id and data.get("user_id") and data["user_id"] != context.user_id:
                    raise ValueError("A logged-in user cannot act as another household person.")
                actor = self.person(data.get("user_id") or context.user_id)
                if op == "pair_telegram":
                    # Only a logged-in HA user can issue a pairing code for themselves.
                    if not context.user_id:
                        raise ValueError("Connect Telegram from your own logged-in HA account.")
                    code = secrets.token_hex(6)
                    self.data["pairing"][context.user_id] = {"digest": hashlib.sha256(code.encode()).hexdigest(), "expires": (dt_util.utcnow() + timedelta(minutes=10)).isoformat()}
                    await self.save()
                    return {"code": code, "person": self.person(context.user_id)}
                if op == "scan":
                    if data["tag_id"] == "reprint-tag":
                        op = "reprint"
                    elif data["tag_id"] in ("rollback-tag", "undo-tag"):
                        op = "rollback"
                    else:
                        data["store"] = TAGS.get(data["tag_id"], "")
                        op = "start"
                await self.perform(op, data, actor, context)
            except ValueError as err:
                raise ServiceValidationError(str(err)) from err

    def active_stores(self):
        settings = self.data["store_settings"]
        return [name for name in dict.fromkeys((*STORES, *settings)) if settings.get(name, {}).get("active", True)]

    def resolve_store(self, value, active_only=False):
        name = store_name(value, self.data["store_settings"])
        if active_only and name not in self.active_stores():
            raise ValueError("That store has been removed. Add it again to use it.")
        return name

    async def update_store(self, op, data):
        name = data["store"].strip()
        if not name or len(name) > 40 or any(ch in "[]" or not ch.isprintable() for ch in name):
            raise ValueError("Enter a store name up to 40 characters, without square brackets.")
        try:
            name = self.resolve_store(name)
        except ValueError:
            if op == "remove_store":
                raise
        settings = self.data["store_settings"]
        if op == "remove_store":
            if any(name in trip["stores"] and trip["status"] == "collecting" for trip in self.data["trips"]):
                raise ValueError("Finish or undo the collecting trip before removing this store.")
            settings[name] = {"active": False, "loyalty": ""}
            await self.save(f"Removed {name} and its loyalty card")
        else:
            loyalty = data.get("loyalty", "").strip()
            if len(loyalty) > 512 or any(not ch.isprintable() for ch in loyalty):
                raise ValueError("Enter a loyalty value on one line, up to 512 characters.")
            settings[name] = {"active": True, "loyalty": loyalty}
            await self.save(f"Saved {name}")

    async def perform(self, op, data, actor, context):
        if op in ("save_store", "remove_store"):
            await self.update_store(op, data)
            return
        if op == "start":
            guest = data.get("guest_shopper", "").strip()
            if len(guest) > 40 or any(not ch.isprintable() for ch in guest):
                raise ValueError("Enter a guest shopper name up to 40 characters.")
            await self.start(self.resolve_store(data.get("store", ""), active_only=True), actor, guest)
            return
        if op == "add_item":
            item = str(data.get("item", "")).strip()
            if not item or len(item) > 180 or any(c in item for c in "\r\n"):
                raise ValueError("Enter an item name on one line, up to 180 characters.")
            requester = (data.get("requester") or "").strip() or actor
            known = {s.name for s in self.hass.states.async_all("person")} | {"Household"}
            if requester not in known and (len(requester) > 40 or any(ch in "()" or not ch.isprintable() for ch in requester)):
                raise ValueError("Enter a guest name up to 40 characters, without parentheses.")
            store = self.resolve_store(data.get("store", ""), active_only=True)
            await self.service("todo", "add_item", {"entity_id": self.entry.data["shopping_list"], "item": f"[{store}] {data.get('quantity', 1)}x {item} ({requester})"}, context)
            await self.save(f"Added {item} for {requester}")
            return
        if op == "food_label":
            await self.food_label(data, actor, context)
            return
        trip = self.select(data.get("trip_id", ""), actor, include_finished=op == "reprint")
        if op == "payback":
            name = data.get("requester") or actor
            if actor not in (name, trip.get("owner", trip["shopper"])):
                raise ValueError("Only the shopper or the named person can change this repayment.")
            row = trip["payback"].get(name)
            if row is None:
                raise ValueError("That person has no repayment on this trip.")
            if trip["status"] != "finished":
                raise ValueError("Mark shopping done before recording repayments.")
            if data.get("amount", "") != "":
                row["amount_cents"] = cents(data["amount"])
            row["paid"] = data.get("paid", True)
            await self.save(f"Updated repayment for {name}")
            return
        if actor != trip.get("owner", trip["shopper"]):
            raise ValueError("Only the person making this trip can print, finish, or undo it.")
        if op == "print_now":
            await self.finalize(trip, context)
        elif op == "reprint":
            await self.reprint(trip, data.get("store", ""), context)
        elif op == "rollback":
            await self.rollback(trip, data.get("store", ""), context)
        elif op == "finish":
            if trip["status"] != "sent":
                raise ValueError("Send all receipts and resolve any print errors first.")
            trip["status"] = "finished"
            self.prune()
            await self.save(f"{trip['shopper']} finished shopping")
            await self.message(f"{trip['shopper']} finished shopping.")

    async def start(self, store, actor, guest=""):
        trip = next((t for t in reversed(self.data["trips"]) if t.get("owner", t["shopper"]) == actor and t["status"] == "collecting"), None)
        if trip and guest and guest != trip["shopper"]:
            raise ValueError("This trip already has a shopper. Finish or undo it before starting another.")
        if trip and store in trip["stores"]:
            return
        if trip:
            trip["stores"].append(store)
        else:
            now = dt_util.utcnow()
            trip = {"id": uuid.uuid4().hex[:12], "shopper": guest or actor, "owner": actor, "stores": [store], "status": "collecting", "created": now.isoformat(), "deadline": (now + timedelta(minutes=5)).isoformat(), "jobs": [], "payback": {}}
            self.data["trips"].append(trip)
        deadline = dt_util.as_local(dt_util.parse_datetime(trip["deadline"])).strftime("%H:%M")
        text = f"{trip['shopper']} is going to {', '.join(trip['stores'])}. Add what you need before {deadline}; the collection window is five minutes."
        await self.save(text)
        self.schedule()
        await self.message(text, trip, announce=True)

    def card_value(self, store):
        override = self.data["store_settings"].get(store)
        if override is not None:
            return override["loyalty"]
        entity = CARDS.get(store)
        state = self.hass.states.get(entity) if entity else None
        return state.state if state else ""

    def blocks(self, trip, job):
        blocks = [{"type": "text", "text": job["store"], "alignment": "center", "bold": True}]
        card = loyalty_block(job["loyalty"])
        if card:
            blocks.extend([{"type": "text", "text": "Loyalty card", "alignment": "center"}, card, {"type": "spacer", "height_mm": 3}])
        blocks.append({"type": "text", "text": "SHOPPING LIST\n==============================\n" + (receipt_lines(job["items"]) or "No items") + "\n==============================", "bold": True})
        blocks.append({"type": "text", "text": f"Thanks for shopping,\n{trip['shopper']}! <3", "alignment": "center", "bold": True})
        names = sorted({i["requester"] for i in job["items"] if i["requester"] not in (trip["shopper"], "Household")})
        if names:
            blocks.append({"type": "text", "text": "Paid back checklist:\n" + "\n".join("[ ] " + n for n in names), "font": "B"})
        blocks.append({"type": "text", "text": f"Finalized at {trip['finalized']}\nTrip {trip['id']}", "font": "B", "alignment": "center"})
        for block in blocks:
            if block["type"] == "text":
                block["text"] = "\n".join(fill(line, width=32) for line in block["text"].split("\n"))
        return blocks

    async def send_job(self, trip, job, context):
        await self.service("epson_receipt_printer", "print_text", {"config_entry_id": self.entry.data["printer_entry"], "blocks": self.blocks(trip, job), "cut_paper": True}, context)

    async def finalize(self, trip, context):
        if trip["status"] != "collecting":
            raise ValueError("This trip has already been finalized. Use Reprint if needed.")
        all_items = await self.items(context)
        claimed = {i["uid"] for t in self.data["trips"] if t["id"] != trip["id"] and t["status"] not in ("finished", "cancelled") for j in t["jobs"] if j["status"] != "cancelled" for i in j["items"]}
        parsed = [p for i in all_items if i.get("status") == "needs_action" and (p := parse_item(i, self.data["store_settings"])) and p["uid"] not in claimed]
        trip["finalized"] = dt_util.now().strftime("%d-%m-%Y %H:%M")
        trip["jobs"] = [{"store": s, "status": "ready", "items": [i for i in parsed if i["store"] == s], "loyalty": self.card_value(s), "completed_uids": []} for s in trip["stores"]]
        trip["status"] = "printing"
        await self.save("Sending shopping receipts")
        self.schedule()
        try:
            for job in trip["jobs"]:
                # Persist the uncertain window BEFORE sending: a restart must never auto-reprint it.
                if not job["items"] and loyalty_block(job["loyalty"]) is None:
                    job["status"] = "empty"
                    continue
                job["status"] = "sending"
                await self.save()
                await self.send_job(trip, job, context)
                job["status"] = "sent"
                await self.save()
                await self.complete_items(job, context)
            trip["status"] = "sent"
            self.payback_rows(trip)
            await self.save("Receipts sent; check the printer")
            await self.message(f"Receipts sent for {', '.join(trip['stores'])} by {trip['shopper']}. Check the printer. Reprint uses the saved lists; Undo restores the whole trip.", trip, announce=True)
        except Exception as err:
            trip["status"] = "attention"
            trip["error"] = str(err)
            for job in trip["jobs"]:
                if job["status"] == "sending":
                    job["status"] = "uncertain"
            self.payback_rows(trip)
            await self.save("Printing needs attention; lists and snapshots preserved")
            await self.message(f"Printing needs attention for {trip['shopper']}'s trip: {err}. Check the paper before Reprint, or use Undo to restore the trip.", trip)

    def payback_rows(self, trip):
        previous = trip["payback"]
        names = {i["requester"] for j in trip["jobs"] if j["status"] != "cancelled" for i in j["items"] if i["requester"] not in (trip["shopper"], "Household")}
        trip["payback"] = {n: previous.get(n, {"paid": False, "amount_cents": None}) for n in sorted(names)}

    async def complete_items(self, job, context):
        """Never complete an item someone edited after the receipt snapshot."""
        current = {i["uid"]: i for i in await self.items(context)}
        for item in job["items"]:
            now = current.get(item["uid"])
            if now and now["summary"] == item["summary"] and now["status"] == "needs_action":
                # Journal first, so rollback can recover even if HA stops during update_item.
                if item["uid"] not in job["completed_uids"]:
                    job["completed_uids"].append(item["uid"])
                    await self.save()
                await self.service("todo", "update_item", {"entity_id": self.entry.data["shopping_list"], "item": item["uid"], "status": "completed"}, context)

    async def reprint(self, trip, store, context):
        if trip["status"] not in ("sent", "attention", "finished"):
            raise ValueError("This trip has no saved receipts to reprint.")
        selected = self.resolve_store(store) if store else None
        jobs = [j for j in trip["jobs"] if j["status"] not in ("cancelled", "empty") and (selected is None or j["store"] == selected)]
        if not jobs:
            raise ValueError("No saved receipt for that shop.")
        for job in jobs:
            await self.send_job(trip, job, context)
            # A retry of a failed original must complete its items; ordinary reprints never do.
            if trip["status"] == "attention" and job["status"] in ("ready", "uncertain", "sent"):
                job["status"] = "sent"
                await self.save()
                await self.complete_items(job, context)
        if trip["status"] == "attention" and all(j["status"] in ("sent", "empty", "cancelled") for j in trip["jobs"]):
            trip["status"] = "sent"
            trip.pop("error", None)
        self.payback_rows(trip)
        await self.save("Saved receipts sent again")

    async def rollback(self, trip, store, context):
        if trip["status"] == "finished" or any(p["paid"] for p in trip["payback"].values()):
            raise ValueError("Purchases/repayments are recorded. Keep that history; add missing items as a new request.")
        if trip["status"] == "cancelled":
            return
        selected = self.resolve_store(store) if store else None
        if selected and selected not in trip["stores"]:
            raise ValueError("That shop is not on this trip.")
        jobs = [j for j in trip["jobs"] if selected is None or j["store"] == selected]
        for job in jobs:
            current = {i["uid"]: i for i in await self.items(context)}
            for item in job["items"]:
                if item["uid"] not in job["completed_uids"]:
                    continue
                now = current.get(item["uid"])
                if now and now["summary"] == item["summary"] and now["status"] == "completed":
                    await self.service("todo", "update_item", {"entity_id": self.entry.data["shopping_list"], "item": item["uid"], "status": "needs_action"}, context)
                elif not now:
                    # Missing/edited items need an explicit decision; don't resurrect deliberate deletions.
                    raise ValueError("A printed item was deleted. Restore it manually before undoing this trip: " + item["summary"])
                elif now["summary"] != item["summary"]:
                    raise ValueError("A printed item was edited. Its changes were preserved: " + now["summary"])
            job["status"] = "cancelled"
            await self.save()
        trip["stores"] = [s for s in trip["stores"] if selected and s != selected]
        if not trip["stores"]:
            trip["status"] = "cancelled"
        self.payback_rows(trip)
        self.schedule()
        await self.save("Trip undone; its items are available again")
        await self.message(f"{trip['shopper']}'s {'trip' if not selected else selected + ' visit'} was cancelled. Its shopping items are available again.")

    async def food_label(self, data, actor, context):
        entry = self.entry.data.get("label_entry")
        if not entry:
            raise ValueError("Choose a Brother QL printer in the integration setup first.")
        date = data.get("date") or dt_util.now().date().isoformat()
        opened = dt_util.parse_date(date)
        if opened is None:
            raise ValueError("Use a valid date in YYYY-MM-DD format.")
        await self.service("brother_label_printer", "print_label", {
            "config_entry_id": entry, "text": opened.strftime("%d-%m-%Y"),
            "alignment": "center", "text_size": "large", "bold": True, "copies": 1,
        }, context)
        await self.save("Opening-date label sent")

    async def telegram(self, event, context):
        text = str(event.get("text", event.get("data", ""))).strip()
        if not (text.startswith("!") or text.startswith("hs:")):
            return
        if text.lower().startswith("!link "):
            digest = hashlib.sha256(text.split(" ", 1)[1].strip().encode()).hexdigest()
            sender = str(event.get("user_id", ""))
            if not sender:
                return
            for uid, record in list(self.data["pairing"].items()):
                if secrets.compare_digest(record["digest"], digest) and dt_util.parse_datetime(record["expires"]) > dt_util.utcnow():
                    actor = self.person(user_id=uid)
                    self.data["telegram_links"][sender] = uid
                    del self.data["pairing"][uid]
                    await self.save()
                    await self.message(f"Telegram connected for {actor}. You can use !shopping ah, !shopping action, !printnow, !reprint, !cancel and !done.")
                    return
            return
        try:
            actor = self.person(telegram_id=str(event.get("user_id", "")))
        except ServiceValidationError:
            LOGGER.warning("Ignoring shopping command from an unlinked Telegram user")
            return
        data = {}
        if text.startswith("hs:"):
            parts = text.split(":")
            if len(parts) != 3 or parts[1] not in ("print_now", "rollback", "reprint", "finish"):
                return
            op, data["trip_id"] = parts[1:]
        else:
            command, _, argument = text.partition(" ")
            commands = {"!shopping": "start", "!reprint": "reprint", "!cancel": "rollback", "!rollback": "rollback", "!printnow": "print_now", "!done": "finish"}
            op = commands.get(command.lower())
            if op is None:
                return
            data["store"] = argument.strip()
        try:
            await self.perform(op, data, actor, context)
        except (ValueError, HomeAssistantError) as err:
            await self.message(f"Shopping: {err}")
