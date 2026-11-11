#!/usr/bin/env python3
"""
Stage 5: Google Sheets State & Cross-Post Tracker (scripts/sync_sheets.py)

Uses `gspread` to log and upsert every processed item into a master Google Sheet with columns:
  SKU | Item Title | Routing Bucket | Est Net Profit | Status | eBay Draft/Item URL | FBMP Listing URL | Craigslist Edit URL | Local Folder Path

Also maintains a local CSV mirror at `items/master_inventory.csv` so inventory state and URLs
are always preserved even when offline or before Google Sheets credentials are configured.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


SHEET_HEADERS = [
    "SKU",
    "Item Title",
    "Routing Bucket",
    "Est Net Profit",
    "Status",
    "eBay Draft/Item URL",
    "FBMP Listing URL",
    "Craigslist Edit URL",
    "Local Folder Path",
]


def item_to_row(item: Dict[str, Any]) -> List[str]:
    """Convert a manifest item dictionary into the 9-column Google Sheet row."""
    net_profit = float(item.get("estimated_net_profit_usd", 0.0))
    return [
        str(item.get("sku") or ""),
        str(item.get("title") or ""),
        str(item.get("routing_bucket") or ""),
        f"${net_profit:.2f}",
        str(item.get("status") or "PENDING_REVIEW"),
        str(item.get("ebay_draft_url") or ""),
        str(item.get("fbmp_listing_url") or ""),
        str(item.get("craigslist_edit_url") or ""),
        str(item.get("folder_path") or ""),
    ]


def sync_local_csv(csv_path: Path, rows: List[List[str]]) -> None:
    """Upsert rows by SKU into the local master_inventory.csv mirror."""
    existing_by_sku: Dict[str, List[str]] = {}
    sku_order: List[str] = []

    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, None)
                for row in reader:
                    if row and len(row) >= 1:
                        sku = row[0]
                        existing_by_sku[sku] = row
                        if sku not in sku_order:
                            sku_order.append(sku)
        except Exception:
            pass

    for row in rows:
        sku = row[0]
        if not sku:
            continue
        existing_by_sku[sku] = row
        if sku not in sku_order:
            sku_order.append(sku)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(SHEET_HEADERS)
        for sku in sku_order:
            writer.writerow(existing_by_sku[sku])

    print(f"[OK] Local CSV mirror synced: '{csv_path.as_posix()}' ({len(sku_order)} total SKU(s)).")


def sync_google_sheet(rows: List[List[str]]) -> bool:
    """Upsert rows by SKU into the configured Google Sheet via gspread."""
    creds_path = Path(os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "./credentials/service_account.json"))
    sheet_id = os.getenv("GOOGLE_SHEET_ID", "").strip()
    worksheet_name = os.getenv("GOOGLE_WORKSHEET_NAME", "Listings").strip() or "Listings"

    if not creds_path.exists():
        print(
            f"[INFO] Google Service Account file not found at '{creds_path.as_posix()}'.\n"
            f"       Local CSV mirror is up-to-date. Place your key at '{creds_path.as_posix()}' "
            f"to enable live Google Sheets sync (see credentials/README.md)."
        )
        return False

    if not sheet_id or sheet_id == "YOUR_GOOGLE_SHEET_ID_HERE":
        print(
            "[INFO] GOOGLE_SHEET_ID is not yet set in .env.\n"
            "       Local CSV mirror is up-to-date. Add GOOGLE_SHEET_ID to .env to sync with Google Sheets."
        )
        return False

    try:
        import gspread
    except ImportError:
        print("[WARN] gspread is not installed. Run pip install -r requirements.txt")
        return False

    try:
        gc = gspread.service_account(filename=str(creds_path))
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(worksheet_name)
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title=worksheet_name, rows=500, cols=len(SHEET_HEADERS))

        all_values = ws.get_all_values()
        if not all_values:
            ws.append_row(SHEET_HEADERS, value_input_option="USER_ENTERED")
            all_values = [SHEET_HEADERS]
        elif all_values[0] != SHEET_HEADERS:
            ws.update(range_name="A1:I1", values=[SHEET_HEADERS])

        sku_to_row_idx: Dict[str, int] = {}
        for idx, existing_row in enumerate(all_values[1:], start=2):
            if existing_row and existing_row[0]:
                sku_to_row_idx[existing_row[0]] = idx

        updated_count = 0
        appended_count = 0
        for row in rows:
            sku = row[0]
            if sku in sku_to_row_idx:
                row_idx = sku_to_row_idx[sku]
                ws.update(
                    range_name=f"A{row_idx}:I{row_idx}",
                    values=[row],
                    value_input_option="USER_ENTERED",
                )
                updated_count += 1
            else:
                ws.append_row(row, value_input_option="USER_ENTERED")
                appended_count += 1

        print(
            f"[OK] Google Sheet '{sh.title}' ({worksheet_name}) synced: "
            f"{appended_count} added, {updated_count} updated."
        )
        return True
    except Exception as exc:
        print(f"[WARN] Google Sheets sync encountered an error: {exc}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage 5: Sync batch_manifest.json to Google Sheets and items/master_inventory.csv."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("items/batch_manifest.json"),
        help="Path to batch_manifest.json (default: items/batch_manifest.json)",
    )
    parser.add_argument(
        "--csv-mirror",
        type=Path,
        default=Path("items/master_inventory.csv"),
        help="Path to local CSV mirror (default: items/master_inventory.csv)",
    )

    args = parser.parse_args()
    if not args.manifest.exists():
        print(f"[ERROR] Manifest not found at '{args.manifest.as_posix()}'. Run Stage 2/3 first.")
        return 1

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    items: List[Dict[str, Any]] = manifest.get("items", [])
    rows = [item_to_row(item) for item in items]

    sync_local_csv(args.csv_mirror, rows)
    sync_google_sheet(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
