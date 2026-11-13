#!/usr/bin/env python3
"""
Listing Management Tool (Revise / Re-price / Update Fields in-place).
Connects to the persistent Chrome CDP session (localhost:9222) and updates
existing eBay, Facebook Marketplace, and Craigslist drafts or live listings
in-place without creating duplicates.

Safety Rule:
- For `AddItem` / `Draft` items: NEVER clicks "List it" or "Publish" — always
  clicks "Save for later" / "Save draft".
- For already-live listings (`ReviseItem`, FBMP Active, CL Active): applies the
  edit (`Revise it`, `Update`, `publish`).
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
    """Open an existing eBay draft or live listing and update the specified fields in-place."""
    item_id_ebay = str(item.get("ebay_item_id") or "").strip()
    draft_url = str(item.get("ebay_draft_url") or "").strip()
    if not item_id_ebay and (not draft_url or "draft_id" not in draft_url.lower()):
        print(f"  [eBay Revise] Skipping {item.get('item_id')}: no eBay draft_id or item_id.")
        return {}

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
            for sel in ("input[name='price']", "input[aria-label='Item price']", "input[aria-label*='Price']"):
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click()
                    await loc.fill("")
                    await loc.fill(f"{price:.2f}")
                    await loc.press("Tab")
                    await page.wait_for_timeout(400)
                    print(f"  [eBay Revise] Updated Item price to: ${price:.2f}", flush=True)
                    break

        if "quantity" in fields:
            for sel in ("input[name='quantity']", "input[aria-label*='Quantity']"):
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.fill("")
                    await loc.fill(str(quantity))
                    print(f"  [eBay Revise] Updated quantity to: {quantity}", flush=True)
                    break

        if "shipping" in fields and ebay_free_shipping:
            for sel in (
                "input[name='freeShipping']",
                "input[type='checkbox'][aria-label*='Free shipping']",
                "label:has-text('Free shipping') input[type='checkbox']",
            ):
                loc = page.locator(sel).first
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
                        break

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


async def revise_fbmp_listing(
    context: Any,
    item: Dict[str, Any],
    fields: List[str],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Find an FBMP draft or active listing via exact title and update fields in-place."""
    title = str(item.get("title") or "")[:80]
    fb_price = int(round(float(item.get("fbmp_list_price_usd") or item.get("comp_price") or 25.0)))
    description = str(item.get("description_detailed") or item.get("description_concise") or title)
    listing_id = str(item.get("fbmp_listing_id") or "").strip()

    print(f"  [FBMP Revise] {item.get('item_id')} '{title}' (fields={fields}, price=${fb_price})", flush=True)
    if dry_run:
        return {}

    page = await context.new_page()
    try:
        is_draft = False
        if listing_id:
            await page.goto(f"https://www.facebook.com/marketplace/edit/?listing_id={listing_id}", wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)
        else:
            await page.goto("https://www.facebook.com/marketplace/you/selling", wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)
            more_sel = f"[role='button'][aria-label='More options for {title}']"
            for _ in range(20):
                if await page.locator(more_sel).count() > 0:
                    break
                await page.mouse.wheel(0, 1500)
                await page.wait_for_timeout(800)

            more = page.locator(more_sel).first
            if await more.count() == 0:
                print(f"  [FBMP Revise] Listing '{title}' not found on Your listings.", flush=True)
                return {}

            await more.scroll_into_view_if_needed()
            await page.wait_for_timeout(400)
            await more.click()
            await page.wait_for_timeout(1200)

            edit_opt = page.locator("[role='menuitem']:has-text('Edit listing')").first
            draft_opt = page.locator("[role='menuitem']:has-text('View draft')").first
            if await edit_opt.count() > 0 and await edit_opt.is_visible():
                await edit_opt.click()
                await page.wait_for_timeout(3500)
                m_id = re.search(r"listing_id=(\d+)", page.url)
                if m_id:
                    listing_id = m_id.group(1)
            elif await draft_opt.count() > 0 and await draft_opt.is_visible():
                is_draft = True
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(300)
                # Click the 'Continue' link on the same draft card
                cont_link = more.locator("xpath=ancestor::div[position() <= 4][.//a[@aria-label='Continue']][1]//a[@aria-label='Continue']").first
                if await cont_link.count() > 0:
                    await cont_link.click()
                    await page.wait_for_timeout(3500)
                else:
                    print("  [FBMP Revise] Could not find Continue link for draft.", flush=True)
                    return {}
            else:
                print("  [FBMP Revise] Neither 'Edit listing' nor 'View draft' found.", flush=True)
                return {}

        if "title" in fields:
            t_in = page.locator("label:has-text('Title') input").first
            if await t_in.count() > 0 and await t_in.is_visible():
                await t_in.fill("")
                await t_in.fill(title)

        if "price" in fields:
            p_in = page.locator("label:has-text('Price') input").first
            if await p_in.count() > 0 and await p_in.is_visible():
                await p_in.click()
                await p_in.fill("")
                await p_in.fill(str(fb_price))
                print(f"  [FBMP Revise] Updated price to ${fb_price}", flush=True)

        if "description" in fields:
            d_in = page.locator("label:has-text('Description') textarea").first
            if await d_in.count() > 0 and await d_in.is_visible():
                await d_in.fill("")
                await d_in.fill(description)

        if "photos" in fields:
            photos = [str(Path(p).resolve()) for p in item.get("photos", []) if Path(p).exists()]
            if photos:
                fi = page.locator("input[type='file'][accept*='image']").first
                if await fi.count() > 0:
                    await fi.set_input_files(photos[:20])
                    await page.wait_for_timeout(3000)

        if is_draft:
            await safe_click_draft_button(
                page,
                selectors=["div[aria-label='Save draft']", "div[role='button']:has-text('Save draft')"],
                platform_name="FBMP",
            )
            await page.wait_for_timeout(3000)
            return {"fbmp_listing_url": page.url} if "draft_id" in page.url else {}

        # Active listing: click Update (or Next -> Update across multi-step edit)
        for _ in range(3):
            upd = page.locator("div[aria-label='Update'], div[role='button']:has-text('Update'), button:has-text('Update')").first
            if await upd.count() > 0 and await upd.is_visible():
                await upd.click()
                await page.wait_for_timeout(3500)
                print(f"  [FBMP Revise] Clicked 'Update' -> {page.url}", flush=True)
                break
            nxt = page.locator("div[aria-label='Next'], div[role='button']:has-text('Next')").first
            if await nxt.count() > 0 and await nxt.is_visible():
                await nxt.click()
                await page.wait_for_timeout(2500)
            else:
                break

        return {"fbmp_listing_id": listing_id} if listing_id else {}
    finally:
        await page.close()


