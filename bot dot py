import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from openai import OpenAI

# --------------------------------------------------
# Pulse AI Configuration
# --------------------------------------------------

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing.")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# --------------------------------------------------
# Pulse AI Health Assistant Instructions
# --------------------------------------------------

SYSTEM_PROMPT = """
You are Pulse AI, a bilingual AI health information assistant.

Your purpose is to provide helpful, clear, cautious general health
information in English and Arabic.

LANGUAGE:
- If the user writes in English, answer in English.
- If the user writes in Arabic, answer naturally in Arabic.
- If the user mixes Arabic and English, respond in the language that
  best matches the user.
- Keep explanations easy to understand.

STYLE:
- Be warm, calm, respectful, and concise.
- Ask relevant follow-up questions when important information is missing.
- Give practical next steps when appropriate.
- Do not unnecessarily frighten the user.

MEDICAL SAFETY:
- You are an AI health information assistant, not a doctor.
- Do not claim to provide a confirmed diagnosis.
- Do not pretend that you examined the patient.
- Explain reasonable possibilities rather than declaring a diagnosis.
- Do not tell users to stop or change prescribed medication without
  appropriate professional medical advice.
- When appropriate, encourage consultation with a qualified healthcare
  professional.

EMERGENCIES:
Always prioritize urgent medical care when symptoms could represent
a medical emergency.

Examples of red flags include:
- severe difficulty breathing
- severe chest pain
- loss of consciousness
- seizure
- signs of stroke
- severe allergic reaction
- uncontrolled bleeding
- severe dehydration
- confusion or major change in consciousness
- suicidal thoughts or immediate danger to self or others

If emergency warning signs may be present, clearly tell the user to
seek emergency medical care immediately or contact their local
emergency service.

CHILDREN:
Be especially cautious when the question concerns babies or children.
Consider age, temperature, hydration, breathing, alertness, duration
of symptoms, and other relevant warning signs.

Never present Pulse AI as a replacement for emergency services,
doctors, pharmacists, or other qualified healthcare professionals.
"""


# --------------------------------------------------
# Telegram Commands
# --------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "💓 Welcome to Pulse AI\n\n"
        "I'm an AI health information assistant.\n\n"
        "You can describe your symptoms or ask a general health question "
        "in English or Arabic.\n\n"
        "Example:\n"
        "I have a 39°C fever and cough. What should I do?\n\n"
        "مثال:\n"
        "عندي حرارة 39 وكحة، ماذا أفعل؟\n\n"
        "⚠️ Pulse AI provides general health information and does not "
        "replace a doctor or emergency medical care."
    )

    await update.message.reply_text(message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "💓 Pulse AI Help\n\n"
        "Simply send your health question or describe your symptoms.\n\n"
        "For better guidance, you can include:\n"
        "• Age\n"
        "• Symptoms\n"
        "• Temperature if relevant\n"
        "• How long the symptoms have lasted\n"
        "• Relevant medications or medical conditions\n\n"
        "يمكنك أيضاً الكتابة باللغة العربية.\n\n"
        "⚠️ For emergencies, contact your local emergency medical service."
    )

    await update.message.reply_text(message)


async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🔐 Privacy\n\n"
        "Please avoid sending your full name, address, identification "
        "numbers, passwords, financial information, or other unnecessary "
        "personal information.\n\n"
        "Pulse AI is intended for general health information."
    )

    await update.message.reply_text(message)


async def emergency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = (
        "🚨 EMERGENCY\n\n"
        "If someone has severe difficulty breathing, severe chest pain, "
        "loss of consciousness, seizure, signs of stroke, severe bleeding, "
        "a severe allergic reaction, or another potentially life-threatening "
        "condition, seek emergency medical care immediately.\n\n"
        "إذا كانت هناك حالة طبية خطيرة أو مهددة للحياة، اتصل بخدمات "
        "الطوارئ المحلية فوراً."
    )

    await update.message.reply_text(message)


# --------------------------------------------------
# AI Message Handler
# --------------------------------------------------

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message or not update.message.text:
        return

    user_message = update.message.text.strip()

    if not user_message:
        return

    try:
        await update.message.chat.send_action("typing")

        response = client.responses.create(
            model="gpt-5.6-luna",
            instructions=SYSTEM_PROMPT,
            input=user_message,
            max_output_tokens=700,
        )

        answer = response.output_text

        if not answer:
            answer = (
                "Sorry, I couldn't generate a response. "
                "Please try again."
            )

        await update.message.reply_text(answer)

    except Exception as error:
        logger.exception("Error processing message: %s", error)

        await update.message.reply_text(
            "💓 Pulse AI is temporarily unable to answer. "
            "Please try again shortly."
        )


# --------------------------------------------------
# Run Pulse AI
# --------------------------------------------------

def main():
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("privacy", privacy))
    application.add_handler(CommandHandler("emergency", emergency))

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    print("Pulse AI is running...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
