import os
import re
import base64
import tempfile
import logging

from datetime import datetime, timezone

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

from openai import AsyncOpenAI


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
# OPENAI
# ==================================================

client = AsyncOpenAI(
    api_key=OPENAI_API_KEY
)

# Normal text / voice health questions
HEALTH_MODEL = "gpt-5.6-luna"

# Use a stronger model for medical-image interpretation
VISION_MODEL = "gpt-5.6-terra"

# Speech-to-text
TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"


# ==================================================
# TEST LIMITS
# ==================================================

# Trusted beta testers:
# 20 free health questions, images or voice notes.
FREE_QUESTION_LIMIT = 20

# Pulse Plus test allowance.
PLUS_QUESTION_LIMIT = 100


# ==================================================
# VOICE LIMITS
# ==================================================

# Maximum voice-note duration for testing.
VOICE_MAX_SECONDS = 120

# Telegram bot downloads have practical file-size limits.
MAX_MEDIA_BYTES = 18 * 1024 * 1024


# ==================================================
# TELEGRAM STARS
# ==================================================

PULSE_PLUS_PRICE = 300
PULSE_PLUS_PAYLOAD = "pulse_plus_monthly_v1"

# Telegram recurring subscription = 30 days.
PULSE_SUBSCRIPTION_PERIOD = 30 * 24 * 60 * 60


# ==================================================
# PRIVACY
# ==================================================

