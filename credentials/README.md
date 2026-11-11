# Google Sheets Service Account Setup (`sync_sheets.py`)

To enable automated Google Sheets logging in Stage 5 (`scripts/sync_sheets.py`):

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Create or select a project and enable the **Google Sheets API** and **Google Drive API**.
3. Navigate to **IAM & Admin > Service Accounts** and click **Create Service Account**.
4. Grant it basic editor access (or simply create it without project roles), then go to the **Keys** tab -> **Add Key** -> **Create new key (JSON)**.
5. Save the downloaded JSON file as:
   `c:\Users\Tyler\Documents\ListingAssistant\credentials\service_account.json`
6. Open the JSON file, copy the `client_email` address (e.g., `resale-bot@your-project.iam.gserviceaccount.com`), and **Share** your target Google Sheet with that email address as an **Editor**.
7. Copy the Spreadsheet ID from your Google Sheet URL (`https://docs.google.com/spreadsheets/d/<GOOGLE_SHEET_ID>/edit`) and paste it into `.env` under `GOOGLE_SHEET_ID`.

> **Note:** Even if Google Sheets is not yet configured, `scripts/sync_sheets.py` and `scripts/teardown_sold.py` automatically maintain a full local mirror at `items/master_inventory.csv` so you never lose listing state or draft URLs.
