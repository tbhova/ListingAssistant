# Resale Listing Pipeline Skill (`resale-lister`)

> **Quick Install Prompt for Antigravity / Claude Code (Copy & Paste):**
> ```text
> Clone https://github.com/tbhova/ListingAssistant.git into my current workspace (or read its README.md), set up the Python environment, and run the "Grill Me" onboarding interview in README.md to configure my .env file.
> ```

An end-to-end, human-in-the-loop **Resale Listing Pipeline Skill** for **Google Antigravity** (`.agent/skills/resale-lister`) and **Claude Code** (`.claude/skills/resale-lister`).

Once installed, simply drop photos of multiple items into `/raw_photos/` and say:
> **"I dropped photos in /raw_photos, get started on listings."**

The agent automatically slices your photos into individual item folders by EXIF timestamp, inspects each item with multimodal vision, calculates conservative Zone 8 shipping & eBay net profit, scores and sorts items into an interactive **"Grill Me" review queue**, creates draft listings across **eBay Seller Hub**, **Facebook Marketplace**, and **Craigslist** via Playwright CDP (without ever auto-publishing), and logs every SKU to **Google Sheets** + a local CSV mirror.

---

## 🤖 Agent Installation & "Grill Me" Onboarding Runbook

*If you are an AI coding agent (Antigravity, Claude Code, Cursor, etc.) asked by the user to install or set up this repository, follow these exact steps:*

### Step 1: Clone & Create the Python Virtual Environment
1. Clone this repository into the user's target workspace directory (if not already cloned):
   ```bash
   git clone https://github.com/tbhova/ListingAssistant.git .
   ```
2. Create a local virtual environment and install dependencies:
   - **Windows (PowerShell):**
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\pip.exe install -r requirements.txt
     ```
   - **macOS / Linux:**
     ```bash
     python3 -m venv .venv
     ./.venv/bin/pip install -r requirements.txt
     ```
3. Copy `.env_example` to `.env`:
   ```bash
   cp .env_example .env
   ```

### Step 2: Conduct the "Grill Me" Onboarding Interview
Before finishing setup, **interview the user** (using your `ask_question` tool or conversational questions) to gather their local settings and populate `.env`:

1. **Origin ZIP Code (`ORIGIN_ZIP`):**
   - *Ask:* "What is your 5-digit ZIP code? (Used to calculate conservative Zone 8 USPS Ground Advantage / UPS Ground shipping and local pickup location)."
2. **Craigslist URL & Subdomain (`CRAIGSLIST_SUBDOMAIN`):**
   - *Ask:* "When you visit Craigslist in your browser, what is the URL (e.g., `https://sfbay.craigslist.org` or `https://seattle.craigslist.org`)?"
   - *Agent Action:* Automatically extract the subdomain before `.craigslist.org` (e.g., `sfbay`, `seattle`, `losangeles`, `chicago`) and write it to `CRAIGSLIST_SUBDOMAIN`.
