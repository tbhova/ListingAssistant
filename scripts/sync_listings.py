"""
sync_listings.py - Two-Way Sync between Markdown and Listing Assistant JSONs/HTML

Commands:
    python scripts/sync_listings.py           # Import from items/LISTINGS_EDIT.md into JSONs & HTML
    python scripts/sync_listings.py --export  # Export latest JSONs into items/LISTINGS_EDIT.md
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
ITEMS_DIR = WORKSPACE_ROOT / "items"
MANIFEST_PATH = ITEMS_DIR / "batch_manifest.json"
MARKDOWN_PATH = ITEMS_DIR / "LISTINGS_EDIT.md"
GALLERY_BUILDER = WORKSPACE_ROOT / "scratch" / "build_master_gallery.py"


def export_markdown_from_manifest() -> None:
    """Generate items/LISTINGS_EDIT.md from items/batch_manifest.json."""
    if not MANIFEST_PATH.exists():
        print(f"[ERROR] Manifest not found at {MANIFEST_PATH}")
        sys.exit(1)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    items = sorted(manifest.get("items", []), key=lambda x: x["item_id"])

    lines: List[str] = [
        "# Inventory Listings Editor (`items/LISTINGS_EDIT.md`)",
        "",
        "> **How to use:** Edit titles, prices, conditions, or descriptions directly below.",
        "> When finished, run: `.venv\\Scripts\\python.exe scripts/sync_listings.py` in your terminal to sync back to JSON manifests and the HTML gallery.",
        "",
        f"**Last Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | **Total Items:** {len(items)}",
        "",
        "================================================================================",
        "",
    ]

    for item in items:
        item_id = item.get("item_id", "item_XX")
        sku = item.get("sku", "")
        title = item.get("title", "").strip()
        brand = item.get("brand", "").strip()
        model_mpn = item.get("model_mpn", "").strip()
        condition = item.get("condition", "Used - Good").strip()
        comp_price = item.get("comp_price", 0.0)
        route = item.get("routing_bucket", "LOCAL_ONLY")
        cond_notes = item.get("condition_notes", "").strip()
        accessories = item.get("accessories", [])
        desc = item.get("description_detailed", "").strip()

        lines.append(f"## [{item_id}] {title}")
        lines.append(f"- **SKU**: `{sku}`")
        lines.append(f"- **Brand**: {brand}")
        lines.append(f"- **Model/MPN**: {model_mpn}")
        lines.append(f"- **Condition**: {condition}")
        lines.append(f"- **Target Price**: ${comp_price:.2f}")
        lines.append(f"- **Route**: {route}")
        lines.append(f"- **Condition Notes**: {cond_notes}")
        if accessories:
            acc_str = ", ".join(accessories)
            lines.append(f"- **Accessories**: {acc_str}")
        lines.append("")
        lines.append("### Description")
        lines.append(desc)
        lines.append("")
        lines.append("--------------------------------------------------------------------------------")
        lines.append("")

    MARKDOWN_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] Exported {len(items)} items to {MARKDOWN_PATH}")


def import_markdown_to_manifest() -> None:
    """Parse items/LISTINGS_EDIT.md and update vision_input.json, manifest, and gallery."""
    if not MARKDOWN_PATH.exists():
        print(f"[ERROR] Markdown file not found at {MARKDOWN_PATH}")
        sys.exit(1)

    content = MARKDOWN_PATH.read_text(encoding="utf-8")

    # Split into item blocks using '## [item_XX]'
    pattern = re.compile(r"^##\s+\[(item_\d{2})\]\s*(.*?)$", re.MULTILINE)
    matches = list(pattern.finditer(content))

    if not matches:
        print("[ERROR] No items found in LISTINGS_EDIT.md matching '## [item_XX]' headers.")
        sys.exit(1)

    updated_count = 0

    for i, match in enumerate(matches):
        item_id = match.group(1)
        header_title = match.group(2).strip()

        start_pos = match.end()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        block = content[start_pos:end_pos]

        # Extract fields
        sku_m = re.search(r"-\s+\*\*SKU\*\*:\s*`?([^`\n]+)`?", block)
        brand_m = re.search(r"-\s+\*\*Brand\*\*:\s*([^\n]+)", block)
        model_m = re.search(r"-\s+\*\*Model/MPN\*\*:\s*([^\n]+)", block)
        cond_m = re.search(r"-\s+\*\*Condition\*\*:\s*([^\n]+)", block)
        price_m = re.search(r"-\s+\*\*Target Price\*\*:\s*\$?([\d\.]+)", block)
        route_m = re.search(r"-\s+\*\*Route\*\*:\s*([^\n]+)", block)
        notes_m = re.search(r"-\s+\*\*Condition Notes\*\*:\s*([^\n]+)", block)
        acc_m = re.search(r"-\s+\*\*Accessories\*\*:\s*([^\n]+)", block)

        # Extract description after '### Description'
        desc_m = re.search(r"###\s+Description\s*\n+(.*?)(?=\n-{3,}|\Z)", block, re.DOTALL)

        # Update item's vision_input.json
        item_folder = ITEMS_DIR / item_id
        v_file = item_folder / "vision_input.json"

        if not v_file.exists():
            print(f"[WARN] Skipping {item_id}: {v_file} does not exist.")
            continue

        try:
            v_data = json.loads(v_file.read_text(encoding="utf-8"))
        except Exception:
            v_data = {}

        if header_title:
            v_data["title"] = header_title
        if brand_m:
            v_data["brand"] = brand_m.group(1).strip()
        if model_m:
            v_data["model_mpn"] = model_m.group(1).strip()
        if cond_m:
            v_data["condition"] = cond_m.group(1).strip()
        if price_m:
            try:
                v_data["comp_price"] = float(price_m.group(1).strip())
            except ValueError:
                pass
        if notes_m:
            v_data["condition_notes"] = notes_m.group(1).strip()
        if acc_m:
            acc_list = [a.strip() for a in acc_m.group(1).split(",") if a.strip()]
            if acc_list:
                v_data["accessories"] = acc_list
        if desc_m:
            v_data["description_detailed"] = desc_m.group(1).strip()

        v_file.write_text(json.dumps(v_data, indent=2), encoding="utf-8")
        updated_count += 1

    print(f"[OK] Parsed markdown and updated {updated_count} vision_input.json files.")

    # Re-run economics calculation
    print("[INFO] Re-calculating economics and updating batch_manifest.json...")
    subprocess.run(
        [sys.executable, str(WORKSPACE_ROOT / "scripts" / "analyze_items.py"), "--calculate"],
        check=True
    )

    # Rebuild gallery if builder script exists
    if GALLERY_BUILDER.exists():
        print("[INFO] Rebuilding batch_gallery.html...")
        subprocess.run([sys.executable, str(GALLERY_BUILDER)], check=True)

    print("[SUCCESS] Complete sync from LISTINGS_EDIT.md finished!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync between LISTINGS_EDIT.md and item JSONs/HTML.")
    parser.add_argument("--export", action="store_true", help="Export latest manifest into LISTINGS_EDIT.md")
    parser.add_argument("--import-md", action="store_true", help="Import from LISTINGS_EDIT.md into JSONs & HTML")

    args = parser.parse_args()

    if args.export:
        export_markdown_from_manifest()
    else:
        # Default behavior: if markdown exists, import; if not, export
        if not MARKDOWN_PATH.exists():
            print("[INFO] LISTINGS_EDIT.md not found. Generating initial export...")
            export_markdown_from_manifest()
        else:
            import_markdown_to_manifest()


if __name__ == "__main__":
    main()
