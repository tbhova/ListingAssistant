---
name: resale-lister
description: >-
  End-to-end Resale Listing Pipeline for eBay, Facebook Marketplace, and Craigslist.
  Automatically triggers whenever the user drops or mentions new photos in /raw_photos,
  asks to start or process listings, mentions inventory batches, resale items, shipping/comp
  calculations, cross-posting drafts, or tearing down sold SKUs.
allowed-tools:
  - Read
  - Write
  - Bash(python scripts/group_photos.py*)
  - Bash(python scripts/analyze_items.py*)
  - Bash(python scripts/sync_sheets.py*)
  - Bash(.venv/Scripts/python.exe scripts/group_photos.py*)
  - Bash(.venv/Scripts/python.exe scripts/analyze_items.py*)
  - Bash(.venv/Scripts/python.exe scripts/sync_sheets.py*)
---

# Resale Listing Pipeline (`resale-lister`)

This skill orchestrates the 5-stage Resale Listing Pipeline in this workspace. Follow each stage in order and **strictly enforce the Stage 3 human-in-the-loop pause** before executing any Playwright browser automation.

---

## Tooling & Environment Conventions

- Prefer running Python scripts via `.venv\Scripts\python.exe` on Windows (or `python` if `.venv` is active).
- Configuration lives in `.env` at the workspace root (`ORIGIN_ZIP`, `CRAIGSLIST_SUBDOMAIN`, fee/profit thresholds, `CHROME_CDP_URL`, `GOOGLE_SHEET_ID`).
- **Pre-Approved Safe Local Scripts:**
  - `scripts/group_photos.py`
  - `scripts/analyze_items.py`
  - `scripts/sync_sheets.py`
- **Human-Gated Browser Scripts (Require Explicit User Approval After Stage 3):**
  - `scripts/launch_chrome.py`
  - `scripts/create_drafts.py`
  - `scripts/teardown_sold.py`

---

## Stage 1: Photo Slicing, Orientation & Visual Discontinuity Audit (`scripts/group_photos.py`)

1. Run the photo grouping script:
   ```powershell
   .venv\Scripts\python.exe scripts/group_photos.py
   ```
   *(Optional flags: `--threshold 45` to adjust the EXIF `DateTimeOriginal` gap in seconds; `--clean` to reset `/items/` before staging).*
2. Inspect the generated `/items/grouping_report.json`.

3. **EXIF Normalization & Orientation Auto-Correction:**
   - Raw phone photos (Pixel, iPhone, Galaxy) require `ImageOps.exif_transpose()` during ingestion to physicalize EXIF rotation.
   - **Overhead / Tabletop Phone Accelerometer Drift:** When a user points a phone straight down at items on a table or carpet, gravity points along the lens axis, causing phone gyro sensors to falsely tag photos as landscape (Orientation 1).
   - **Visual Orientation Check:** During visual inspection, check label and packaging text orientation. If text reads vertically or upside-down, rotate the image on disk (90°, 180°, or 270°) and re-save so eBay, FBMP, and Craigslist drafts receive 100% upright images.

4. **Timestamp Thresholds vs. Visual Discontinuity (The 25s–90s Lesson):**
   - **Why pure timestamp reduction fails:** Setting the threshold too short (e.g., <30s) shatters complex multi-angle mechanical equipment, shoes, or furniture into false separate items (pauses of 45–90s are normal while rotating a table saw, adjusting chair tilt, or checking shoe soles). Yet rapid tabletop swaps of small tools often occur in 25–32s, causing false merges under a 45–60s threshold.
   - **Visual Discontinuity / Object Change Rule:** Grouping must combine time thresholds with visual object checks:
     - If the background/surface remains constant but the primary subject, color palette, or tool packaging abruptly changes between consecutive photos, trigger an **Item Boundary Event** even if $\Delta t < 30\text{s}$.
     - If consecutive photos separated by 60–90s share the same physical machinery or setting, classify as a **Continuation Shot** and merge.

