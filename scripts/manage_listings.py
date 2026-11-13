#!/usr/bin/env python3
"""
Listing Management Tool (Revise / Re-price / Update Fields in-place).
Connects to the persistent Chrome CDP session (localhost:9222) and updates
existing eBay drafts or already-live listings in-place without creating duplicates.

Safety Rule:
- For `AddItem` drafts: NEVER clicks "List it" — always clicks "Save for later".
- For `ReviseItem` on already-live listings: clicks "Revise it" to apply the edit.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from create_drafts import safe_click_draft_button  # noqa: E402

MANIFEST_PATH = Path("items/batch_manifest.json")


def _match_skus(items: List[Dict[str, Any]], sku_arg: Optional[str]) -> List[Dict[str, Any]]:
    if not sku_arg:
        return items
    targets = {s.strip().lower() for s in sku_arg.split(",") if s.strip()}
    return [
        it
        for it in items
        if str(it.get("item_id", "")).lower() in targets
        or str(it.get("sku", "")).lower() in targets
    ]


async def revise_ebay_listing(
    context: Any,
    item: Dict[str, Any],
    fields: List[str],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Open an existing eBay draft or live listing and update the specified fields in-place.
    Returns updated metadata (e.g., ebay_item_id if discovered).
    """
    item_id_ebay = str(item.get("ebay_item_id") or "").strip()
    draft_url = str(item.get("ebay_draft_url") or "").strip()
    if not item_id_ebay and (not draft_url or "draft_id" not in draft_url.lower()):
        raise RuntimeError(f"No valid eBay itemId or draft_id URL found for {item.get('item_id')}")

    price = float(item.get("ebay_list_price_usd") or item.get("comp_price") or 25.0)
    weight_lbs = float((item.get("shipping") or {}).get("actual_weight_lbs") or item.get("weight_lbs") or 1.0)
    ebay_free_shipping = (
        bool(item["ebay_free_shipping"])
        if "ebay_free_shipping" in item
        else (weight_lbs < 10.0)
    )
    title = str(item.get("title") or "")[:80]
    quantity = int(item.get("quantity") or 1)
    description = str(item.get("description_detailed") or item.get("description_concise") or title)

    print(
        f"  [eBay Revise] {item.get('item_id')} "
        f"(fields={fields}, price=${price:.2f}, free_ship={ebay_free_shipping})",
        flush=True,
    )
    if dry_run:
        return {}

    page = await context.new_page()
    try:
        is_live_revision = False
        if item_id_ebay:
            target_url = f"https://www.ebay.com/lstng?itemId={item_id_ebay}&mode=ReviseItem"
            is_live_revision = True
        else:
            target_url = draft_url

        await page.goto(target_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        if "signin" in page.url.lower() or "login" in page.url.lower():
            raise RuntimeError("eBay session is not signed in. Please sign in to Chrome CDP profile first.")

        # Check if an AddItem draft URL now says "Your listing is now live | ID-<itemId>"
        if not is_live_revision:
            body_txt = await page.locator("body").inner_text()
            m = re.search(r"ID-(\d+)", body_txt)
            if m and "now live" in body_txt.lower():
                item_id_ebay = m.group(1)
                is_live_revision = True
                rev_url = f"https://www.ebay.com/lstng?itemId={item_id_ebay}&mode=ReviseItem"
                print(f"  [eBay Revise] Listing is live (itemId={item_id_ebay}); opening {rev_url}", flush=True)
                await page.goto(rev_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(4000)

        # Wait for editor inputs to hydrate
        for _ in range(20):
            if await page.locator("input[name='price'], input[aria-label='Item price']").count() > 0:
                break
            await page.wait_for_timeout(500)

        if "title" in fields:
            for sel in ("input[name='title']", "input[aria-label*='Title']", "input[id*='title']"):
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.fill("")
                    await loc.fill(title)
                    print(f"  [eBay Revise] Updated title: {title}", flush=True)
                    break

        if "price" in fields:
            updated_price = False
            for sel in (
                "input[name='price']",
                "input[aria-label='Item price']",
                "input[aria-label*='Price']",
                "input[id*='binPrice']",
            ):
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click()
                    await loc.fill("")
                    await loc.fill(f"{price:.2f}")
                    await loc.press("Tab")
                    await page.wait_for_timeout(400)
                    print(f"  [eBay Revise] Updated Item price to: ${price:.2f}", flush=True)
                    updated_price = True
                    break
            if not updated_price:
                print("  [eBay Revise] Warning: Could not locate Price input.", flush=True)

        if "quantity" in fields:
            for sel in ("input[name='quantity']", "input[aria-label*='Quantity']", "input[id*='quantity']"):
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.fill("")
                    await loc.fill(str(quantity))
                    print(f"  [eBay Revise] Updated quantity to: {quantity}", flush=True)
                    break

        if "shipping" in fields and ebay_free_shipping:
            toggled = False
            for sel in (
                "input[name='freeShipping']",
                "input[type='checkbox'][aria-label*='Free shipping']",
                "label:has-text('Free shipping') input[type='checkbox']",
            ):
                loc = page.locator(sel).first
                # Wait briefly for shipping section to hydrate
                for _ in range(10):
                    if await loc.count() > 0:
                        break
                    await page.wait_for_timeout(500)
                if await loc.count() > 0:
                    await loc.scroll_into_view_if_needed()
                    await page.wait_for_timeout(300)
                    if await loc.is_visible():
                        if not await loc.is_checked():
                            await loc.check()
                            await page.wait_for_timeout(500)
                        print("  [eBay Revise] Free shipping enabled (< 10 lbs).", flush=True)
                        toggled = True
                        break
            if not toggled:
                print("  [eBay Revise] Note: Free shipping checkbox not visible/matched.", flush=True)

        if "description" in fields:
            desc_updated = False
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                body = frame.locator("body[contenteditable='true']")
                if await body.count() > 0:
                    await body.first.click()
                    await body.first.fill(description)
                    print("  [eBay Revise] Updated description in rich-text iframe.", flush=True)
                    desc_updated = True
                    break
            if not desc_updated:
                ta = page.locator("textarea[aria-label*='Description'], textarea[name='description']").first
                if await ta.count() > 0 and await ta.is_visible():
                    await ta.fill(description)
                    print("  [eBay Revise] Updated description textarea.", flush=True)

        if "photos" in fields:
            photos = [str(Path(p).resolve()) for p in item.get("photos", []) if Path(p).exists()]
            if photos:
                fi = page.locator("input[type='file']").first
                if await fi.count() > 0:
                    await fi.set_input_files(photos[:24])
                    await page.wait_for_timeout(3000)
                    print(f"  [eBay Revise] Uploaded {len(photos[:24])} photo(s).", flush=True)

        if is_live_revision:
            revise_btn = page.locator("button:has-text('Revise it'), button[aria-label='Revise it']").first
            await revise_btn.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            await revise_btn.click()
            await page.wait_for_timeout(5000)
            print(f"  [eBay Revise] Clicked 'Revise it' -> {page.url}", flush=True)
        else:
            await safe_click_draft_button(
                page,
                selectors=[
                    "button:has-text('Save for later')",
                    "button:has-text('Save as draft')",
                    "button[aria-label*='Save for later']",
                ],
                platform_name="eBay",
            )
            await page.wait_for_timeout(4000)

        return {"ebay_item_id": item_id_ebay} if item_id_ebay else {}
    finally:
        await page.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Revise existing eBay/FBMP/CL drafts or listings in-place.")
    parser.add_argument("--sku", required=True, help="Comma-separated item_id(s) or SKU(s), e.g. item_08,item_07,item_02")
    parser.add_argument("--platform", default="ebay", choices=["ebay"], help="Platform to revise (currently ebay)")
    parser.add_argument(
        "--fields",
        default="price,shipping",
        help="Comma-separated fields to update: price,shipping,title,quantity,description,photos",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print actions without modifying browser forms")
    args = parser.parse_args()

    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Manifest not found: {MANIFEST_PATH}")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    targets = _match_skus(manifest.get("items", []), args.sku)
    if not targets:
        print(f"No matching items found for --sku {args.sku!r}")
        return

    fields = [f.strip().lower() for f in args.fields.split(",") if f.strip()]
    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")

    p = await async_playwright().start()
    try:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        changed = False
        for item in targets:
            if args.platform == "ebay":
                updates = await revise_ebay_listing(context, item, fields=fields, dry_run=args.dry_run)
                if updates:
                    item.update(updates)
                    changed = True
        if changed and not args.dry_run:
            MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    finally:
        await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
