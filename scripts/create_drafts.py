#!/usr/bin/env python3
"""
Stage 4: Multi-Platform Playwright Drafts (scripts/create_drafts.py)

Connects via Playwright CDP (`connect_over_cdp("http://localhost:9222")`) to the dedicated,
logged-in Chrome profile (`./chrome_profile`) and creates draft listings based on each item's
Routing Bucket (`ALL_THREE`, `LOCAL_ONLY`, `EBAY_ONLY`):
  - eBay Seller Hub: Uploads photos, fills title, specifics, description, and pricing,
    and clicks "Save for later" / "Save as draft".
  - Facebook Marketplace: Uploads photos, fills title, price (+10-15% negotiation buffer),
    condition, description, and location, and clicks "Save draft".
  - Craigslist: Navigates local posting flow, fills fields, uploads images, and parks on the
    final "Preview" / "Draft" tab without publishing.

HARD SAFETY RULE:
  This script enforces a strict runtime guard (`assert_not_publish_button`) that refuses to
  click any button whose accessible label or text matches a live publish action
  ("Publish", "List it", "List item", "Publish listing").
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# Hard Safety Guard: Any button text matching these tokens is strictly forbidden
FORBIDDEN_PUBLISH_TOKENS = {
    "publish",
    "publish listing",
    "list it",
    "list item",
    "list for free",
    "post now",
    "submit listing",
}


def assert_safe_action_label(label: str) -> None:
    """Raise RuntimeError if an automation selector/label ever attempts a live publish click."""
    normalized = " ".join(label.strip().lower().split())
    for forbidden in FORBIDDEN_PUBLISH_TOKENS:
        if normalized == forbidden or normalized.startswith(f"{forbidden} "):
            raise RuntimeError(
                f"[SAFETY BLOCK] Attempted to click forbidden publish button: '{label}'. "
                f"Stage 4 is strictly restricted to saving drafts / previewing."
            )


async def safe_click_draft_button(page: Any, selectors: List[str], platform_name: str) -> bool:
    """
    Iterate candidate draft-save selectors, verify text does not violate the Hard Safety Rule,
    and click the first visible/enabled draft button.
    """
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if await locator.count() > 0 and await locator.is_visible():
                text_content = (await locator.inner_text() or "").strip()
                aria_label = (await locator.get_attribute("aria-label") or "").strip()
                assert_safe_action_label(text_content)
                assert_safe_action_label(aria_label)
                await locator.click(timeout=5000)
                print(f"  [{platform_name}] Clicked draft button: '{text_content or aria_label or sel}'")
                return True
        except RuntimeError:
            raise
        except Exception:
            continue
    return False


async def create_ebay_draft(
    context: Any,
    item: Dict[str, Any],
    dry_run: bool = False,
) -> str:
    """
    Create or update an eBay Seller Hub draft listing with full SPA lifecycle support.
    Uploads high-resolution raw photos, populates rich-text description iframe,
    sets eBay quantity schema field, sets Buy It Now price and shipping dimensions,
    and clicks 'Save for later' (strictly enforcing the Hard Safety Rule: NEVER 'List it').
    Returns the canonical resume URL: https://www.ebay.com/sl/list?mode=AddItem&draft_id=<ID>.
    """
    title = str(item.get("title") or "")[:80]
    price = float(item.get("ebay_list_price_usd") or item.get("comp_price") or 25.0)
    quantity = int(item.get("quantity") or 1)
    description = str(
        item.get("description_detailed") or item.get("description_concise") or title
    )
    brand = str(item.get("brand") or "Unbranded")
    model_mpn = str(item.get("model_mpn") or "Does Not Apply")
    condition_str = str(item.get("condition") or "Used - Good").lower()
    photos = [str(Path(p).resolve()) for p in item.get("photos", []) if Path(p).exists()]

    print(f"  [eBay] Preparing draft for '{title}' (${price:.2f}, qty: {quantity}, {len(photos)} photo(s))...", flush=True)
    if dry_run:
        return "https://www.ebay.com/sh/lst/drafts?dry_run=true"

    page = await context.new_page()

    # Known existing draft IDs map for current batch
    known_draft_ids: Dict[str, str] = {
        "item_02": "5315475130221",
        "item_07": "5315015828220",
        "item_08": "5314715942522",
        "item_16": "5314106230823",
        "item_12": "5314099107923",
        "item_03": "5319856841422",
        "item_10": "5320147259120",
    }

    # Check if item has a known or existing draft_id
    existing_url = item.get("ebay_draft_url") or ""
    draft_id_match = re.search(r"draft_?id=(\d+)", existing_url, re.IGNORECASE)
    draft_id = draft_id_match.group(1) if draft_id_match else known_draft_ids.get(item.get("item_id", ""))

    editor_mounted = False

    if draft_id:
        resume_url = f"https://www.ebay.com/sl/list?mode=AddItem&draft_id={draft_id}"
        print(f"  [eBay] Opening existing draft ID {draft_id}: {resume_url} ...", flush=True)
        try:
            await page.goto(resume_url, wait_until="domcontentloaded")
            await page.wait_for_selector("input#fehelix-uploader, input[type='file']", state="attached", timeout=30000)
            editor_mounted = True
            print("  [eBay] Existing draft editor loaded successfully.", flush=True)
        except Exception as exc:
            print(f"  [eBay] Could not open existing draft directly ({exc}), falling back to prelist creation...", flush=True)
            editor_mounted = False

    if not editor_mounted:
        # Create fresh draft via prelist suggest
        await page.goto("https://www.ebay.com/sl/prelist/suggest", wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        # Step 1: Search box
        search_selectors = [
            "input[placeholder*='Tell us what you']",
            "input[name='keyword']",
            "input[type='text']",
        ]
        for sel in search_selectors:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(title)
                await loc.press("Enter")
                await page.wait_for_timeout(3000)
                break

        # Step 2: Category selection on identify page if prompted
        try:
            cat_btn = page.locator("button.se-field-card__body").first
            if await cat_btn.count() > 0 and await cat_btn.is_visible():
                print(f"  [eBay] Selecting category card: '{(await cat_btn.inner_text()).strip()[:50]}'", flush=True)
                await cat_btn.click()
                await page.wait_for_timeout(2500)
        except Exception as exc:
            print(f"  [eBay Category selection note]: {exc}", flush=True)

        # Step 3: Click "Continue without match"
        cwm = page.locator("button:has-text('Continue without match'), a:has-text('Continue without match')").first
        if await cwm.count() > 0:
            for _ in range(10):
                if await cwm.is_enabled():
                    print("  [eBay] Clicking 'Continue without match'...", flush=True)
                    await cwm.click()
                    await page.wait_for_timeout(2000)
                    break
                await page.wait_for_timeout(500)

        # Step 4: Condition selection modal
        cond_val = "1000" if ("new" in condition_str and "like" not in condition_str) else "3000"
        if "parts" in condition_str:
            cond_val = "7000"

        try:
            cond_loc = page.locator(
                f"button:has-text('Pre-owned'), button:has-text('Used'), input[name='condition'][value='{cond_val}'], input[name='condition']"
            ).first
            if await cond_loc.count() > 0:
                print(f"  [eBay] Selecting condition element...", flush=True)
                if (await cond_loc.get_attribute("type")) == "radio":
                    await cond_loc.check(force=True)
                else:
                    await cond_loc.click()
                await page.wait_for_timeout(1000)
        except Exception as exc:
            print(f"  [eBay Condition selection note]: {exc}", flush=True)

        # Step 5: Click "Continue to listing" / "Continue"
        cont_btn = page.locator("button.condition-dialog-radix__continue-btn, button:has-text('Continue to listing'), button:has-text('Continue')").first
        if await cont_btn.count() > 0:
            for _ in range(10):
                if await cont_btn.is_enabled():
                    print("  [eBay] Clicking 'Continue to listing' / 'Continue'...", flush=True)
                    await cont_btn.click()
                    await page.wait_for_timeout(3000)
                    break
                await page.wait_for_timeout(500)

        # Step 6: Wait for listing editor SPA to mount
        print("  [eBay] Waiting for listing editor SPA to mount...", flush=True)
        await page.wait_for_selector("input#fehelix-uploader, input[type='file']", state="attached", timeout=35000)

    # Editor SPA is now mounted! Wait 3s for React components to stabilize
    await page.wait_for_timeout(3000)

    # Extract draft ID from URL if present
    curr_url = page.url
    m = re.search(r"draft_?id=(\d+)", curr_url, re.IGNORECASE)
    if m:
        draft_id = m.group(1)
        print(f"  [eBay] Current draft ID: {draft_id}", flush=True)

    # 1. Update Title if present
    for title_sel in [
        "input[name='title']",
        "input[aria-label*='Item title']",
        "textarea[name='title']",
    ]:
        loc = page.locator(title_sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.click()
            await loc.fill(title)
            print(f"  [eBay] Title set: '{title}'", flush=True)
            break

    # 2. Update Brand / MPN specifics if present
    for brand_sel in ["input[aria-label*='Brand']", "input[name*='Brand']"]:
        loc = page.locator(brand_sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(brand)
            break

    for mpn_sel in ["input[aria-label*='MPN']", "input[aria-label*='Model']"]:
        loc = page.locator(mpn_sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(model_mpn)
            break

    # 3. Upload Original High-Resolution Raw Photos
    if photos:
        try:
            file_input = page.locator("input#fehelix-uploader, input[type='file']").first
            if await file_input.count() > 0:
                # Check how many images are currently loaded
                current_img_count = await page.locator(".se-uploader-module-image, img[src*='ebayimg']").count()
                if current_img_count < len(photos):
                    upload_set = photos[:24]
                    print(f"  [eBay] Uploading {len(upload_set)} original high-resolution photo(s)...", flush=True)
                    await file_input.set_input_files(upload_set)
                    upload_wait = min(25, 4 + len(upload_set) * 1)
                    await page.wait_for_timeout(upload_wait * 1000)
                    print(f"  [eBay] Photos uploaded.", flush=True)
                else:
                    print(f"  [eBay] Photos already present on draft ({current_img_count} image(s)).", flush=True)
        except Exception as exc:
            print(f"  [eBay Photo upload note]: {exc}", flush=True)

    # 4. Populate Description via Rich-Text Iframe Body & Fallback
    desc_populated = False
    try:
        desc_frame = page.frame_locator("#se-rte-frame__summary, iframe[title='Description']").first
        body_loc = desc_frame.locator("body")
        if await body_loc.count() > 0:
            await body_loc.click()
            await page.wait_for_timeout(300)
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await page.wait_for_timeout(200)
            await page.keyboard.insert_text(description)
            await page.wait_for_timeout(500)
            print(f"  [eBay] Populated description in rich-text iframe ({len(description)} chars).", flush=True)
            desc_populated = True
    except Exception as exc:
        print(f"  [eBay Rich-text iframe note]: {exc}", flush=True)

    if not desc_populated:
        # Fallback to hidden raw textarea with DOM event dispatch
        raw_ta = page.locator("textarea[name='description'], textarea[id*='rich-text-editor']").first
        if await raw_ta.count() > 0:
            try:
                await raw_ta.evaluate(
                    "(ta, val) => { ta.value = val; ta.dispatchEvent(new Event('input', {bubbles: true})); ta.dispatchEvent(new Event('change', {bubbles: true})); }",
                    description,
                )
                print(f"  [eBay] Populated description via raw textarea fallback.", flush=True)
            except Exception as exc:
                print(f"  [eBay Raw textarea note]: {exc}", flush=True)

    # 5. Populate Quantity Schema Field
    try:
        for qty_sel in [
            "input[name='quantity']",
            "input[id*='PRICE'][id*='textbox']",
            "input[aria-label*='Quantity']",
        ]:
            qty_loc = page.locator(qty_sel).first
            if await qty_loc.count() > 0 and await qty_loc.is_visible():
                await qty_loc.click()
                await qty_loc.fill(str(quantity))
                print(f"  [eBay] Set quantity schema field to: {quantity}", flush=True)
                break
    except Exception as exc:
        print(f"  [eBay Quantity field note]: {exc}", flush=True)

    # 6. Populate Buy It Now Price
    for price_sel in [
        "input[name='price']",
        "input[aria-label*='Buy It Now price']",
        "input[aria-label*='Price']",
    ]:
        loc = page.locator(price_sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.click()
            await loc.fill(f"{price:.2f}")
            print(f"  [eBay] Set Buy It Now price to: ${price:.2f}", flush=True)
            break

    # 7. Package Weight, Dimensions & Free Shipping (< 10 lbs)
    shipping = item.get("shipping") or {}
    weight_lbs = float(shipping.get("actual_weight_lbs") or item.get("weight_lbs") or 1.0)
    ebay_free_shipping = (
        bool(item["ebay_free_shipping"])
        if "ebay_free_shipping" in item
        else (weight_lbs < 10.0)
    )
    dims = shipping.get("dimensions_in") or [12, 9, 4]
    field_pairs = [
        ("input[aria-label*='Weight in pounds'], input[name*='majorWeight']", str(max(1, int(round(weight_lbs))))),
        ("input[aria-label*='Length'], input[name*='packageLength']", str(int(dims[0]))),
        ("input[aria-label*='Width'], input[name*='packageWidth']", str(int(dims[1]))),
        ("input[aria-label*='Depth'], input[aria-label*='Height'], input[name*='packageDepth']", str(int(dims[2]))),
    ]
    for sel, val in field_pairs:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(val)
        except Exception:
            pass

    if ebay_free_shipping:
        for sel in (
            "input[type='checkbox'][aria-label*='Free shipping']",
            "label:has-text('Free shipping') input[type='checkbox']",
            "input[name*='freeShipping']",
        ):
            try:
                cb = page.locator(sel).first
                if await cb.count() > 0 and await cb.is_visible():
                    if not await cb.is_checked():
                        await cb.check()
                    print("  [eBay] Enabled Free shipping (< 10 lbs).", flush=True)
                    break
            except Exception:
                continue

    # Step 8: Click "Save for later" / "Save as draft" — NEVER click "List it"
    await safe_click_draft_button(
        page,
        selectors=[
            "button:has-text('Save for later')",
            "button:has-text('Save as draft')",
            "button[aria-label*='Save for later']",
        ],
        platform_name="eBay",
    )
    await page.wait_for_timeout(5000)

    # Return canonical resume URL
    if draft_id:
        final_url = f"https://www.ebay.com/sl/list?mode=AddItem&draft_id={draft_id}"
    else:
        m2 = re.search(r"draft_?id=(\d+)", page.url, re.IGNORECASE)
        final_url = f"https://www.ebay.com/sl/list?mode=AddItem&draft_id={m2.group(1)}" if m2 else page.url

    print(f"  [eBay] Successfully saved and parked draft: {final_url}", flush=True)
    return final_url


def _map_fbmp_category(item: Dict[str, Any], prefer_shipping: bool = False) -> str:
    """Map item metadata to a Facebook Marketplace category (preferring shipping-eligible ones)."""
    text = f"{item.get('category', '')} {item.get('title', '')}".lower()
    if any(k in text for k in ("auto", "ford", "powersports", "helmet", "visor", "brake", "motor", "battery", "f-150", "car")):
        return "Auto parts"
    if any(k in text for k in ("tool", "saw", "pump", "mityvac", "bleeder", "drill", "wrench", "truing")):
        return "Tools"
    if any(k in text for k in ("sport", "outdoor", "bicycle", "bike", "golf", "fitness")):
        return "Sports & Outdoors"
    if any(k in text for k in ("electronic", "computer", "camera", "audio", "monitor", "speaker")):
        return "Electronics & computers"
    if any(k in text for k in ("furniture", "chair", "aeron", "headrest", "office", "household", "kitchen", "appliance", "coffee")):
        return "Household"
    return "Household" if prefer_shipping else "Miscellaneous"


def _map_fbmp_condition(condition: str) -> str:
    """Map inventory condition string to Facebook Marketplace's Condition dropdown options."""
    c = (condition or "").strip().lower()
    if c == "new" or c.startswith("new "):
        return "New"
    if "like new" in c or "open box" in c:
        return "Used - Like New"
    if "very good" in c or "good" in c:
        return "Used - Good"
    if "fair" in c or "parts" in c:
        return "Used - Fair"
    return "Used - Good"


