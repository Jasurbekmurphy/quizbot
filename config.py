import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
# Test yarata oladiganlar. Bo'sh bo'lsa — hamma o'z testini yarata oladi. Testni yechish hamma uchun ochiq.
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
DB_PATH = os.getenv("DB_PATH", "quiz.db")
# Statistikani ko'ra oladiganlar (bot egasi). Telegram ID raqamlari, vergul bilan.
OWNER_IDS = {int(x) for x in os.getenv("OWNER_IDS", "").replace(" ", "").split(",") if x}
