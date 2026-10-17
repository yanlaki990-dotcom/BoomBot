import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x]

CHANNEL_ID = os.getenv("CHANNEL_ID", "@BoomDLC")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/BoomDLC")
SUPPORT_URL = os.getenv("SUPPORT_URL", "https://t.me/BoomDLC")

PLANS = {
    "1d": ("1 день", 1, False),
    "7d": ("7 дней", 7, False),
    "30d": ("30 дней", 30, True),
    "90d": ("3 месяца", 90, True),
    "forever": ("Навсегда", None, True),
}

LOADER_URL = os.getenv("LOADER_URL", "https://github.com/BoomDLC/Loader/releases/latest")
