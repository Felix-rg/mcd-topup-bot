# ===== FILE: config.py =====
import os
from dotenv import load_dotenv

load_dotenv()

# ===== DIGIFLAZZ =====
DIGIFLAZZ_USERNAME = os.getenv("DIGIFLAZZ_USERNAME")
DIGIFLAZZ_KEY = os.getenv("DIGIFLAZZ_KEY")

# ===== TRIPAY =====
TRIPAY_API_KEY = os.getenv("TRIPAY_API_KEY")
TRIPAY_PRIVATE_KEY = os.getenv("TRIPAY_PRIVATE_KEY")
TRIPAY_MERCHANT_CODE = "T41788"
TRIPAY_CALLBACK_URL = "mcd-topup-bot-production.up.railwat.app/callback"
TRIPAY_BASE_URL = "https://tripay.co.id/api-sandbox/transaction/create"

INSTANCE_ID = os.getenv("INSTANCE_ID")
TOKEN_ULTRAMSG = os.getenv("TOKEN_ULTRAMSG")  

BASE_URL = "mcd-topup-bot-production.up.railwat.app"

PRICES = {
    "telkomsel": {"5k": 6500, "10k": 11000, "15k": 16000, "20k": 21000},
    "xl": {"5k": 6200, "10k": 10500, "15k": 15500, "20k": 20500}
}
