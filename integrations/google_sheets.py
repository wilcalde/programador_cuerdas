import gspread
from google.oauth2.service_account import Credentials
import os

def sync_production_from_sheets():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_path = "integrations/service_account.json"
    
    if not os.path.exists(creds_path):
        return {"error": "Service account file missing"}

    try:
        creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
        client = gspread.authorize(creds)
        
        sheet_url = os.getenv("GOOGLE_SHEET_URL")
        sh = client.open_by_url(sheet_url)
        worksheet = sh.get_worksheet(0)
        
        data = worksheet.get_all_records()
        return {"success": True, "data": data}
        
    except Exception as e:
        return {"error": str(e)}