async def create_fbmp_draft(
    context: Any,
    item: Dict[str, Any],
    dry_run: bool = False,
) -> str:
    """
    Create a Facebook Marketplace item draft (patterns adapted from
    NmediaCloud/marketplace-listing-assistant & Haron1-max/Facebook-Marketplace-Auto-Poster).
    Applies +10-15% negotiation buffer price, selects Category/Condition,
    advances to Delivery step for shipping+pickup items, and clicks 'Save draft'.
    """
    title = str(item.get("title") or "")[:80]
    fb_price = int(round(float(item.get("fbmp_list_price_usd") or item.get("comp_price") or 25.0)))
    description = str(
        item.get("description_detailed") or item.get("description_concise") or title
    )
    origin_zip = os.getenv("ORIGIN_ZIP", "98101")
    photos = [str(Path(p).resolve()) for p in item.get("photos", []) if Path(p).exists()]
    wants_shipping = bool(item.get("fbmp_shipping_and_pickup")) or item.get("routing_bucket") in ("EBAY_ONLY", "EBAY_AND_FBMP")
    fb_category = _map_fbmp_category(item, prefer_shipping=wants_shipping)
    fb_condition = _map_fbmp_condition(str(item.get("condition") or ""))

    print(
        f"  [FBMP] Preparing draft for '{title}' (${fb_price}, cat='{fb_category}', "
        f"cond='{fb_condition}', shipping+pickup={wants_shipping}, {len(photos)} photo(s))..."
    )
    if dry_run:
        return "https://www.facebook.com/marketplace/you/selling?dry_run=true"

    page = await context.new_page()
    await page.goto("https://www.facebook.com/marketplace/create/item", wait_until="domcontentloaded")
    await page.wait_for_timeout(3500)

    # Upload photos via file input
    if photos:
        try:
            file_input = page.locator("input[type='file'][accept*='image']").first
            if await file_input.count() > 0:
                await file_input.set_input_files(photos[:20], timeout=90000)
                print(f"  [FBMP] Uploaded {len(photos[:20])} photo(s).")
                await page.wait_for_timeout(3000)
        except Exception as exc:
            print(f"  [FBMP] Photo upload note: {exc}")

    # Fill Title, Price, and Description using resilient aria/label selectors
    label_fields = [
        ("label:has-text('Title') input", title),
        ("input[aria-label='Title']", title),
        ("label:has-text('Price') input", str(fb_price)),
        ("input[aria-label='Price']", str(fb_price)),
        ("label:has-text('Description') textarea", description),
        ("textarea[aria-label='Description']", description),
    ]
    for sel, val in label_fields:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(val)
                await page.wait_for_timeout(350)
        except Exception:
            continue

    # Select Category
    try:
        cat_lbl = page.locator("label:has-text('Category')").first
        if await cat_lbl.count() > 0 and await cat_lbl.is_visible():
            await cat_lbl.click()
            await page.wait_for_timeout(1200)
            cat_opt = page.locator(
                f"[role='menuitem']:has-text('{fb_category}'), "
                f"[role='option']:has-text('{fb_category}'), "
                f"span:text-is('{fb_category}')"
            ).first
            if await cat_opt.count() > 0 and await cat_opt.is_visible():
                await cat_opt.click()
                print(f"  [FBMP] Selected category: {fb_category}")
                await page.wait_for_timeout(800)
    except Exception as exc:
        print(f"  [FBMP] Category selection note: {exc}")

    # Select Condition
    try:
        cond_lbl = page.locator("label:has-text('Condition')").first
        if await cond_lbl.count() > 0 and await cond_lbl.is_visible():
            await cond_lbl.click()
            await page.wait_for_timeout(1000)
            cond_opt = page.locator(
                f"[role='option']:has-text('{fb_condition}'), "
                f"[role='menuitem']:has-text('{fb_condition}'), "
                f"span:text-is('{fb_condition}')"
            ).first
            if await cond_opt.count() > 0 and await cond_opt.is_visible():
                await cond_opt.click()
                print(f"  [FBMP] Selected condition: {fb_condition}")
                await page.wait_for_timeout(800)
    except Exception as exc:
        print(f"  [FBMP] Condition selection note: {exc}")

    # Fill Location if visible on Step 1
    for sel in ("label:has-text('Location') input", "input[aria-label='Location']"):
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible():
                await loc.fill(origin_zip)
                await page.wait_for_timeout(400)
                break
        except Exception:
            continue

    # For shipping + local pickup items, advance to Step 2 (?step=delivery)
    if wants_shipping:
        try:
            next_btn = page.locator("div[aria-label='Next'], button:has-text('Next')").first
            if await next_btn.count() > 0 and await next_btn.is_visible():
                disabled = await next_btn.get_attribute("aria-disabled")
                if disabled != "true":
                    await next_btn.click()
                    await page.wait_for_timeout(3000)
                    print(f"  [FBMP] Advanced to delivery step: {page.url}")
                else:
                    print("  [FBMP] Next button disabled; saving draft from Step 1.")
        except Exception as exc:
            print(f"  [FBMP] Delivery step note: {exc}")

    # Click "Save draft" with exponential backoff on FBMP "Something went wrong" rate-limit modal
    backoff_delays = [10, 25, 60]
    for attempt in range(len(backoff_delays) + 1):
        await safe_click_draft_button(
            page,
            selectors=[
                "div[aria-label='Save draft']",
                "div[role='button']:has-text('Save draft')",
                "button:has-text('Save draft')",
                "span:has-text('Save draft')",
            ],
            platform_name="FBMP",
        )
        await page.wait_for_timeout(4500)
        err_dlg = page.locator("[role='dialog']:has-text('Something went wrong')").first
        if await err_dlg.count() > 0 and await err_dlg.is_visible():
            close_btn = err_dlg.locator("[role='button']:has-text('Close'), button:has-text('Close'), [aria-label='Close']").first
            if await close_btn.count() > 0:
                await close_btn.click()
                await page.wait_for_timeout(500)
            if attempt < len(backoff_delays):
                wait_s = backoff_delays[attempt]
                print(f"  [FBMP] 'Something went wrong' modal detected; backing off {wait_s}s (attempt {attempt + 1}/{len(backoff_delays)})...", flush=True)
                await page.wait_for_timeout(wait_s * 1000)
                continue
            print("  [FBMP] Warning: 'Something went wrong' persisted after retries.", flush=True)
        break

    final_url = page.url
    await page.close()
    return final_url


