import gspread
from google.oauth2.service_account import Credentials
import os

def sync_production_from_sheets():
    """
    Connects to Google Sheets and syncs 'Producción Real' with Supabase orders.
    Requires a service_account.json file.
    """
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    # Path to your service account JSON
    creds_path = "integrations/service_account.json"
    
    if not os.path.exists(creds_path):
        return {"error": "Service account file missing"}

    try:
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        client = gspread.authorize(creds)
        
        sheet_url = os.getenv("GOOGLE_SHEET_URL")
        sh = client.open_by_url(sheet_url)
        worksheet = sh.get_worksheet(0) # First sheet
        
        data = worksheet.get_all_records()
        # Logic to parse data and update Supabase would go here
        return {"success": True, "data": data}
        
    except Exception as e:
        return {"error": str(e)}
