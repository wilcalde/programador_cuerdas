import os
from supabase import create_client, Client
from dotenv import load_dotenv

if os.path.exists('.env'):
    load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL or SUPABASE_KEY not set in environment")
    return create_client(SUPABASE_URL, SUPABASE_KEY)