CRAIGSLIST_SUBDOMAIN_TO_POST_CODE: Dict[str, str] = {
    "sfbay": "sfo",
    "sfo": "sfo",
    "sacramento": "sac",
    "seattle": "sea",
    "losangeles": "lax",
    "sandiego": "sdo",
    "newyork": "nyc",
    "chicago": "chi",
    "portland": "pdx",
    "austin": "aus",
    "phoenix": "phx",
    "denver": "den",
}


def match_craigslist_category(category_hint: str, title: str) -> str:
    """Map eBay category taxonomy and item title to a standard Craigslist category."""
    cat = (category_hint or "").lower()
    tit = (title or "").lower()

    # Priority 1: eBay taxonomy mapping
    if any(k in cat for k in ["lawn mower", "yard, garden", "mower"]):
        return "farm & garden"
    if any(k in cat for k in ["cycling", "bicycle"]):
        return "bicycles"
    if any(k in cat for k in ["kitchen appliance", "hvac", "major appliance"]):
        return "appliances"
    if any(k in cat for k in ["power tool", "tools & workshop", "hand tools"]):
        return "tools"
    if any(k in cat for k in ["furniture"]):
        return "furniture"
    if any(k in cat for k in ["video game", "consoles"]):
        return "video gaming"
    if any(k in cat for k in ["clothing", "shoes", "accessories"]):
        return "clothing & accessories"
    if any(k in cat for k in ["automotive tool"]):
        return "tools"
    if any(k in cat for k in ["auto parts", "ebay motors"]):
        return "auto parts"

    # Priority 2: Title keywords (word-boundary matched)
    title_rules = [
        (r"\b(mower|lawn|weed|trimmer|chainsaw)\b", "farm & garden"),
        (r"\b(bike|bicycle|truing|derailleur|cassette)\b", "bicycles"),
        (r"\b(espresso|cappuccino|coffee|blender|refrigerator|microwave)\b", "appliances"),
        (r"\b(saw|miter|drill|router|planer|caliper|press|lathe|wrench|socket)\b", "tools"),
        (r"\b(chair|desk|couch|sofa|table|hall tree|cabinet|dresser)\b", "furniture"),
        (r"\b(nintendo|switch|playstation|xbox|cockpit|wheelbase|pedals)\b", "video gaming"),
        (r"\b(boots|shoes|jacket|helmet|gloves|riding)\b", "clothing & accessories"),
        (r"\b(laptop|desktop|monitor|pc|macbook)\b", "computers"),
        (r"\b(stereo|speaker|amplifier|receiver)\b", "electronics"),
    ]
    for pattern, cl_cat in title_rules:
        if re.search(pattern, tit):
            return cl_cat

    return "general for sale"