3. **Craigslist Sub-Area & City/Neighborhood (`CRAIGSLIST_AREA` & `CRAIGSLIST_CITY`):**
   - *Ask:* "When you click **'post to classifieds'** on your local Craigslist, does it ask you to pick a sub-area (like *'south bay area'*) and/or a specific city/neighborhood (like *'san jose west'*), or does it go straight to *'choose a type of posting'*?"
   - *Agent Action:* Populate `CRAIGSLIST_AREA` and `CRAIGSLIST_CITY` (or leave them blank if their city doesn't use sub-areas).
4. **Photo Slicing Time-Gap Threshold (`PHOTO_TIME_GAP_SECONDS`):**
   - *Ask:* "How many seconds do you usually pause between finishing photos of one item and starting the next? (Default is `60` seconds; sequential photos taken within this window are grouped into the same item folder)."
5. **Profit & Routing Thresholds (`MIN_PROFIT_ALL_THREE`, `MIN_PROFIT_EBAY_ONLY`, `LOCAL_ONLY_MAX_WEIGHT_LBS`):**
   - *Ask:* "Do the default routing thresholds work for you, or would you like to customize them?"
     - **`ALL_THREE` (eBay + FB Marketplace + Craigslist):** Net Profit $\ge \$40$ (`MIN_PROFIT_ALL_THREE=40.00`)
     - **`EBAY_ONLY`:** Net Profit $\$10\text{–}\$39$ (`MIN_PROFIT_EBAY_ONLY=10.00`)
     - **`LOCAL_ONLY` (FB Marketplace + Craigslist):** Heavy/bulky items over `25 lbs` or `24 inches` where local comp $\ge \$20$
     - **`DONATE_OR_LOT`:** Net Profit $< \$10$
6. **Google Sheets Tracking (`GOOGLE_SHEET_ID`, Optional):**
   - *Ask:* "Do you want to sync your inventory to a Google Sheet right now (paste your Google Sheet URL and I'll extract the `GOOGLE_SHEET_ID`), or use the automatic local CSV mirror (`items/master_inventory.csv`) first?"
   - *Agent Action:* If the user pastes a full URL like `https://docs.google.com/spreadsheets/d/1-XY5ts3HF.../edit`, extract the ID between `/d/` and `/edit` and write it to `GOOGLE_SHEET_ID`. Point them to [`credentials/README.md`](./credentials/README.md) when they are ready to drop in `credentials/service_account.json`.

### Step 3: Back Up `.env` to Google Drive (Recommended)
Once the user's `.env` file is configured, offer to back it up to their personal **Google Drive** so their private configuration (`ORIGIN_ZIP`, `GOOGLE_SHEET_ID`, and API keys) is safely stored in the cloud without ever being committed to Git:

1. Check if the Antigravity **Google Drive** plugin (`antigravity-plugins-official/plugin/gdrive`) is installed, or suggest installing it.
2. Create a private backup folder in their Google Drive root (or check if one already exists):
   ```bash
   gdrive readonly search --name-contains "ListingAssistant_Backups"
   gdrive mutate mkdir "ListingAssistant_Backups" --parent root
   ```
3. Upload (or update in-place) the user's `.env` file into that folder:
   ```bash
   # First-time upload:
   gdrive mutate upload .env --parent <FOLDER_ID>

   # Updating an existing backup in-place:
   gdrive mutate update <FILE_ID> .env
   ```
4. Save the returned `GOOGLE_DRIVE_BACKUP_FOLDER_ID` and `GOOGLE_DRIVE_ENV_FILE_ID` into `.env`.
   *(On any future machine, the agent can restore `.env` in one command via `gdrive readonly download <FILE_ID> --out .env`!)*

### Step 4: One-Time Dedicated Chrome Profile Login (Optional Now or Before Stage 4)
Tell the user they can initialize their dedicated Chrome automation profile (`./chrome_profile` on CDP port `9222`) whenever they are ready to generate browser drafts:
```powershell
.\.venv\Scripts\python.exe scripts/launch_chrome.py --login-setup
```
This opens a dedicated Chrome window (which never conflicts with their main open Chrome browser) with tabs for **eBay Seller Hub**, **Facebook Marketplace**, and **Craigslist**. Once they log in once, their session cookies persist in `./chrome_profile` for all future runs.

---

## 🏗️ 5-Stage Pipeline Architecture

```text
ListingAssistant/
├── .env_example                         # Public template (copied to gitignored .env during setup)
├── requirements.txt                     # Pillow, pillow-heif, playwright, gspread, google-auth, python-dotenv, google-genai
├── .agent/skills/resale-lister/SKILL.md # Google Antigravity Skill definition
├── .claude/skills/resale-lister/SKILL.md# Claude Code Skill definition
├── credentials/README.md                # Google Cloud Service Account setup guide
├── raw_photos/                          # Drop raw phone/camera photos (.jpg, .png, .webp, .heic) here
├── items/                               # Auto-generated item_XX/ folders, review_queue.md, and master_inventory.csv
└── scripts/
    ├── group_photos.py                  # Stage 1: EXIF timestamp photo slicing & 1-2 photo outlier detection
    ├── analyze_items.py                 # Stage 2 & 3: Vision inspection, Zone 8 shipping math, routing & Priority Queue
    ├── launch_chrome.py                 # Helper: Launches dedicated Chrome profile on CDP port 9222
    ├── create_drafts.py                 # Stage 4: Playwright CDP multi-platform draft generator (Hard Safety Guard)
    ├── sync_sheets.py                   # Stage 5: Google Sheets + local CSV inventory sync
    └── teardown_sold.py                 # Stage 5 Helper: Cross-post teardown when a SKU sells
```

### Stage 1: Photo Slicing & Visual Audit (`scripts/group_photos.py`)
- Scans `/raw_photos/` and extracts EXIF `DateTimeOriginal` timestamps (supporting `.jpg`, `.jpeg`, `.png`, `.webp`, and iPhone `.heic` via `pillow-heif`).
- Groups sequential photos within `PHOTO_TIME_GAP_SECONDS` into `/items/item_01/`, `/items/item_02/`, etc.
- Flags 1–2 photo outlier groups in `items/grouping_report.json` so the agent's vision model can inspect and merge continuation shots (`--merge item_03 item_02`).

### Stage 2: Vision Extraction, Zone 8 Shipping Math & Routing (`scripts/analyze_items.py`)
- **Agent-Native Vision (Default, $0 API cost):** The agent inspects each `/items/item_XX/` folder directly and records Brand, Model/MPN, Condition, Included Accessories, Estimated Packaged Weight & Dimensions, and Sold Comp Price.
- Calculates **Estimated Net Profit** = `Comp Price - Est. Zone 8 Shipping (USPS Ground Advantage / UPS Ground w/ Dim Weight) - eBay Fees (13.5% + $0.40)`.
- Assigns each item to `LOCAL_ONLY`, `ALL_THREE`, `EBAY_ONLY`, or `DONATE_OR_LOT` (with automatic bundle suggestions).

### Stage 3: Priority Scoring & "Grill Me" Review Queue
- Computes `Priority = (Uncertainty_Score * 0.6) + (Normalized_Value_Score * 0.4)` and generates `items/review_queue.md` sorted from highest Priority Score to lowest.
- **High-Priority / High-Value Items:** Full draft descriptions + explicit clarifying questions (unreadable serials, untested functionality, wear details).
- **Low-Priority / Low-Value Items:** Concise descriptions ready for one-click approval.
- **Mandatory Pause:** Execution halts here until you answer the questions and approve the queue.

### Stage 4: Multi-Platform Playwright Drafts (`scripts/create_drafts.py`)
- Connects via `connect_over_cdp("http://localhost:9222")` to your dedicated `./chrome_profile`.
- **eBay:** Uploads photos, fills title, specifics, description, price, and shipping dimensions, and clicks **"Save for later" / "Save as draft"**.
- **Facebook Marketplace:** Uploads photos, fills local pickup details (with +12% negotiation buffer), and clicks **"Save draft"**.
- **Craigslist:** Navigates your local sub-area/city flow, fills fields, uploads images, and parks on the final **"Preview"** tab.
- **Hard Safety Rule:** A runtime guard (`assert_safe_action_label`) blocks any button matching `"Publish"`, `"List it"`, or `"List item"`.

### Stage 5: Google Sheets Sync & Sold SKU Teardown (`scripts/sync_sheets.py` & `scripts/teardown_sold.py`)
- Syncs `SKU | Item Title | Routing Bucket | Est Net Profit | Status | eBay Draft/Item URL | FBMP Listing URL | Craigslist Edit URL | Local Folder Path` to Google Sheets and `items/master_inventory.csv`.
- When an item sells, run `python scripts/teardown_sold.py <SKU> [--sold-on ebay|fbmp|craigslist]` to open/delete the remaining cross-posts and mark the SKU `SOLD`.

---

## 🔒 Keeping Your Personal `.env` Backed Up Privately (Optional)

Because `.env` is ignored in `.gitignore`, your personal ZIP code, Google Sheet ID, and API keys are never pushed to this public repository. To back up or restore your personal `.env` across machines:
1. **Google Drive (Built into Onboarding Step 3):** Ask your agent to *"Back up my `.env` to Google Drive"* (uses `gdrive mutate update <GOOGLE_DRIVE_ENV_FILE_ID> .env`) or *"Restore my ListingAssistant `.env` from Google Drive"* (`gdrive readonly download <GOOGLE_DRIVE_ENV_FILE_ID> --out .env`).
2. **Private Dotfiles / Gist:** Alternatively, store `.env` in a private GitHub Gist or private repository.
