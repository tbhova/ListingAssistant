#!/usr/bin/env python3
"""
Stage 5 Helper: FBMP Sold Order -> Pirate Ship Label Drafter & Rate Comparator
(scripts/ship_pirateship.py)

Workflow:
  1. Extracts buyer Name + Shipping Address from Facebook Marketplace (`View Order` on
     `https://www.facebook.com/marketplace/you/selling`) for a given `--item` title
     (or accepts `--name` / `--address` directly).
  2. Connects to Chrome CDP (`http://localhost:9222`) and opens `https://ship.pirateship.com/ship`.
     If redirected to `/login`, leaves the tab open and exits with code 2 so you can log in once.
  3. Fills recipient address, package dimensions (`L x W x H`), and weight (`lbs`, `oz`).
     - MEDIA MAIL GUARD: Only enables USPS Media Mail if `--is-book` is explicitly passed.
  4. Clicks "Get Rates", scrapes USPS vs. UPS/other carrier rates, compares cheapest USPS
     (which includes free pickup) against any cheaper non-USPS option, and STOPS before
     purchasing so you can confirm the carrier and price.

HARD SAFETY RULE:
  This script NEVER clicks "Buy Label" / "Pay" unless `--confirm-buy` AND `--service "<name>"`
  are explicitly passed after human approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def parse_address_block(raw_lines: List[str], has_name_first: bool = False) -> Dict[str, str]:
    """Parse multi-line US shipping address into buyer_name, street, street2, city, state, zip."""
    cleaned = [ln.strip() for ln in raw_lines if ln.strip() and ln.strip().lower() != "copy"]
    buyer_name = ""
    if has_name_first and len(cleaned) >= 3:
        buyer_name = cleaned[0]
        cleaned = cleaned[1:]

    street = cleaned[0] if len(cleaned) >= 1 else ""
    street2 = cleaned[1] if len(cleaned) >= 3 else ""
    city, state, zip_code = "", "", ""
    if len(cleaned) >= 2:
        last_line = cleaned[-1]
        m = re.match(r"^(.+?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", last_line)
        if m:
            city, state, zip_code = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        else:
            city = last_line
    return {
        "buyer_name": buyer_name,
        "street": street,
        "street2": street2,
        "city": city,
        "state": state,
        "zip": zip_code,
        "full_address": "\n".join(([buyer_name] if buyer_name else []) + cleaned),
    }


async def extract_fbmp_order_address(page: Any, item_query: str) -> Dict[str, str]:
    """Open FBMP /selling, find matching sold order, click View Order, and extract Buyer + Address."""
    await page.goto("https://www.facebook.com/marketplace/you/selling", wait_until="domcontentloaded")
    await page.wait_for_timeout(4500)

    # Locate the card matching item_query and click its "View Order" button
    cards = page.locator(f"[role='main'] div:has-text('{item_query}'):has([role='button']:has-text('View Order'))")
    if await cards.count() > 0:
        view_btn = cards.last.locator("[role='button']:has-text('View Order'), a:has-text('View Order')").first
    else:
        view_btn = page.locator("[role='main'] [role='button']:has-text('View Order'), [role='main'] a:has-text('View Order')").first

    if await view_btn.count() == 0:
        raise RuntimeError(f"Could not find a 'View Order' button matching '{item_query}' on FBMP /selling.")

    await view_btn.click()
    await page.wait_for_timeout(3000)

    dlg = page.locator("[role='dialog']").last
    modal_text = await dlg.inner_text() if await dlg.count() > 0 else await page.locator("[role='main']").first.inner_text()

    lines = [ln.strip() for ln in modal_text.splitlines() if ln.strip()]
    buyer_name = ""
    addr_lines: List[str] = []
    order_id = ""

    for idx, ln in enumerate(lines):
        low = ln.lower()
        if low == "buyer information" and idx + 1 < len(lines):
            buyer_name = lines[idx + 1]
        elif low == "shipping address":
            for offset in range(1, 7):
                if idx + offset >= len(lines):
                    break
                nxt = lines[idx + offset]
                nxt_low = nxt.lower()
                if nxt_low == "copy":
                    continue
                if any(
                    nxt_low.startswith(stop)
                    for stop in ("tracking number", "order status", "items", "order summary", "when you'll get paid")
                ):
                    break
                addr_lines.append(nxt)
        elif low.startswith("order #"):
            order_id = ln.replace("Order #", "").replace("Copy", "").strip()

    parsed = parse_address_block(addr_lines, has_name_first=True)
    if buyer_name and not parsed.get("buyer_name"):
        parsed["buyer_name"] = buyer_name
    parsed["order_id"] = order_id
    return parsed


async def draft_pirateship_label(
    context: Any,
    recipient: Dict[str, str],
    weight_lbs: int,
    weight_oz: float,
    dims_in: List[float],
    is_book: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """
    Open Pirate Ship (`/ship`), fill recipient + package dims/weight (Media Mail ONLY if is_book=True),
    click 'Get Rates', and return parsed USPS vs. UPS/other rates without purchasing.
    """
    if dry_run:
        return {
            "status": "DRY_RUN",
            "recipient": recipient,
            "rates": [],
        }

    page = await context.new_page()
    await page.goto("https://ship.pirateship.com/ship/single", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    page_title = (await page.title() or "").lower()
    if "/login" in page.url.lower() or "log in" in page_title or "sign in" in page_title:
        print("[LOGIN REQUIRED] Pirate Ship redirected to login.", flush=True)
        print("                 Please log in once in the open Chrome window, then re-run this command.", flush=True)
        return {"status": "LOGIN_REQUIRED", "url": page.url}

    # Dismiss cookie banner if present
    try:
        cookie_btn = page.locator("button[aria-label='Close Cookie Banner'], button:has-text('Reject Cookies')").first
        if await cookie_btn.count() > 0 and await cookie_btn.is_visible():
            await cookie_btn.click()
            await page.wait_for_timeout(300)
    except Exception:
        pass

    # Fill recipient address directly using Pirate Ship's exact field names
    field_map = [
        ("input[name='shipToAddress.fullName']", recipient.get("buyer_name", "")),
        ("input[name='shipToAddress.address1']", recipient.get("street", "")),
        ("input[name='shipToAddress.address2']", recipient.get("street2", "")),
        ("input[name='shipToAddress.city']", recipient.get("city", "")),
        ("input[name='shipToAddress.regionCode']", recipient.get("state", "")),
        ("input[name='shipToAddress.postcode']", recipient.get("zip", "")),
    ]
    for sel, val in field_map:
        if not val:
            continue
        loc = page.locator(sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(val)

    # Package dimensions (L x W x H) and weight (lbs, oz)
    l_in, w_in, h_in = [str(round(float(d), 1)) for d in dims_in[:3]]
    pkg_fields = [
        ("input[name='packagePreset.packageDetails.package.dimensionX']", l_in),
        ("input[name='packagePreset.packageDetails.package.dimensionY']", w_in),
        ("input[name='packagePreset.packageDetails.package.dimensionZ']", h_in),
        ("input[name='packagePreset.packageDetails.package.weightPounds']", str(weight_lbs)),
        ("input[name='packagePreset.packageDetails.package.weightOunces']", str(round(weight_oz, 1))),
    ]
    for sel, val in pkg_fields:
        loc = page.locator(sel).first
        if await loc.count() > 0 and await loc.is_visible():
            await loc.fill(val)

    # Media Mail checkbox: STRICTLY ONLY open Extra Services and check Media Mail if is_book is True
    if is_book:
        try:
            es_btn = page.locator("button[aria-label='open Extra Services']").first
            if await es_btn.count() > 0 and await es_btn.is_visible():
                await es_btn.click()
                await page.wait_for_timeout(800)
            mm_chk = page.locator("label:has-text('Media Mail') input[type='checkbox'], input[name*='media']").first
            if await mm_chk.count() > 0 and not await mm_chk.is_checked():
                await mm_chk.check()
                print("  [Pirate Ship] Enabled USPS Media Mail (item flagged as Book).", flush=True)
        except Exception as exc:
            print(f"  [Pirate Ship] Media Mail toggle note: {exc}", flush=True)

    # Click "Get Rates" — NEVER click "Buy Label"
    get_rates_btn = page.locator("button[type='submit']:has-text('Get Rates'), button:has-text('Get Rates')").first
    if await get_rates_btn.count() > 0 and await get_rates_btn.is_visible():
        await get_rates_btn.click()
        await page.wait_for_timeout(3500)

    # Handle Pirate Ship "Validated address available" modal -> click "Continue" (uses USPS validated address)
    try:
        val_continue = page.locator("button:has-text('Continue')").first
        if await val_continue.count() > 0 and await val_continue.is_visible():
            val_text = (await page.locator("[role='dialog'], body").first.inner_text() or "")
            if "validated address" in val_text.lower():
                await val_continue.click()
                print("  [Pirate Ship] Accepted USPS validated address ('Continue').", flush=True)
                await page.wait_for_timeout(4500)
    except Exception:
        pass

    # Scrape returned rates from Pirate Ship rates screen
    rates_text = (await page.locator("body").inner_text())[:5000]
    rate_matches = re.findall(
        r"((?:USPS|UPS)\s+[A-Za-z0-9\s\-\.\®]+?)\s+\$(\d+\.\d{2})",
        rates_text,
    )
    parsed_rates: List[Dict[str, Any]] = []
    seen_services: set[str] = set()
    for svc, price_str in rate_matches:
        svc_clean = " ".join(svc.split())
        if len(svc_clean) > 60 or svc_clean in seen_services:
            continue
        seen_services.add(svc_clean)
        parsed_rates.append({"service": svc_clean, "price_usd": float(price_str)})

    # Park on the rates page (do NOT close so user can review, and do NOT click Buy)
    print(f"  [Pirate Ship] Parked on Rates page (NOT purchased): {page.url}", flush=True)
    return {
        "status": "RATES_READY",
        "url": page.url,
        "recipient": recipient,
        "rates": parsed_rates,
    }


def summarize_rate_comparison(rates: List[Dict[str, Any]]) -> None:
    """Compare cheapest USPS (free pickup) vs. cheapest non-USPS rate."""
    if not rates:
        print("[INFO] No rates parsed automatically — review the open Pirate Ship tab.")
        return

    usps_rates = sorted([r for r in rates if "USPS" in r["service"].upper()], key=lambda x: x["price_usd"])
    other_rates = sorted([r for r in rates if "USPS" not in r["service"].upper()], key=lambda x: x["price_usd"])

    print("\n--- PIRATE SHIP RATE COMPARISON ---")
    for r in sorted(rates, key=lambda x: x["price_usd"])[:8]:
        print(f"  - {r['service']:42s} : ${r['price_usd']:.2f}")

    if usps_rates and other_rates:
        best_usps = usps_rates[0]
        best_other = other_rates[0]
        if best_other["price_usd"] < best_usps["price_usd"]:
            diff = round(best_usps["price_usd"] - best_other["price_usd"], 2)
            print(
                f"\n[DECISION NEEDED] {best_other['service']} (${best_other['price_usd']:.2f}) is "
                f"${diff:.2f} cheaper than {best_usps['service']} (${best_usps['price_usd']:.2f}, includes free USPS pickup). "
                f"Ask the user which carrier they prefer before purchasing!"
            )
        else:
            print(
                f"\n[RECOMMENDED] {best_usps['service']} (${best_usps['price_usd']:.2f}) is both cheapest "
                f"and includes free USPS pickup."
            )


async def run_pirateship_flow(args: argparse.Namespace) -> int:
    from playwright.async_api import async_playwright

    cdp_url = os.getenv("CHROME_CDP_URL", "http://localhost:9222")
    p_cm = await async_playwright().start()
    try:
        browser = await p_cm.chromium.connect_over_cdp(cdp_url)
    except Exception as exc:
        await p_cm.stop()
        print(f"[ERROR] Chrome CDP is not running on {cdp_url}: {exc}")
        return 1

    context = browser.contexts[0] if browser.contexts else await browser.new_context()

    try:
        if args.address:
            recipient = parse_address_block(args.address.splitlines())
            recipient["buyer_name"] = args.buyer_name or ""
        elif args.item:
            fb_page = await context.new_page()
            recipient = await extract_fbmp_order_address(fb_page, args.item)
            await fb_page.close()
        else:
            print("[ERROR] Provide either --item '<listing title>' (to extract from FBMP) or --address '<full address>'.")
            return 1

        print(f"[RECIPIENT] {recipient.get('buyer_name')} | {recipient.get('street')}, {recipient.get('city')}, {recipient.get('state')} {recipient.get('zip')}")

        result = await draft_pirateship_label(
            context=context,
            recipient=recipient,
            weight_lbs=args.lbs,
            weight_oz=args.oz,
            dims_in=[args.length, args.width, args.height],
            is_book=args.is_book,
            dry_run=args.dry_run,
        )

        if result["status"] == "LOGIN_REQUIRED":
            return 2

        summarize_rate_comparison(result.get("rates", []))
        return 0
    finally:
        await p_cm.stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract FBMP sold order address and draft a Pirate Ship label (never auto-buys)."
    )
    parser.add_argument("--item", type=str, default=None, help="Sold FBMP item title substring to match in View Order")
    parser.add_argument("--buyer-name", type=str, default=None, help="Optional buyer name if passing --address directly")
    parser.add_argument("--address", type=str, default=None, help="Optional multi-line address instead of scraping FBMP")
    parser.add_argument("--lbs", type=int, required=True, help="Boxed weight (pounds)")
    parser.add_argument("--oz", type=float, default=0.0, help="Boxed weight (ounces, default: 0)")
    parser.add_argument("--length", type=float, required=True, help="Box length (inches)")
    parser.add_argument("--width", type=float, required=True, help="Box width (inches)")
    parser.add_argument("--height", type=float, required=True, help="Box height (inches)")
    parser.add_argument("--is-book", action="store_true", help="Enable USPS Media Mail option (ONLY use for books)")
    parser.add_argument("--dry-run", action="store_true", help="Extract address and simulate without opening Pirate Ship")

    args = parser.parse_args()
    return asyncio.run(run_pirateship_flow(args))


if __name__ == "__main__":
    sys.exit(main())