async def revise_craigslist_listing(
    context: Any,
    item: Dict[str, Any],
    fields: List[str],
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Revise an existing Craigslist preview draft or live posting in-place."""
    cl_url = str(item.get("craigslist_edit_url") or "").strip()
    if not cl_url:
        print(f"  [CL Revise] Skipping {item.get('item_id')}: no craigslist_edit_url.")
        return {}

    cl_price = int(round(float(item.get("craigslist_list_price_usd") or item.get("comp_price") or 25.0)))
    title = str(item.get("title") or "")[:70]
    description = str(item.get("description_detailed") or item.get("description_concise") or title)

    print(f"  [CL Revise] {item.get('item_id')} -> {cl_url} (fields={fields}, price=${cl_price})", flush=True)
    if dry_run:
        return {}

    page = await context.new_page()
    try:
        await page.goto(cl_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        body_txt = await page.locator("body").inner_text()
        is_live = "thanks for posting" in body_txt.lower()

        edit_btn = page.locator(
            "button:has-text('edit post'), input[type='submit'][value*='edit post'], input[type='submit'][value='edit']"
        ).first
        if await edit_btn.count() > 0 and await edit_btn.is_visible():
            await edit_btn.click()
            await page.wait_for_timeout(2500)

        if "title" in fields:
            t_in = page.locator("input[name='PostingTitle']").first
            if await t_in.count() > 0:
                await t_in.fill(title)
        if "price" in fields:
            p_in = page.locator("input[name='price']").first
            if await p_in.count() > 0:
                await p_in.fill("")
                await p_in.fill(str(cl_price))
                print(f"  [CL Revise] Updated price to ${cl_price}", flush=True)
        if "description" in fields:
            d_in = page.locator("textarea[name='PostingBody']").first
            if await d_in.count() > 0:
                await d_in.fill(description)

        cont = page.locator("button[name='go'], button:has-text('continue')").first
        if await cont.count() > 0 and await cont.is_visible():
            await cont.click()
            await page.wait_for_timeout(2500)

        if is_live:
            pub = page.locator("button[name='go']:has-text('publish'), button:has-text('publish')").first
            if await pub.count() > 0 and await pub.is_visible():
                await pub.click()
                await page.wait_for_timeout(2500)
                print(f"  [CL Revise] Published live edit -> {page.url}", flush=True)
        else:
            print(f"  [CL Revise] Parked on preview (not published): {page.url}", flush=True)

        return {}
    finally:
        await page.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Revise existing eBay/FBMP/CL drafts or listings in-place.")
    parser.add_argument("--sku", required=True, help="Comma-separated item_id(s) or SKU(s), e.g. item_08,item_07,item_02")
    parser.add_argument("--platform", default="ebay", choices=["ebay", "fbmp", "craigslist", "all"])
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
            if args.platform in ("ebay", "all"):
                upd = await revise_ebay_listing(context, item, fields=fields, dry_run=args.dry_run)
                if upd:
                    item.update(upd)
                    changed = True
            if args.platform in ("fbmp", "all"):
                upd = await revise_fbmp_listing(context, item, fields=fields, dry_run=args.dry_run)
                if upd:
                    item.update(upd)
                    changed = True
            if args.platform in ("craigslist", "all"):
                upd = await revise_craigslist_listing(context, item, fields=fields, dry_run=args.dry_run)
                if upd:
                    item.update(upd)
                    changed = True
        if changed and not args.dry_run:
            MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    finally:
        await p.stop()


if __name__ == "__main__":
    asyncio.run(main())
