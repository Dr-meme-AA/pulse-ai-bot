import os

PROJECT_NAME = "Pulse AI"
PROJECT_SUPPORT_EMAIL = "pulse.support.ai@gmail.com"
PROJECT_X = "@Pulseai_support"
PROJECT_TELEGRAM_SUPPORT = "@pulse_support_ai"
BOT_USERNAME = "@Pulseaihealthbot"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
DATABASE_URL = os.environ.get("DATABASE_URL")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing.")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing.")

FREE_QUESTION_LIMIT = 20
PLUS_QUESTION_LIMIT = 100

PULSE_PLUS_PRICE = 300
PULSE_PLUS_PAYLOAD = "pulse_plus_monthly_v1"
PULSE_SUBSCRIPTION_PERIOD = 30 * 24 * 60 * 60

HEALTH_MODEL = "gpt-5.6-luna"
VISION_MODEL = "gpt-5.6-terra"
TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"

PRIVACY_POLICY_URL = (
    "https://github.com/Dr-meme-AA/"
    "pulse-ai-bot/blob/main/PRIVACY.md"
)
