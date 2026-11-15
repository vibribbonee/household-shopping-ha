# Household shopping

[![Add to Home Assistant Marketplace](https://my.home-assistant.io/badges/marketplace_repository.svg)](https://my.home-assistant.io/redirect/marketplace_repository/?owner=vibribbonee&repository=household-shopping-ha&category=integration)

A local Home Assistant custom integration plus one dashboard card. It uses the existing Epson and Brother integrations in project 03, and keeps `todo.shopping_list` as the household's grocery list.

## Everyday use

- Add an item with its shop, quantity and requester, using the existing shopping form or the new card.
- Scan an existing shop tag or tap **I'm going**. A five-minute window starts for that shopper. Scan another shop during the window to include it in the same trip. Repeating a shop scan does nothing and never resets the deadline.
- Enter **Guest shopper** beside **I’m going** to use their name on the announcement and receipt. The signed-in person manages the trip.
- The household receives a Telegram message and an announcement through the configured idle voice satellite. Telegram and the dashboard offer **Print now** and **Cancel trip**.
- At the deadline, each shop gets its own receipt, grouped by item, with requester quantities, loyalty card and repayment checkboxes. Only matching items are included. Untagged items remain on the list.
- Sent items are marked completed, retaining their UIDs. Reprint uses the saved snapshot including the original time and loyalty card. It does not consume new requests.
- **Undo whole trip** restores that trip's completed items across all shops. **Undo this shop** restores only that shop. New additions are preserved. If a printed item was edited or deleted meanwhile, undo stops and asks for a manual decision rather than replacing someone's changes.
- Choose **Custom guest…** under **For** to put a guest name on an item. Use the paper receipt to check off paybacks. **Shopping done** closes rollback.
- The Brother QL prints one opening-date sticker on request, containing only the date (`DD-MM-YYYY`). Tap **Print today’s opening date**, or choose another date if you opened something earlier. Today uses HA’s configured local date. This records the opening date; it does not calculate an expiry date.

## Telegram identity

Log in to HA as yourself, open the shopping card and press **Connect my Telegram**. Send `!link CODE` to the existing household bot within ten minutes. Codes are single-use, stored only as hashes, and bind the sender's Telegram ID to that HA user. Existing `telegram_user_id` person attributes also work.

Commands: `!shopping ah`, `!shopping action`, `!printnow`, `!reprint`, `!cancel`, `!rollback`, `!done`. A shop name after `!reprint` or `!cancel` selects that shop. Buttons identify the exact trip so old messages do not accidentally operate on a newer trip. The person who starts the trip manages its lifecycle.

## Stores and loyalty cards

Open **Manage stores & loyalty cards** on the Shopping page to add a store or edit an existing one. **Remove store** hides the store and clears its saved loyalty setting. Saved receipts and shopping items are kept. Add the store again to shop for its pending items.

Existing Albert Heijn, Vomar and Action cards are read from their original helpers until the first edit on the Shopping page. Enter a loyalty value in one of these formats:

- `_data` or `qr:data`: QR code.
- 12 or 13 digits: EAN-13; a supplied check digit is validated by the Epson renderer.
- Other printable ASCII: Code 128.
- `image:cards/ah.png`: local image at `/config/www/cards/ah.png`.
- Blank or `None`: no card.

The card value is saved with the receipt. Reprints therefore retain the card used on that trip. Remote card images are not downloaded automatically.

## Install

Copy `custom_components/household_shopping` into `/config/custom_components`, restart HA, then add **Household Shopping** under Settings → Devices & services. Select the Epson entry, shopping to-do list, optional QL entry, household Telegram notify entity and announcement satellite. One integration entry serves the household.

Host `www/household-shopping-card.js` under `/config/www` and register `/local/household-shopping-card.js?v=1.0.0` as a module resource. Add:

```yaml
type: custom:household-shopping-card
entity: sensor.household_shopping
```

The `household_shopping.request` action handles trips, adding items, repayments, opening-date labels, and input routing. Its `operation` field selects the operation. An omitted `trip_id` selects the caller's latest eligible trip. A native HA automation routes `tag` and `telegram_text`/`telegram_callback` events to this action; backend timers persist deadlines independently of automation delays.

## Persistence and failures

The integration uses Home Assistant's `Store` API for durable trip snapshots, job states and Telegram links. No scripts hand-edit HA's `.storage` files. A restart restores collection deadlines; an overdue collection runs when HA starts. An interrupted print is marked **attention** and never automatically reprinted. A service error preserves snapshots and leaves unprocessed list items available for undo/retry.

TCP delivery is not physical print confirmation. Check the Epson paper before reprinting a job marked uncertain. A network interface that accepts connections while failing to print may need restarting, as happened during setup. This integration never factory-resets printers.

Tests use isolated fake HA services and storage: multi-store snapshotting, grouping, quantities, loyalty formats, partial failures, restart interruption, reprint, repayments, ownership and rollback. They do not print paper, announce messages, or change the live household list.

`ha-before-migration.json` contains the fetched automation/script/dashboard configurations from before the migration. Use HA's configuration APIs to restore individual objects if necessary. Disabling/removing this custom integration alone does not restore the previous automation configuration.

## Contributing

Pull requests are welcome. Describe what changed and how you tested it.

## AI disclosure

OpenAI Codex assisted with development, testing, and documentation.