5. **Outlier & Split Verification Pass:**
   - For folders with single photos, or folders containing multiple distinct tools photographed in rapid succession, split them into dedicated item directories:
     ```powershell
     .venv\Scripts\python.exe scripts/group_photos.py --split item_XX <photo_index>
     .venv\Scripts\python.exe scripts/group_photos.py --merge item_XX item_YY
     ```
   - Re-index item folders sequentially (`item_01` through `item_NN`).

---

## Stage 2: Vision Extraction, Shipping Math & Routing (`scripts/analyze_items.py`)

1. Initialize or inspect the item folders:
   ```powershell
   .venv\Scripts\python.exe scripts/analyze_items.py --init
   ```
2. **Multimodal Vision Inspection (Agent-Native Default, $0 API Cost):**
   - For each `/items/item_XX/` folder, view its photos using `read_file`.
   - Extract and populate `/items/item_XX/vision_input.json` (or update `/items/vision_batch.json`) with:
     - `brand`: Manufacturer/brand name (or `"Unknown"` / `"Unbranded"`).
     - `model_mpn`: Model number, MPN, or part number read from labels/markings.
     - `title`: SEO-optimized 80-char max eBay/Marketplace title (`Brand + Model + Key Spec + Condition`).
     - `category`: General category (e.g., `"Electronics"`, `"Audio"`, `"Tools"`, `"Home & Garden"`).
     - `condition`: One of `"New"`, `"Open Box"`, `"Used - Like New"`, `"Used - Good"`, `"Used - Fair"`, `"For Parts or Not Working"`.
     - `condition_notes`: Specific cosmetic notes observed in photos (scratches, dents, clean faceplate, etc.).
     - `accessories`: List of included cables, remotes, manuals, batteries, or accessories visible in photos.
     - `weight_lbs`: Estimated packaged shipping weight in pounds (float).
     - `dimensions_in`: Estimated packaged box dimensions `[Length, Width, Height]` in inches.
     - `comp_price`: Estimated eBay sold comparable price in USD (float).
     - `uncertainty_score`: Integer `0–100` indicating how uncertain you are about model ID, working condition, completeness, or comp value.
     - `grill_questions`: Array of specific clarifying questions for the user (e.g., `"Photo 3 shows the back label slightly blurred—what is the exact serial/model suffix?"`, `"Does this unit power on and pass audio testing?"`).
     - `description_detailed`: Rich, structured draft description for high-priority/high-value items.
     - `description_concise`: Clean, concise 2–3 sentence description for low-priority items.
   - *(Fallback mode: If the user prefers standalone API extraction and `GEMINI_API_KEY` is configured in `.env`, run `.venv\Scripts\python.exe scripts/analyze_items.py --use-api`).*
3. **Run Shipping Math, Profit Calculation & Routing:**
   ```powershell
   .venv\Scripts\python.exe scripts/analyze_items.py --calculate
   ```
   This script computes:
   - **Conservative Zone 8 Shipping** from `ORIGIN_ZIP` (accounting for both actual weight and USPS/UPS dimensional weight `(L*W*H)/166` & `/139`).
   - **eBay Fees:** `(Comp Price * 13.5%) + $0.40`.
   - **Estimated Net Profit:** `Comp Price - Est. Zone 8 Shipping - eBay Fees`.
   - **FBMP Price with Negotiation Buffer:** `Comp Price * (1 + FBMP_NEGOTIATION_BUFFER)` rounded cleanly.
   - **Routing Buckets:**
     1. `LOCAL_ONLY` (Craigslist + FB Marketplace): Heavy/bulky items (`weight > 20 lbs`, `longest side > 24"`, or `shipping > 40% of comp`) where `Comp Price >= $20`.
     2. `ALL_THREE` (eBay + CL + FBMP): High-value items (`Est. Net Profit >= $50`) worth shipping and cross-posting locally.
     3. `EBAY_ONLY`: Smaller, medium-value items (`$15 <= Est. Net Profit < $50`) not worth local pickup scheduling friction.
     4. `DONATE_OR_LOT`: Items where `Est. Net Profit < $15` (automatically generates logical bundle suggestions when multiple low-value items exist).