PRIVACY_POLICY_URL = (
    "https://github.com/Dr-meme-AA/"
    "pulse-ai-bot/blob/main/PRIVACY.md"
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
# PULSE AI SYSTEM PROMPT
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
- Be warm, calm, respectful, practical and concise.
- Ask relevant follow-up questions when important information is missing.
- Give practical next steps when appropriate.
- Do not unnecessarily frighten the user.

MEDICAL SAFETY:
- You are an AI health information assistant, not a doctor.
- Do not claim to provide a confirmed diagnosis.
- Do not pretend that you physically examined the patient.
- Explain reasonable possibilities rather than declaring a diagnosis.
- Do not tell users to stop or change prescribed medication without
  appropriate professional medical advice.
- When appropriate, recommend consultation with a qualified healthcare
  professional.

EMERGENCIES:
Always prioritize urgent medical care when symptoms could represent
a medical emergency.

Red flags can include:
- severe difficulty breathing
- severe or crushing chest pain
- loss of consciousness
- seizure
- signs of stroke
- severe allergic reaction
- uncontrolled or heavy bleeding
- severe dehydration
- confusion or major change in consciousness
- serious trauma
- suicidal thoughts
- immediate danger to self or others

If emergency warning signs may be present, clearly tell the user to
seek emergency medical care immediately or contact the appropriate
local emergency service.

CHILDREN:
Be especially cautious when the question concerns babies or children.

Consider:
- age
- temperature
- hydration
- breathing
- alertness
- duration of symptoms
- pain
- feeding
- urine output
- relevant warning signs

Never present Pulse AI as a replacement for emergency services,
doctors, pharmacists, hospitals, or other qualified healthcare
professionals.
"""


# ==================================================
# MEDICAL IMAGE SAFETY
# ==================================================

VISION_PROMPT = """
The user has provided an image for general health-information purposes.

IMAGE SAFETY RULES:

- Carefully describe only what is reasonably visible in the image.
- Do NOT claim certainty from an image alone.
- Do NOT make a definitive medical diagnosis.
- Clearly explain limitations of visual assessment.
- Do not claim to know the exact depth of a wound from a photograph.
- Do not claim with certainty that stitches are or are not required.
- Do not claim that bones, nerves, tendons, internal organs, or deeper
  tissues are normal merely because they cannot be seen.

For wounds or injuries, assess visible warning features such as:
- wound edges appearing separated or gaping
- visible significant tissue injury
- active or heavy bleeding
- marked swelling
- contamination
- concerning discoloration
- possible infection
- location over a joint, hand, face or other important area

When appropriate, ask about:
- when the injury occurred
- how it occurred
- ongoing bleeding
- numbness
- weakness
- movement
- severe pain
- contamination
- animal/human bites
- tetanus vaccination

If the appearance or history suggests that urgent assessment may be
needed, clearly recommend prompt in-person medical evaluation.

If emergency features are apparent or described, advise emergency care.

For rashes, swelling, skin lesions, medications, reports, or other
health-related images, provide cautious general information and explain
when professional assessment would be appropriate.

Always respond in the user's language when possible.
"""


# ==================================================
# USER PLAN / QUESTION COUNT
# ==================================================

def get_plan(context: ContextTypes.DEFAULT_TYPE):
    plan = context.user_data.get("pulse_plan", "FREE")

    # Check whether an in-memory Plus subscription expired.
    if plan == "PLUS":

        expiration = context.user_data.get(
            "subscription_expiration_date"
        )

        if expiration:

            try:
                now = datetime.now(timezone.utc)

                if expiration <= now:
                    context.user_data["pulse_plan"] = "FREE"
                    context.user_data["questions_used"] = 0
                    plan = "FREE"

            except Exception:
                pass

    return plan


def get_question_limit(context: ContextTypes.DEFAULT_TYPE):

    if get_plan(context) == "PLUS":
        return PLUS_QUESTION_LIMIT

    return FREE_QUESTION_LIMIT


def get_questions_used(context: ContextTypes.DEFAULT_TYPE):

    return context.user_data.get(
        "questions_used",
        0
    )


def questions_remaining(context: ContextTypes.DEFAULT_TYPE):

    used = get_questions_used(context)
    limit = get_question_limit(context)

    return max(
        limit - used,
        0
    )


def record_successful_question(
    context: ContextTypes.DEFAULT_TYPE
):

    used = get_questions_used(context) + 1

    context.user_data["questions_used"] = used

    return used


# ==================================================
# CREATE PULSE PLUS INVOICE
# ==================================================

async def create_plus_invoice(
    context: ContextTypes.DEFAULT_TYPE
):

    return await context.bot.create_invoice_link(
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


# ==================================================
# LIMIT / UPGRADE MESSAGE
# ==================================================

async def send_upgrade_offer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    limit_reached=False,
):

    try:

        invoice_link = await create_plus_invoice(context)

        keyboard = [
            [
                InlineKeyboardButton(
                    f"⭐ Pulse Plus — {PULSE_PLUS_PRICE} Stars",
                    url=invoice_link,
                )
            ]
        ]

    except Exception as error:

        logger.exception(
            "Could not create upgrade invoice: %s",
            error
        )

        keyboard = []


    if limit_reached:

        message = (
            "💚 You've used your 20 free Pulse AI questions.\n\n"
            "Thank you for helping us test Pulse AI.\n\n"
            "To continue using Pulse AI, you can upgrade to "
            "Pulse Plus.\n\n"
            f"⭐ {PULSE_PLUS_PRICE} Telegram Stars\n"
            "📅 Renews every 30 days\n"
            f"💬 Up to {PLUS_QUESTION_LIMIT} questions "
            "during this testing version\n"
            "📷 Image support\n"
            "🎙 Voice-note support\n"
            "🌐 English + Arabic\n\n"
            "⚠️ Emergency medical care should never be delayed "
            "because of a Pulse AI usage limit."
        )

    else:

        message = (
            "💚 PULSE PLUS\n\n"
            f"⭐ {PULSE_PLUS_PRICE} Telegram Stars\n"
            "📅 Renews every 30 days\n"
            f"💬 Up to {PLUS_QUESTION_LIMIT} questions "
            "during this testing version\n"
            "📷 Image analysis\n"
            "🎙 Voice-note questions\n"
            "🌐 English + Arabic\n\n"
            "Pulse AI provides general health information "
            "and is not a doctor."
        )


    await update.effective_message.reply_text(
        message,
        reply_markup=(
            InlineKeyboardMarkup(keyboard)
            if keyboard
            else None
        ),
    )


# ==================================================
# CHECK QUOTA
# ==================================================

async def check_question_access(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    used = get_questions_used(context)
    limit = get_question_limit(context)

    if used >= limit:

        if get_plan(context) == "FREE":

            await send_upgrade_offer(
                update,
                context,
                limit_reached=True,
            )

        else:

            await update.effective_message.reply_text(
                "💚 You've reached the current Pulse Plus "
                "testing allowance.\n\n"
                "Please contact the Pulse AI team if you need "
                "additional testing access."
            )

        return False

    return True


# ==================================================
# USAGE STATUS
# ==================================================

async def show_usage_after_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    used = get_questions_used(context)
    limit = get_question_limit(context)
    remaining = max(limit - used, 0)
    plan = get_plan(context)


    if plan == "FREE":

        if remaining > 0:

            await update.effective_message.reply_text(
                f"🧪 Pulse AI Test: {used}/{limit} questions used "
                f"• {remaining} remaining"
            )

        else:

            await send_upgrade_offer(
                update,
                context,
                limit_reached=True,
            )

    else:

        await update.effective_message.reply_text(
            f"⭐ Pulse Plus: {used}/{limit} questions used "
            f"• {remaining} remaining"
        )


# ==================================================
# GROUP MENTION HELPER
# ==================================================

def extract_group_mention(
    raw_text,
    context: ContextTypes.DEFAULT_TYPE,
):

    bot_username = (
        context.bot.username
        or "Pulseaihealthbot"
    )

    bot_mention = f"@{bot_username}"

    mention_pattern = re.compile(
        rf"(?<!\w){re.escape(bot_mention)}(?!\w)",
        re.IGNORECASE,
    )

    if not mention_pattern.search(raw_text):
        return None

    clean_text = mention_pattern.sub(
        "",
        raw_text,
    ).strip()

    return clean_text


# ==================================================
# /START
# ==================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        "💓 Welcome to Pulse AI\n\n"

        "I'm a bilingual AI health information assistant.\n\n"

        "During this private testing phase you can use:\n\n"

        "💬 Text questions\n"
        "📷 Health-related photos\n"
        "🎙 Voice notes\n"
        "🌐 English or Arabic\n\n"

        f"🎁 You currently have {FREE_QUESTION_LIMIT} "
        "free test questions.\n\n"

        "Example:\n"
        "I have a 39°C fever and cough. What should I do?\n\n"

        "مثال:\n"
        "عندي حرارة 39 وكحة، ماذا أفعل؟\n\n"

        "You can also send a photo with a short explanation, "
        "or send your question as a voice note.\n\n"

        "⚠️ Pulse AI provides general health information. "
        "It is not a doctor and does not replace emergency "
        "or professional medical care."
    )

    await update.message.reply_text(message)


# ==================================================
# /HELP
# ==================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        "💓 Pulse AI Help\n\n"

        "You can communicate with Pulse AI in three ways:\n\n"

        "💬 TEXT\n"
        "Write your health question normally.\n\n"

        "📷 PHOTO\n"
        "Send a health-related image. Adding a caption explaining "
        "what happened will help Pulse AI provide better information.\n\n"

        "🎙 VOICE\n"
        "Send a voice note describing your question or symptoms.\n\n"

        "Helpful details include:\n"
        "• Age\n"
        "• Symptoms\n"
        "• Temperature\n"
        "• Duration\n"
        "• Relevant medication\n"
        "• Medical conditions\n"
        "• What happened before an injury\n\n"

        "يمكنك استخدام البوت باللغة العربية أيضاً.\n\n"

        "⚠️ Pulse AI cannot confirm a diagnosis from text, "
        "voice, or an image alone.\n\n"

        "For emergencies, seek immediate professional medical care."
    )

    await update.message.reply_text(message)


# ==================================================
# /PRIVACY
# ==================================================

async def privacy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    keyboard = [
        [
            InlineKeyboardButton(
                "🔐 View Privacy Policy",
                url=PRIVACY_POLICY_URL,
            )
        ]
    ]

    message = (
        "🔐 Pulse AI Privacy\n\n"

        "Health questions, images and voice notes may contain "
        "sensitive information.\n\n"

        "Please avoid sending unnecessary identifying information "
        "such as:\n"
        "• Full legal name\n"
        "• Identification numbers\n"
        "• Home address\n"
        "• Passwords\n"
        "• Financial information\n\n"

        "Images and voice notes sent for analysis may be processed "
        "by the AI services required to answer your question.\n\n"

        "Read the full Privacy Policy below."
    )

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


# ==================================================
# /EMERGENCY
# ==================================================

async def emergency(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        "🚨 EMERGENCY\n\n"

        "Seek immediate emergency medical care if someone has "
        "severe difficulty breathing, severe chest pain, loss of "
        "consciousness, seizure, signs of stroke, severe bleeding, "
        "a severe allergic reaction, or another potentially "
        "life-threatening condition.\n\n"

        "Do not wait for an AI response in an emergency.\n\n"

        "إذا كانت هناك حالة طبية خطيرة أو مهددة للحياة، "
        "اطلب المساعدة الطبية الطارئة فوراً."
    )

    await update.message.reply_text(message)


# ==================================================
# /UPGRADE
# ==================================================

async def upgrade_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await send_upgrade_offer(
        update,
        context,
        limit_reached=False,
    )


# ==================================================
# /USAGE
# ==================================================

async def usage_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    plan = get_plan(context)
    used = get_questions_used(context)
    limit = get_question_limit(context)
    remaining = max(limit - used, 0)

    message = (
        "💓 PULSE AI USAGE\n\n"
        f"Plan: {plan}\n"
        f"Questions used: {used}/{limit}\n"
        f"Questions remaining: {remaining}\n\n"
        "Text, image and voice questions each count as one question."
    )

    await update.message.reply_text(message)


# ==================================================
# /ACCOUNT
# ==================================================

async def account_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    plan = get_plan(context)
    used = get_questions_used(context)
    limit = get_question_limit(context)
    remaining = max(limit - used, 0)


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
            f"💬 Usage: {used}/{limit}\n"
            f"💬 Remaining: {remaining}\n"
            f"📅 Current period ends: {expiration_text}"
        )

    else:

        message = (
            "💚 PULSE AI ACCOUNT\n\n"
            "Plan: Free Test\n\n"
            f"💬 Usage: {used}/{limit}\n"
            f"💬 Remaining: {remaining}\n\n"
            "Use /upgrade to view Pulse Plus."
        )


    await update.message.reply_text(message)


# ==================================================
# /PAYSUPPORT
# ==================================================

async def paysupport_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = (
        "💳 PULSE AI PAYMENT SUPPORT\n\n"

        "If you experience a problem with a Pulse Plus payment "
        "or subscription, contact the Pulse AI team.\n\n"

        "Please include:\n"
        "• Telegram username\n"
        "• Approximate payment date\n"
        "• Short description of the issue\n\n"

        "⚠️ Never send passwords, authentication codes, "
        "credit-card information, wallet seed phrases, "
        "or private keys."
    )

    await update.message.reply_text(message)


# ==================================================
# PAYMENT PRE-CHECKOUT
# ==================================================

async def precheckout_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.pre_checkout_query

    if not query:
        return


    if query.invoice_payload != PULSE_PLUS_PAYLOAD:

        await query.answer(
            ok=False,
            error_message=(
                "This Pulse AI payment could not be verified."
            ),
        )

        return


    if query.currency != "XTR":

        await query.answer(
            ok=False,
            error_message=(
                "Pulse Plus uses Telegram Stars."
            ),
        )

        return


    if query.total_amount != PULSE_PLUS_PRICE:

        await query.answer(
            ok=False,
            error_message=(
                "The payment amount could not be verified."
            ),
        )

        return


    await query.answer(ok=True)


# ==================================================
# SUCCESSFUL PAYMENT
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


    if (
        payment.currency != "XTR"
        or payment.invoice_payload != PULSE_PLUS_PAYLOAD
        or payment.total_amount != PULSE_PLUS_PRICE
    ):

        logger.warning(
            "Unexpected payment data."
        )

        return


    # Activate Pulse Plus.
    context.user_data["pulse_plan"] = "PLUS"

    # Start Plus allowance fresh.
    context.user_data["questions_used"] = 0

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
        "Pulse Plus payment successful for Telegram user %s",
        update.effective_user.id
        if update.effective_user
        else "unknown",
    )


    await update.message.reply_text(
        "✅ PULSE PLUS ACTIVATED\n\n"

        "Your Telegram Stars payment was successful. 💚\n\n"

        "⭐ Plan: Pulse Plus\n"
        f"💬 Test allowance: {PLUS_QUESTION_LIMIT} questions\n"
        "📷 Image support\n"
        "🎙 Voice-note support\n"
        "🌐 English + Arabic\n"
        "📅 Renews every 30 days\n\n"

        "Thank you for supporting Pulse AI."
    )


# ==================================================
# TEXT HEALTH QUESTIONS
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


    # --------------------------------------------------
    # GROUPS:
    # respond ONLY when explicitly mentioned.
    # --------------------------------------------------

    if chat.type in ("group", "supergroup"):

        user_message = extract_group_mention(
            user_message,
            context,
        )

        if user_message is None:
            return


        if not user_message:

            await update.message.reply_text(
                "💓 Please mention me together with your "
                "health question."
            )

            return


    if not await check_question_access(
        update,
        context,
    ):
        return


    try:

        await update.message.chat.send_action(
            action="typing"
        )


        response = await client.responses.create(
            model=HEALTH_MODEL,
            instructions=SYSTEM_PROMPT,
            input=user_message,
            max_output_tokens=700,

            # Do not keep the Response object for later retrieval.
            store=False,
        )


        answer = response.output_text


        if not answer:

            answer = (
                "Sorry, I couldn't generate a response. "
                "Please try again."
            )


        await update.message.reply_text(answer)


        record_successful_question(context)


        await show_usage_after_answer(
            update,
            context,
        )


    except Exception as error:

        logger.exception(
            "Error processing text message: %s",
            error
        )

        await update.message.reply_text(
            "💓 Pulse AI is temporarily unable to answer. "
            "Please try again shortly."
        )


# ==================================================
# PHOTO / IMAGE HANDLER
# ==================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    if not update.message.photo:
        return


    caption = (
        update.message.caption
        or ""
    ).strip()


    chat = update.effective_chat


    # --------------------------------------------------
    # GROUPS:
    # photo must contain @Pulseaihealthbot in caption.
    # --------------------------------------------------

    if chat.type in ("group", "supergroup"):

        caption = extract_group_mention(
            caption,
            context,
        )

        if caption is None:
            return


    if not await check_question_access(
        update,
        context,
    ):
        return


    # Largest available Telegram photo.
    photo = update.message.photo[-1]


    if (
        photo.file_size
        and photo.file_size > MAX_MEDIA_BYTES
    ):

        await update.message.reply_text(
            "📷 This image is too large for the current "
            "Pulse AI testing version."
        )

        return


    try:

        await update.message.chat.send_action(
            action="typing"
        )


        telegram_file = await photo.get_file()


        image_bytes = await telegram_file.download_as_bytearray()


        image_base64 = base64.b64encode(
            bytes(image_bytes)
        ).decode("utf-8")


        image_data_url = (
            "data:image/jpeg;base64,"
            + image_base64
        )


        if caption:

            image_question = (
                "The user provided this explanation with the image:\n\n"
                f"{caption}\n\n"
                "Review the image together with the user's explanation "
                "and provide cautious general health guidance."
            )

        else:

            image_question = (
                "Please review this health-related image. "
                "Describe what is reasonably visible, explain possible "
                "concerns without making a definitive diagnosis, ask any "
                "important follow-up questions, and explain when in-person "
                "medical assessment may be appropriate."
            )


        response = await client.responses.create(
            model=VISION_MODEL,

            instructions=(
                SYSTEM_PROMPT
                + "\n\n"
                + VISION_PROMPT
            ),

            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": image_question,
                        },
                        {
                            "type": "input_image",
                            "image_url": image_data_url,
                            "detail": "high",
                        },
                    ],
                }
            ],

            max_output_tokens=850,

            store=False,
        )


        answer = response.output_text


        if not answer:

            answer = (
                "I couldn't reliably analyze this image. "
                "Please try another clear photo or seek "
                "professional medical assessment if you are concerned."
            )


        await update.message.reply_text(
            "📷 Pulse AI Image Review\n\n"
            + answer
        )


        record_successful_question(context)


        await show_usage_after_answer(
            update,
            context,
        )


    except Exception as error:

        logger.exception(
            "Error processing image: %s",
            error
        )

        await update.message.reply_text(
            "📷 Pulse AI couldn't process this image right now.\n\n"
            "Please try again with a clear photo."
        )


# ==================================================
# VOICE NOTE HANDLER
# ==================================================

async def handle_voice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    if not update.message:
        return

    voice = update.message.voice

    if not voice:
        return


    # Voice is currently private-chat only.
    if update.effective_chat.type != "private":

        await update.message.reply_text(
            "🎙 For privacy, please send medical voice notes "
            "directly to Pulse AI in a private chat."
        )

        return


    if not await check_question_access(
        update,
        context,
    ):
        return


    if voice.duration > VOICE_MAX_SECONDS:

        await update.message.reply_text(
            f"🎙 For this testing version, please keep voice "
            f"notes under {VOICE_MAX_SECONDS // 60} minutes."
        )

        return


    if (
        voice.file_size
        and voice.file_size > MAX_MEDIA_BYTES
    ):

        await update.message.reply_text(
            "🎙 This voice file is too large for the current "
            "testing version."
        )

        return


    temp_path = None


    try:

        await update.message.chat.send_action(
            action="typing"
        )


        telegram_file = await voice.get_file()


        with tempfile.NamedTemporaryFile(
            suffix=".ogg",
            delete=False,
        ) as temp_file:

            temp_path = temp_file.name


        await telegram_file.download_to_drive(
            temp_path
        )


        # --------------------------------------------------
        # SPEECH TO TEXT
        # --------------------------------------------------

        with open(
            temp_path,
            "rb",
        ) as audio_file:

            transcript = await client.audio.transcriptions.create(
                model=TRANSCRIPTION_MODEL,
                file=audio_file,
            )


        transcript_text = (
            transcript.text
            if transcript
            else ""
        )


        transcript_text = transcript_text.strip()


        if not transcript_text:

            await update.message.reply_text(
                "🎙 I couldn't clearly understand that voice note. "
                "Please try again or type your question."
            )

            return


        # --------------------------------------------------
        # HEALTH ANSWER
        # --------------------------------------------------

        response = await client.responses.create(
            model=HEALTH_MODEL,

            instructions=SYSTEM_PROMPT,

            input=(
                "The user sent the following voice note. "
                "Answer the health question naturally in the same "
                "language used by the user:\n\n"
                + transcript_text
            ),

            max_output_tokens=700,

            store=False,
        )


        answer = response.output_text


        if not answer:

            answer = (
                "Sorry, I understood the voice note but couldn't "
                "generate a health response. Please try again."
            )


        await update.message.reply_text(
            "🎙 Pulse AI Voice Reply\n\n"
            + answer
        )


        record_successful_question(context)


        await show_usage_after_answer(
            update,
            context,
        )


    except Exception as error:

        logger.exception(
            "Error processing voice note: %s",
            error
        )

        await update.message.reply_text(
            "🎙 Pulse AI couldn't process this voice note "
            "right now. Please try again or type your question."
        )


    finally:

        if temp_path:

            try:

                if os.path.exists(temp_path):
                    os.remove(temp_path)

            except Exception:

                pass


# ==================================================
# RUN PULSE AI
# ==================================================

def main():

    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .build()
    )


    private_only = filters.ChatType.PRIVATE


    # ==================================================
    # PRIVATE COMMANDS
    # ==================================================

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
            "usage",
            usage_command,
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
    # TELEGRAM STARS
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
    # IMAGES
    # ==================================================

    application.add_handler(
        MessageHandler(
            filters.PHOTO,
            handle_photo,
        )
    )


    # ==================================================
    # VOICE NOTES
    # ==================================================

    application.add_handler(
        MessageHandler(
            filters.VOICE,
            handle_voice,
        )
    )


    # ==================================================
    # NORMAL TEXT
    # ==================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )


    logger.info(
        "Pulse AI multimodal test version is running..."
    )


    application.run_polling(
        drop_pending_updates=True
    )


# ==================================================
# START
# ==================================================

if __name__ == "__main__":
    main()
