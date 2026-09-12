import os
import re
import logging

from telegram import (
    Update,
    LabeledPrice,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI


# ==================================================
# PULSE AI CONFIGURATION
# ==================================================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing.")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing.")


# ==================================================
# PULSE PLUS / TELEGRAM STARS
# ==================================================

PULSE_PLUS_PRICE = 300
PULSE_PLUS_PAYLOAD = "pulse_plus_monthly_v1"

# 30 days
PULSE_SUBSCRIPTION_PERIOD = 30 * 24 * 60 * 60


# ==================================================
# PRIVACY POLICY
# ==================================================

PRIVACY_POLICY_URL = (
    "https://github.com/Dr-meme-AA/"
    "pulse-ai-bot/blob/main/PRIVACY.md"
)


# ==================================================
# OPENAI
# ==================================================

client = OpenAI(
    api_key=OPENAI_API_KEY
)


# ==================================================
# LOGGING
# ==================================================

logging.basicConfig(
    format=(
        "%(asctime)s - "
        "%(name)s - "
        "%(levelname)s - "
        "%(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ==================================================
# PULSE AI HEALTH ASSISTANT INSTRUCTIONS
# ==================================================

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


# ==================================================
# START COMMAND
# ==================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = (
        "💓 Welcome to Pulse AI\n\n"

        "I'm a bilingual AI health information assistant.\n\n"

        "You can describe your symptoms or ask a general health "
        "question in English or Arabic.\n\n"

        "Example:\n"
        "I have a 39°C fever and cough. What should I do?\n\n"

        "مثال:\n"
        "عندي حرارة 39 وكحة، ماذا أفعل؟\n\n"

        "⚠️ Pulse AI provides general health information and "
        "does not replace a doctor or emergency medical care.\n\n"

        "⭐ For increased Pulse AI access, use /upgrade"
    )

    await update.message.reply_text(message)


# ==================================================
# HELP COMMAND
# ==================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = (
        "💓 Pulse AI Help\n\n"

        "Simply send your health question or describe your "
        "symptoms.\n\n"

        "For better guidance, you can include:\n"
        "• Age\n"
        "• Symptoms\n"
        "• Temperature if relevant\n"
        "• How long the symptoms have lasted\n"
        "• Relevant medications or medical conditions\n\n"

        "يمكنك أيضاً الكتابة باللغة العربية.\n\n"

        "⚠️ For emergencies, contact your local emergency "
        "medical service.\n\n"

        "⭐ To view Pulse Plus, use /upgrade"
    )

    await update.message.reply_text(message)


# ==================================================
# PRIVACY COMMAND
# ==================================================

async def privacy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    keyboard = [
        [
            InlineKeyboardButton(
                "🔐 View Privacy Policy",
                url=PRIVACY_POLICY_URL
            )
        ]
    ]

    message = (
        "🔐 Pulse AI Privacy\n\n"

        "Please avoid sending unnecessary personal information "
        "such as your full name, home address, identification "
        "numbers, passwords, or financial information.\n\n"

        "Pulse AI is intended for general health information.\n\n"

        "You can read the complete Privacy Policy below."
    )

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ==================================================
# EMERGENCY COMMAND
# ==================================================

async def emergency(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = (
        "🚨 EMERGENCY\n\n"

        "If someone has severe difficulty breathing, severe "
        "chest pain, loss of consciousness, seizure, signs of "
        "stroke, severe bleeding, a severe allergic reaction, "
        "or another potentially life-threatening condition, "
        "seek emergency medical care immediately.\n\n"

        "إذا كانت هناك حالة طبية خطيرة أو مهددة للحياة، "
        "اتصل بخدمات الطوارئ المحلية فوراً."
    )

    await update.message.reply_text(message)


# ==================================================
# PULSE PLUS / TELEGRAM STARS
# ==================================================

async def upgrade_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    try:

        invoice_link = await context.bot.create_invoice_link(
            title="Pulse Plus",

            description=(
                "Pulse Plus membership with increased "
                "Pulse AI access. Renews every 30 days."
            ),

            payload=PULSE_PLUS_PAYLOAD,

            provider_token="",

            currency="XTR",

            prices=[
                LabeledPrice(
                    label="Pulse Plus - 30 days",
                    amount=PULSE_PLUS_PRICE,
                )
            ],

            subscription_period=PULSE_SUBSCRIPTION_PERIOD,
        )

        keyboard = [
            [
                InlineKeyboardButton(
                    f"⭐ Subscribe — {PULSE_PLUS_PRICE} Stars",
                    url=invoice_link,
                )
            ]
        ]

        message = (
            "💚 PULSE PLUS\n\n"

            "Upgrade your Pulse AI access.\n\n"

            f"⭐ Price: {PULSE_PLUS_PRICE} Telegram Stars\n"
            "📅 Subscription: Every 30 days\n"
            "💬 Increased Pulse AI usage\n"
            "🌐 English + Arabic\n"
            "🔒 Private AI health conversations\n\n"

            "The subscription automatically renews every "
            "30 days through Telegram Stars unless cancelled.\n\n"

            "⚠️ Pulse AI provides general health information "
            "and is not a doctor.\n\n"

            "Tap below to subscribe:"
        )

        await update.message.reply_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as error:

        logger.exception(
            "Error creating Pulse Plus invoice: %s",
            error
        )

        await update.message.reply_text(
            "💓 Pulse AI could not create the payment "
            "page right now.\n\n"
            "Please try again shortly."
        )


# ==================================================
# TELEGRAM PAYMENT PRE-CHECKOUT
# ==================================================

async def precheckout_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.pre_checkout_query

    if not query:
        return

    # Verify Pulse Plus invoice
    if query.invoice_payload != PULSE_PLUS_PAYLOAD:

        logger.warning(
            "Rejected unknown payment payload from user %s: %s",
            query.from_user.id,
            query.invoice_payload,
        )

        await query.answer(
            ok=False,
            error_message=(
                "This Pulse AI payment could not be verified. "
                "Please open /upgrade and try again."
            ),
        )

        return

    # Verify Telegram Stars
    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message=(
                "Invalid payment currency. "
                "Pulse Plus uses Telegram Stars."
            ),
        )

        return

    # Verify price
    if query.total_amount != PULSE_PLUS_PRICE:

        await query.answer(
            ok=False,
            error_message=(
                "The payment amount could not be verified. "
                "Please try again from /upgrade."
            ),
        )

        return

    await query.answer(ok=True)


# ==================================================
# SUCCESSFUL TELEGRAM STARS PAYMENT
# ==================================================

async def successful_payment_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    payment = update.message.successful_payment

    if not payment:
        return

    user = update.effective_user

    # Final payment validation
    if (
        payment.currency != "XTR"
        or payment.invoice_payload != PULSE_PLUS_PAYLOAD
        or payment.total_amount != PULSE_PLUS_PRICE
    ):

        logger.warning(
            "Unexpected successful payment data for user %s",
            user.id if user else "unknown",
        )

        return

    # --------------------------------------------------
    # TEMPORARY PLUS STATUS
    # --------------------------------------------------
    # This remains in memory only.
    # Later this should move to PostgreSQL.
    # --------------------------------------------------

    context.user_data["pulse_plan"] = "PLUS"

    context.user_data[
        "telegram_payment_charge_id"
    ] = payment.telegram_payment_charge_id

    if payment.subscription_expiration_date:

        context.user_data[
            "subscription_expiration_date"
        ] = payment.subscription_expiration_date

    context.user_data[
        "is_recurring"
    ] = bool(payment.is_recurring)


    logger.info(
        (
            "PULSE PLUS PAYMENT SUCCESS | "
            "Telegram user=%s | "
            "Stars=%s | "
            "Charge ID=%s | "
            "Recurring=%s | "
            "First recurring=%s | "
            "Expires=%s"
        ),
        user.id if user else "unknown",
        payment.total_amount,
        payment.telegram_payment_charge_id,
        payment.is_recurring,
        payment.is_first_recurring,
        payment.subscription_expiration_date,
    )


    if payment.subscription_expiration_date:

        expires = payment.subscription_expiration_date

        expiration_text = expires.strftime(
            "%d %B %Y at %H:%M UTC"
        )

    else:

        expiration_text = "30 days from purchase"


    message = (
        "✅ PULSE PLUS ACTIVATED\n\n"

        "Your Telegram Stars payment was successful. 💚\n\n"

        "⭐ Plan: Pulse Plus\n"
        f"⭐ Paid: {payment.total_amount} Stars\n"
        "📅 Billing: Every 30 days\n"
        f"⏳ Current period: {expiration_text}\n"
        "💬 Increased Pulse AI access\n"
        "🌐 English + Arabic\n\n"

        "Thank you for supporting Pulse AI.\n\n"

        "⚠️ Pulse AI provides general health information "
        "and does not replace professional medical care."
    )

    await update.message.reply_text(message)


# ==================================================
# ACCOUNT COMMAND
# ==================================================

async def account_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    plan = context.user_data.get(
        "pulse_plan",
        "FREE"
    )

    if plan == "PLUS":

        expiration = context.user_data.get(
            "subscription_expiration_date"
        )

        if expiration:

            expiration_text = expiration.strftime(
                "%d %B %Y at %H:%M UTC"
            )

        else:

            expiration_text = "Active"


        message = (
            "💚 PULSE AI ACCOUNT\n\n"

            "⭐ Plan: Pulse Plus\n"
            "✅ Status: Active\n"
            f"📅 Current period ends: {expiration_text}\n\n"

            "Thank you for supporting Pulse AI."
        )

    else:

        message = (
            "💚 PULSE AI ACCOUNT\n\n"

            "Plan: Free\n\n"

            "You are currently using the free Pulse AI plan.\n\n"

            "Use /upgrade to view Pulse Plus."
        )

    await update.message.reply_text(message)


# ==================================================
# PAYMENT SUPPORT
# ==================================================

async def paysupport_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = (
        "💳 PULSE AI PAYMENT SUPPORT\n\n"

        "If you have a problem with a Pulse Plus payment "
        "or subscription, please contact the Pulse AI team.\n\n"

        "Please include:\n"
        "• Your Telegram username\n"
        "• Approximate payment date\n"
        "• A short description of the problem\n\n"

        "⚠️ Never send your Telegram password, wallet seed "
        "phrase, private key, credit-card details, or "
        "authentication codes."
    )

    await update.message.reply_text(message)


# ==================================================
# AI MESSAGE HANDLER
# ==================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message:
        return

    if not update.message.text:
        return

    user_message = update.message.text.strip()

    if not user_message:
        return

    chat = update.effective_chat


    # ==================================================
    # GROUP / SUPERGROUP BEHAVIOR
    # ==================================================
    #
    # Pulse AI must stay completely silent unless
    # somebody explicitly mentions:
    #
    # @Pulseaihealthbot
    #
    # ==================================================

    if chat.type in ("group", "supergroup"):

        # Get actual Telegram username dynamically
        bot_username = context.bot.username

        if not bot_username:
            logger.warning(
                "Could not determine bot username."
            )
            return

        bot_mention = f"@{bot_username}"

        # Match @Pulseaihealthbot regardless of capitalization
        mention_pattern = re.compile(
            rf"(?<!\w){re.escape(bot_mention)}(?!\w)",
            re.IGNORECASE,
        )

        # ----------------------------------------------
        # NO MENTION = DO NOTHING
        # ----------------------------------------------

        if not mention_pattern.search(user_message):

            logger.debug(
                "Ignoring group message because bot "
                "was not mentioned."
            )

            return


        # ----------------------------------------------
        # REMOVE @Pulseaihealthbot
        # BEFORE SENDING QUESTION TO OPENAI
        # ----------------------------------------------

        user_message = mention_pattern.sub(
            "",
            user_message
        ).strip()


        # ----------------------------------------------
        # USER MENTIONED BOT BUT ASKED NOTHING
        # ----------------------------------------------

        if not user_message:

            await update.message.reply_text(
                "💓 Hi! Please ask me your health question.\n\n"

                "Example:\n"

                f"{bot_mention} "
                "I have a fever and cough. What should I do?\n\n"

                "For more private health questions, you can also "
                "message me directly."
            )

            return


    # ==================================================
    # SEND HEALTH QUESTION TO OPENAI
    # ==================================================

    try:

        await update.message.chat.send_action(
            action="typing"
        )

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


        # Reply directly to the user's message
        await update.message.reply_text(
            answer
        )


    except Exception as error:

        logger.exception(
            "Error processing AI message: %s",
            error
        )

        await update.message.reply_text(
            "💓 Pulse AI is temporarily unable to answer. "
            "Please try again shortly."
        )


# ==================================================
# RUN PULSE AI
# ==================================================

def main():

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )


    # ==================================================
    # PRIVATE CHAT ONLY
    # ==================================================

    private_only = filters.ChatType.PRIVATE


    # --------------------------------------------------
    # Private commands
    # --------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
            filters=private_only,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command,
            filters=private_only,
        )
    )

    application.add_handler(
        CommandHandler(
            "privacy",
            privacy,
            filters=private_only,
        )
    )

    application.add_handler(
        CommandHandler(
            "emergency",
            emergency,
            filters=private_only,
        )
    )

    application.add_handler(
        CommandHandler(
            "upgrade",
            upgrade_command,
            filters=private_only,
        )
    )

    application.add_handler(
        CommandHandler(
            "account",
            account_command,
            filters=private_only,
        )
    )

    application.add_handler(
        CommandHandler(
            "paysupport",
            paysupport_command,
            filters=private_only,
        )
    )


    # ==================================================
    # PAYMENT HANDLERS
    # ==================================================

    application.add_handler(
        PreCheckoutQueryHandler(
            precheckout_callback
        )
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_callback,
        )
    )


    # ==================================================
    # NORMAL TEXT / HEALTH QUESTIONS
    # ==================================================
    #
    # PRIVATE CHAT:
    #   Answer normally.
    #
    # GROUP:
    #   handle_message() checks for @Pulseaihealthbot.
    #
    # ==================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )


    logger.info(
        "Pulse AI is running..."
    )


    application.run_polling(
        drop_pending_updates=True
    )


# ==================================================
# START
# ==================================================

if __name__ == "__main__":
    main()
