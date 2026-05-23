# ===== FILE: config.py =====
import os
from dotenv import load_dotenv

load_dotenv()

ADMIN_SECRET = os.getenv("ADMIN_SECRET")

# ===== DIGIFLAZZ =====
DIGIFLAZZ_USERNAME = os.getenv("DIGIFLAZZ_USERNAME")
DIGIFLAZZ_KEY = os.getenv("DIGIFLAZZ_KEY")

# ===== TRIPAY =====
TRIPAY_API_KEY = os.getenv("TRIPAY_API_KEY")
TRIPAY_PRIVATE_KEY = os.getenv("TRIPAY_PRIVATE_KEY")
<<<<<<< HEAD
TRIPAY_MERCHANT_CODE = "T41788"
TRIPAY_CALLBACK_URL = "mcd-topup-bot-production.up.railwat.app/callback"
TRIPAY_BASE_URL = "https://tripay.co.id/api-sandbox/transaction/create"
=======
TRIPAY_MERCHANT_CODE = os.getenv("TRIPAY_MERCHANT_CODE")
TRIPAY_CALLBACK_URL = os.getenv("TRIPAY_CALLBACK_URL")
TRIPAY_BASE_URL = os.getenv("TRIPAY_BASE_URL")
>>>>>>> 86f7b85 (Engine 2.0)

INSTANCE_ID = os.getenv("INSTANCE_ID")
TOKEN_ULTRAMSG = os.getenv("TOKEN_ULTRAMSG")  

<<<<<<< HEAD
BASE_URL = "mcd-topup-bot-production.up.railwat.app"
=======
BASE_URL = os.getenv("BASE_URL")  # URL publik untuk callback dan link QR
>>>>>>> 86f7b85 (Engine 2.0)

ENV = "DEV"   # ganti ke "PROD" kalau sudah live