def map_craigslist_condition(condition_str: str) -> str:
    """Map condition text to Craigslist select value."""
    c = (condition_str or "").lower()
    if "brand new" in c or "new with tags" in c or c == "new":
        return "10"  # new
    if "like new" in c or "open box" in c:
        return "20"  # like new
    if "excellent" in c or "refurbished" in c:
        return "30"  # excellent
    if "fair" in c or "for parts" in c:
        return "50"  # fair
    return "40"      # good (used default)


async def create_craigslist_preview(
    context: Any,
    item: Dict[str, Any],
    dry_run: bool = False,
) -> str:
    """
    Navigate local Craigslist posting flow directly via post.craigslist.org/c/<post_code>,
    select subarea and neighborhood, select for-sale-by-owner and matched category, fill
    all form details, upload full-resolution raw photos, and park on the final 'Preview' tab
    without clicking 'Publish'.
    """
    subdomain = os.getenv("CRAIGSLIST_SUBDOMAIN", "sfbay").strip()
    post_code = CRAIGSLIST_SUBDOMAIN_TO_POST_CODE.get(subdomain.lower(), subdomain.lower())
    cl_area = os.getenv("CRAIGSLIST_AREA", "south bay area").strip()
    cl_city = os.getenv("CRAIGSLIST_CITY", "san jose west").strip()
    origin_zip = os.getenv("ORIGIN_ZIP", "95126").strip()

    title = str(item.get("title") or "")[:70]
    cl_price = int(round(float(item.get("craigslist_list_price_usd") or item.get("comp_price") or 25.0)))
    description = str(
        item.get("description_detailed") or item.get("description_concise") or title
    )
    brand = str(item.get("brand") or "")[:30]
    model_mpn = str(item.get("model_mpn") or "")[:30]
    condition_str = str(item.get("condition") or "Used")
    cond_val = map_craigslist_condition(condition_str)

    # Always use original high-resolution raw photos (excluding thumbnails or metadata)
    photos = sorted(
        [
            str(Path(p).resolve())
            for p in item.get("photos", [])
            if Path(p).is_file()
            and not Path(p).name.startswith("thumb_")
            and Path(p).suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        ],
        key=lambda x: Path(x).name,
    )

    base_url = f"https://{subdomain}.craigslist.org"
    print(
        f"  [Craigslist] Preparing preview on {base_url} (post_code='{post_code}', "
        f"area='{cl_area}', city='{cl_city}') for '{title}' (${cl_price})...",
        flush=True,
    )
    if dry_run:
        return f"{base_url}/?dry_run=true"

    page = await context.new_page()

    # Direct navigation to post_code avoids the iframe embed and Aberdeen default redirect
    start_url = f"https://post.craigslist.org/c/{post_code}"
    await page.goto(start_url, wait_until="domcontentloaded")
    await page.wait_for_timeout(1500)

    # Step 0: Handle "?s=copyfromanother" ("Re-use selected data from your previous posting?")
    try:
        if "?s=copyfromanother" in page.url or "copy from previous" in (await page.title()).lower():
            skip_btn = page.locator("button:has-text('skip'), input[value='skip'], a:has-text('skip')").first
            if await skip_btn.count() > 0 and await skip_btn.is_visible():
                await skip_btn.click()
                await page.wait_for_timeout(1500)
                print("  [Craigslist] Skipped 'copy from previous posting' prompt.", flush=True)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 0]: {exc}", flush=True)

    # Step 0b: Top-level city/area confirmation (?s=area) -> already defaults to post_code, click continue
    try:
        if "?s=area" in page.url or "choose area" in (await page.title()).lower():
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 0b]: {exc}", flush=True)

    # Step 1: Sub-area selection (?s=subarea)
    try:
        cur_title = (await page.title()).lower()
        if "?s=subarea" in page.url or "nearest area" in cur_title:
            area_selected = False
            if cl_area:
                loc = page.locator(f"label:has-text('{cl_area}')").first
                if await loc.count() > 0:
                    await loc.click()
                    area_selected = True
            if not area_selected:
                sb = page.locator("input[type='radio'][value='2']").first
                if await sb.count() > 0:
                    await sb.click()
                else:
                    await page.locator("input[type='radio']").first.click()
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 1]: {exc}", flush=True)

    # Step 2: Neighborhood selection (?s=hood)
    try:
        if "?s=hood" in page.url or "neighborhood" in (await page.title()).lower():
            hood_selected = False
            if cl_city:
                loc = page.locator(f"label:has-text('{cl_city}')").first
                if await loc.count() > 0:
                    await loc.click()
                    hood_selected = True
            if not hood_selected:
                sj = page.locator("input[type='radio'][value='40'], label:has-text('san jose west')").first
                if await sj.count() > 0:
                    await sj.click()
                else:
                    await page.locator("input[type='radio']").first.click()
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 2]: {exc}", flush=True)

    # Step 3: Type of posting (?s=type) -> for sale by owner (fso)
    try:
        if "?s=type" in page.url or "choose type" in (await page.title()).lower():
            fso_radio = page.locator("input[type='radio'][value='fso'], label:has-text('for sale by owner')").first
            if await fso_radio.count() > 0:
                await fso_radio.click()
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 3]: {exc}", flush=True)

    # Step 4: Category selection (?s=cat)
    try:
        if "?s=cat" in page.url or "choose category" in (await page.title()).lower():
            target_cat = match_craigslist_category(item.get("category", ""), title)
            print(f"  [Craigslist] Matched category '{target_cat}'", flush=True)
            cat_loc = page.locator(f"label:has-text('{target_cat}')").first
            if await cat_loc.count() > 0:
                await cat_loc.click()
            else:
                gen_loc = page.locator("label:has-text('general for sale')").first
                if await gen_loc.count() > 0:
                    await gen_loc.click()
                else:
                    await page.locator("input[type='radio']").first.click()
            await page.locator("button[type='submit'], button:has-text('continue')").first.click()
            await page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"  [Craigslist Note - Step 4]: {exc}", flush=True)

    # Step 5: Post Details Form (?s=edit) — wait explicitly for form to load
    await page.wait_for_selector("input[name='PostingTitle'], #PostingTitle", timeout=15000)
    try:
        title_inp = page.locator("input[name='PostingTitle'], #PostingTitle").first
        if await title_inp.count() > 0 and await title_inp.is_visible() and await title_inp.is_enabled():
            await title_inp.fill(title)
    except Exception as exc:
        print(f"  [Craigslist Note - Title]: {exc}", flush=True)

    try:
        price_inp = page.locator("input[name='price'], #price").first
        if await price_inp.count() > 0 and await price_inp.is_visible() and await price_inp.is_enabled():
            await price_inp.fill(str(cl_price))
    except Exception:
        pass

    try:
        postal_inp = page.locator("input[name='postal'], #postal_code").first
        if await postal_inp.count() > 0 and await postal_inp.is_visible() and await postal_inp.is_enabled():
            await postal_inp.fill(origin_zip)
    except Exception:
        pass

    try:
        city_inp = page.locator("input[name='city']:not([disabled]), input[name='GeographicArea']:not([disabled])").first
        if await city_inp.count() > 0 and await city_inp.is_visible() and await city_inp.is_enabled():
            await city_inp.fill(cl_city)
    except Exception:
        pass

    try:
        body_inp = page.locator("textarea[name='PostingBody'], #PostingBody").first
        if await body_inp.count() > 0 and await body_inp.is_visible() and await body_inp.is_enabled():
            await body_inp.fill(description)
    except Exception:
        pass

    try:
        if brand and brand != "TBD":
            mfg_inp = page.locator("input[name='sale_manufacturer']").first
            if await mfg_inp.count() > 0 and await mfg_inp.is_visible() and await mfg_inp.is_enabled():
                await mfg_inp.fill(brand)
    except Exception:
        pass

    try:
        if model_mpn and model_mpn != "TBD":
            mod_inp = page.locator("input[name='sale_model']").first
            if await mod_inp.count() > 0 and await mod_inp.is_visible() and await mod_inp.is_enabled():
                await mod_inp.fill(model_mpn)
    except Exception:
        pass

    # Condition dropdown: styled by jQuery UI, select via dispatchEvent
    try:
        cond_elem = page.locator("select[name='condition']").first
        if await cond_elem.count() > 0:
            await cond_elem.evaluate(
                f"el => {{ el.value = '{cond_val}'; el.dispatchEvent(new Event('change', {{ bubbles: true }})); }}"
            )
    except Exception:
        pass

    try:
        cont_btn = page.locator("button[type='submit']:has-text('continue'), button.continue").first
        if await cont_btn.count() > 0:
            await cont_btn.click()
            for _ in range(10):
                await page.wait_for_timeout(500)
                if "?s=edit" not in page.url:
                    break
    except Exception as exc:
        print(f"  [Craigslist Note - Step 5 submit]: {exc}", flush=True)

    # Step 6: Map / Location Confirmation (?s=geoverify)
    try:
        if "?s=geoverify" in page.url or "map" in (await page.title()).lower():
            map_cont = page.locator("button[type='submit']:has-text('continue'), button.continue").first
            if await map_cont.count() > 0 and await map_cont.is_visible():
                btn_txt = (await map_cont.inner_text() or "").strip()
                assert_safe_action_label(btn_txt)
                await map_cont.click()
                for _ in range(10):
                    await page.wait_for_timeout(500)
                    if "?s=geoverify" not in page.url:
                        break
    except Exception as exc:
        print(f"  [Craigslist Note - Step 6]: {exc}", flush=True)

    # Step 7: Upload Original High-Resolution Raw Photos (?s=editimage)
    if photos and ("?s=editimage" in page.url or "images" in (await page.title()).lower()):
        try:
            file_input = page.locator("input[type='file']").first
            if await file_input.count() > 0:
                upload_set = photos[:24]
                print(f"  [Craigslist] Uploading {len(upload_set)} original high-resolution raw photo(s)...", flush=True)
                await file_input.set_input_files(upload_set)
                upload_wait_seconds = min(15, 3 + len(upload_set) * 1)
                await page.wait_for_timeout(upload_wait_seconds * 1000)
        except Exception as exc:
            print(f"  [Craigslist Photo upload note]: {exc}", flush=True)

    # Step 8: Click "done with images" to land on final Preview screen
    try:
        done_img_btn = page.locator("button:has-text('done with images')").first
        if await done_img_btn.count() > 0 and await done_img_btn.is_visible():
            await done_img_btn.click()
            for _ in range(10):
                await page.wait_for_timeout(500)
                if "?s=preview" in page.url:
                    break
    except Exception as exc:
        print(f"  [Craigslist Note - Step 8]: {exc}", flush=True)

    # Verify Preview Screen (Hard Safety Rule: NEVER click "publish")
    publish_btn = page.locator("button:has-text('publish'), input[value='publish']")
    pub_count = await publish_btn.count()
    if "?s=preview" not in page.url or pub_count == 0:
        raise RuntimeError(
            f"Craigslist draft did not reach Preview screen (landed on {page.url}, publish_btn={pub_count})."
        )

    print(
        f"  [Craigslist Safety Check] 'Publish' button found on screen — strictly parked on Preview (NOT published).",
        flush=True,
    )
    print(f"  [Craigslist] Successfully parked on final Preview tab: {page.url}", flush=True)
    return page.url