---

## Stage 3: Priority Scoring & "Grill Me" Review Queue

1. `scripts/analyze_items.py --calculate` automatically computes the **Priority Score (0–100)** for each item:
   $$\text{Priority} = (\text{Uncertainty\_Score} \times 0.6) + (\text{Normalized\_Value\_Score} \times 0.4)$$
   and outputs `/items/review_queue.md` and `/items/batch_manifest.json` sorted from **highest Priority Score to lowest**.
2. Present the interactive Markdown review table and item cards directly in chat:
   - **High-Priority / High-Value Items:** Present full draft descriptions and **explicitly grill the user** with the clarifying questions (unreadable serials, untested functionality, wear details).
   - **Low-Priority / Low-Value Items (bottom of queue):** Present clean, concise descriptions ready for one-click approval, plus any suggested bundles for `DONATE_OR_LOT` items.
3. **CRITICAL HARD STOP:**
   - **HALT EXECUTION HERE.**
   - Do **NOT** launch Chrome or run `scripts/create_drafts.py` until the user replies to your questions and approves the titles, pricing, and descriptions.
   - Once the user replies with answers or edits, update `/items/batch_manifest.json` (and re-run `scripts/analyze_items.py --calculate` if prices/weights changed).

---

## Stage 4: Multi-Platform Playwright Drafts (`scripts/create_drafts.py`)

Only execute Stage 4 **after** the user explicitly approves the review queue from Stage 3.

1. Ensure the dedicated Chrome automation profile is running on port `9222`:
   ```powershell
   .venv\Scripts\python.exe scripts/launch_chrome.py --check-or-launch
   ```
   *(If this is the user's first run in `./chrome_profile`, let them know they can log into eBay, Facebook, and Craigslist once in that Chrome window; sessions persist across all future runs).*
2. Run the Playwright CDP draft generator:
   ```powershell
   .venv\Scripts\python.exe scripts/create_drafts.py
   ```
   *(Supports `--sku <SKU>` to run a single item, `--platform ebay|fbmp|craigslist`, or `--dry-run` to verify selectors without modifying forms).*
3. **Hard Safety Rule:** `scripts/create_drafts.py` is hard-coded to **NEVER** click final `"Publish"`, `"List Item"`, or `"List It"` buttons:
   - **eBay:** Uploads photos, fills title, item specifics, description, price, and shipping, and clicks **"Save for later" / "Save as draft"**.
   - **FB Marketplace:** Uploads photos, fills title, buffered local price (+10–15%), condition, description, and location, and clicks **"Save draft"**.
   - **Craigslist:** Fills local posting flow, uploads images, and parks on the final **"Preview" / "Draft"** tab without publishing.

---

## Stage 5: Google Sheets State & Cross-Post Tracker (`scripts/sync_sheets.py` & `scripts/teardown_sold.py`)

1. Immediately after Stage 4 (or anytime to checkpoint state), sync the batch to Google Sheets and the local CSV mirror:
   ```powershell
   .venv\Scripts\python.exe scripts/sync_sheets.py
   ```
   This updates the master Google Sheet (`SKU | Item Title | Routing Bucket | Est Net Profit | Status | eBay Draft/Item URL | FBMP Listing URL | Craigslist Edit URL | Local Folder Path`) and `items/master_inventory.csv`.
2. **When an Item Sells (`teardown_sold.py`):**
   When the user says an item sold (e.g., *"ITEM-01 sold on eBay, tear down the cross-posts"*), run:
   ```powershell
   .venv\Scripts\python.exe scripts/teardown_sold.py <SKU>
   ```
   This reads the cross-posted URLs for `<SKU>`, opens/deletes the remaining active listings via Chrome CDP, and marks the SKU `SOLD` in Google Sheets.
