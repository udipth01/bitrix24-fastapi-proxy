import os
from supabase import create_client
from dotenv import load_dotenv
load_dotenv()

BOLNA_TOKEN = os.getenv("BOLNA_API_KEY")
BITRIX_WEBHOOK = os.getenv("BITRIX_WEBHOOK")
SUPABASE_URL = "https://fbputkobdsqorfdizbyf.supabase.co"
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