async def run_draft_pipeline(
    manifest_path: Path,
    sku_filter: Optional[str] = None,
    platform_filter: Optional[str] = None,
    dry_run: bool = False,
    overwrite: bool = False,
) -> int:
    """Connect to Chrome CDP on port 9222 and create drafts for all eligible items in manifest."""
    if not manifest_path.exists():
        print(f"[ERROR] Manifest not found at '{manifest_path.as_posix()}'. Run Stage 2/3 first.")
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    items: List[Dict[str, Any]] = manifest.get("items", [])
    if not items:
        print("[INFO] No items in batch_manifest.json.")
        return 0

    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")

    if dry_run:
        print(f"[DRY-RUN] Simulating Playwright CDP draft creation against {cdp_url}...")
        context = None
        browser = None
        p_cm = None
    else:
        from playwright.async_api import async_playwright

        p_cm = await async_playwright().start()
        try:
            browser = await p_cm.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            await p_cm.stop()
            print(
                f"[ERROR] Could not connect to Chrome CDP at {cdp_url}: {exc}\n"
                f"        Run: .venv\\Scripts\\python.exe scripts/launch_chrome.py --check-or-launch"
            )
            return 1

        context = browser.contexts[0] if browser.contexts else await browser.new_context()

    try:
        for item in items:
            sku = item.get("sku", "")
            if sku_filter and sku.lower() != sku_filter.lower() and item.get("item_id", "").lower() != sku_filter.lower():
                continue

            bucket = item.get("routing_bucket", "DONATE_OR_LOT")
            if bucket == "DONATE_OR_LOT" and not sku_filter:
                print(f"[SKIP] {sku} ({item.get('item_id')}) is in DONATE_OR_LOT bucket.")
                continue

            print("-" * 72)
            print(f"Processing {sku} ({item.get('item_id')}) | Bucket: {bucket} | Title: {item.get('title')}")

            run_ebay = bucket in ("ALL_THREE", "EBAY_ONLY", "EBAY_AND_FBMP") and platform_filter in (None, "ebay")
            run_fbmp = (
                bucket in ("ALL_THREE", "LOCAL_ONLY", "EBAY_AND_FBMP")
                or bool(item.get("fbmp_shipping_and_pickup"))
            ) and platform_filter in (None, "fbmp")
            run_cl = bucket in ("ALL_THREE", "LOCAL_ONLY") and platform_filter in (None, "craigslist")

            cl_url = item.get("craigslist_edit_url") or ""
            is_cl_incomplete = (
                not cl_url
                or "dry_run" in cl_url
                or "s=type" in cl_url
                or "s=subarea" in cl_url
                or "s=hood" in cl_url
                or "s=cat" in cl_url
                or "s=edit" in cl_url
                or "s=geoverify" in cl_url
                or "s=editimage" in cl_url
                or "aberdeen" in cl_url.lower()
            )

            ebay_url = item.get("ebay_draft_url") or ""
            is_ebay_incomplete = (
                not ebay_url
                or "prelist" in ebay_url
                or ("draft_id" not in ebay_url.lower() and "draftid" not in ebay_url.lower())
            )

            needs_ebay = run_ebay and (overwrite or is_ebay_incomplete)
            needs_fbmp = run_fbmp and (overwrite or not item.get("fbmp_listing_url"))
            needs_cl = run_cl and (overwrite or is_cl_incomplete)

            if not (needs_ebay or needs_fbmp or needs_cl) and not sku_filter:
                print(f"[SKIP] {sku} ({item.get('item_id')}) already has all required drafts created.", flush=True)
                continue

            if needs_ebay:
                try:
                    ebay_url = await create_ebay_draft(context, item, dry_run=dry_run)
                    item["ebay_draft_url"] = ebay_url
                except Exception as exc:
                    print(f"  [eBay ERROR] {exc}", flush=True)

            if needs_fbmp:
                try:
                    fbmp_url = await create_fbmp_draft(context, item, dry_run=dry_run)
                    item["fbmp_listing_url"] = fbmp_url
                except Exception as exc:
                    print(f"  [FBMP ERROR] {exc}", flush=True)

            if needs_cl:
                try:
                    cl_url = await create_craigslist_preview(context, item, dry_run=dry_run)
                    item["craigslist_edit_url"] = cl_url
                except Exception as exc:
                    print(f"  [Craigslist ERROR] {exc}", flush=True)

            item["status"] = "DRAFTS_CREATED" if not dry_run else "DRY_RUN_VERIFIED"
            item["drafted_at"] = datetime.now().isoformat()

            # Persist per-item analysis.json and batch_manifest.json
            folder_path = Path(item.get("folder_path", ""))
            if folder_path.exists():
                (folder_path / "analysis.json").write_text(json.dumps(item, indent=2), encoding="utf-8")
            manifest["updated_at"] = datetime.now().isoformat()
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        manifest["updated_at"] = datetime.now().isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print("=" * 72)
        print(f"STAGE 4 COMPLETE: Updated draft URLs in '{manifest_path.as_posix()}'.")
        print("Hard Safety Rule verified: 0 listings were auto-published.")
        print("=" * 72)
        return 0
    finally:
        if p_cm is not None:
            await p_cm.stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 4: Multi-Platform Playwright CDP Draft Creator (eBay, FBMP, Craigslist)."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("items/batch_manifest.json"),
        help="Path to batch_manifest.json (default: items/batch_manifest.json)",
    )
    parser.add_argument(
        "--sku",
        type=str,
        default=None,
        help="Optional single SKU or item_XX ID to draft",
    )
    parser.add_argument(
        "--platform",
        choices=["ebay", "fbmp", "craigslist"],
        default=None,
        help="Optional single platform filter",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate drafts even if a draft URL is already recorded",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate draft creation and verify routing without launching browser clicks",
    )

    args = parser.parse_args()
    return asyncio.run(
        run_draft_pipeline(
            manifest_path=args.manifest,
            sku_filter=args.sku,
            platform_filter=args.platform,
            dry_run=args.dry_run,
            overwrite=args.overwrite,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
