import os
import re
import math
import base64
import tempfile
import logging
import asyncio
from datetime import datetime, timezone, timedelta

import httpx

from telegram import (
    Update,
    LabeledPrice,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from openai import AsyncOpenAI

from config import (
    PROJECT_NAME,
    PROJECT_SUPPORT_EMAIL,
    PROJECT_X,
    PROJECT_TELEGRAM_SUPPORT,
    TELEGRAM_BOT_TOKEN,
    OPENAI_API_KEY,
    DATABASE_URL,
    GOOGLE_MAPS_API_KEY,
    FREE_QUESTION_LIMIT,
    PLUS_QUESTION_LIMIT,
    PULSE_PLUS_PRICE,
    PULSE_PLUS_PAYLOAD,
    PULSE_SUBSCRIPTION_PERIOD,
    HEALTH_MODEL,
    VISION_MODEL,
    TRANSCRIPTION_MODEL,
    PRIVACY_POLICY_URL,
)
from database import db


# ==================================================
# CLIENTS / LIMITS
# ==================================================

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

VOICE_MAX_SECONDS = 120
MAX_MEDIA_BYTES = 18 * 1024 * 1024
MAX_CARE_RESULTS = 5
CARE_SEARCH_RADIUS_METERS = 15000.0

GOOGLE_PLACES_TEXT_SEARCH_URL = (
    "https://places.googleapis.com/v1/places:searchText"
)

GOOGLE_FIELD_MASK = ",".join(
    [
        "places.id",
        "places.displayName",
        "places.formattedAddress",
        "places.location",
        "places.googleMapsUri",
        "places.websiteUri",
        "places.internationalPhoneNumber",
        "places.nationalPhoneNumber",
        "places.rating",
        "places.userRatingCount",
        "places.currentOpeningHours",
        "places.businessStatus",
        "places.primaryTypeDisplayName",
    ]
)


# ==================================================
# MEDICATION REMINDER CONVERSATION STATES
# ==================================================

(
    MED_NAME,
    MED_STRENGTH,
    MED_SOURCE,
    MED_INTERVAL,
    MED_INTERVAL_CUSTOM,
    MED_START,
    MED_DURATION,
    MED_CONFIRM,
) = range(100, 108)

REMINDER_POLL_SECONDS = 30



# ==================================================
# LOGGING
# ==================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ==================================================
# HEALTH / SAFETY PROMPTS
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
- Be warm, calm, respectful, practical, and concise.
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
- When appropriate, encourage consultation with a qualified healthcare
  professional or pharmacist.

EMERGENCIES:
Always prioritize urgent medical care when symptoms could represent
an emergency, including severe difficulty breathing, severe/crushing
chest pain, loss of consciousness, seizure, signs of stroke, severe
allergic reaction, uncontrolled bleeding, severe dehydration, major
confusion, serious trauma, suicidal thoughts, or immediate danger.

If warning signs may be present, clearly tell the user to seek emergency
medical care immediately. Do not let a usage limit or payment prompt
delay emergency care.

CHILDREN:
Be especially cautious with babies and children. Consider age, weight
when medication is discussed, temperature, hydration, breathing,
alertness, feeding, urine output, duration, pain, and red flags.

Never present Pulse AI as a replacement for emergency services,
doctors, pharmacists, hospitals, or other qualified professionals.
"""

VISION_PROMPT = """
The user has provided a health-related image.

- Describe only what is reasonably visible.
- Do not diagnose with certainty from an image alone.
- Explain the limitations of visual assessment.
- For wounds, do not claim exact depth or say with certainty whether
  stitches are required; discuss visible concern such as gaping edges,
  bleeding, contamination, swelling, discoloration, infection signs,
  and location over important structures.
- For medication packaging, read only clearly visible label information.
  Never identify an unknown pill/tablet with confidence from shape/color
  alone. Ask the user to confirm the exact medicine name and strength
  printed on the packaging.
- Recommend in-person care promptly when appropriate.
- If emergency warning signs are visible or described, advise immediate
  emergency care.
- Answer in the user's language when possible.
"""

MEDICATION_PROMPT = """
You are Pulse AI Medication Guidance.

Your role is to provide cautious, general medication information and
support safe next steps.

CORE RULES:
- You are not prescribing medication.
- Do not claim that a medicine will cure the user's condition.
- Prefer common OTC symptom-relief information only when appropriate.
- Do not recommend starting prescription-only medicines, antibiotics,
  prescription steroids, controlled medicines, sedatives, or changing
  an existing prescription without professional review.
- Never invent a treatment schedule.

BEFORE SPECIFIC DOSING:
Consider whether you need:
- age
- weight, especially for children
- pregnancy or breastfeeding status
- medication allergies
- liver disease
- kidney disease
- stomach ulcer or gastrointestinal bleeding history
- blood thinners
- other regular medicines
- exact medicine name
- exact active ingredient
- exact strength/concentration

CHILDREN:
- Do not guess pediatric dosing.
- Before discussing a pediatric dose, require age, current weight,
  exact medicine name, and exact strength/concentration.
- Encourage confirmation with a pharmacist or clinician.

MEDICINE PHOTOS:
- Identification is based only on what is clearly visible.
- Ask the user to verify the exact medicine name and strength printed
  on the original packaging.
- Do not identify an unknown loose tablet from appearance alone.
- Do not create a medication reminder until the user confirms the
  medicine and the schedule.

DOSING:
- If discussing an OTC medicine, explain that the user should follow the
  package label and confirm suitability with a pharmacist or doctor if
  uncertain.
- If required safety information is missing, ask for it instead of guessing.
- When a standard OTC label dose is appropriate and the relevant details
  are known, explain it cautiously together with important contraindications
  and maximum label limits.

REMINDERS:
- Pulse AI may remind the user only of a schedule the user explicitly
  confirms came from their doctor, pharmacist, or the medicine label.
- A reminder is not a prescription, authorization, or treatment decision.

SAFETY:
- If symptoms suggest an emergency or require professional assessment,
  prioritize that over medication suggestions.
- Encourage pharmacist or doctor confirmation when there are medical
  conditions, allergies, pregnancy/breastfeeding, other medicines,
  uncertain dosing, or a child is being treated.

Reply in the user's language.
"""


# ==================================================
# CARE FINDER DETECTION
# ==================================================

CARE_TERMS = [
    "hospital", "clinic", "doctor", "dentist", "pediatrician",
    "paediatrician", "pharmacy", "medical center", "medical centre",
    "health center", "health centre", "urgent care", "emergency room",
    "specialist", "dermatologist", "cardiologist", "orthopedic",
    "orthopaedic", "ent", "ophthalmologist", "gynecologist",
    "gynaecologist", "psychiatrist", "psychologist", "physiotherapist",
    "physio", "surgeon", "مستشفى", "مستشفيات", "عيادة", "عيادات",
    "طبيب", "طبيبة", "دكتور", "دكتورة", "أسنان", "اسنان", "صيدلية",
    "صيدليات", "مركز صحي", "مركز طبي", "طوارئ", "أخصائي", "اخصائي",
    "اختصاصي", "أطفال", "اطفال", "جلدية", "قلب", "عظام", "أنف",
    "انف", "أذن", "اذن", "حنجرة", "عيون", "نساء", "ولادة", "نفسي",
    "علاج طبيعي",
]

CARE_SEARCH_TERMS = [
    "find", "looking for", "i need", "need a", "need an", "nearest",
    "near me", "nearby", "closest", "recommend", "where is", "open now",
    "around me", "search for", "ابحث", "أبحث", "ابي", "أبي", "احتاج",
    "أحتاج", "اريد", "أريد", "اقرب", "أقرب", "قريب مني", "بالقرب مني",
    "وين", "رشح", "أفضل", "افضل", "دور لي",
]

NEAR_ME_TERMS = [
    "near me", "nearest", "nearby", "closest", "around me",
    "قريب مني", "بالقرب مني", "أقرب", "اقرب",
]

MEDICATION_TERMS = [
    # English
    "medicine", "medication", "tablet", "tablets", "pill", "pills",
    "capsule", "capsules", "painkiller", "pain killer", "dose", "dosage",
    "what can i take", "what should i take", "can i take",
    "how many tablets", "how many pills", "paracetamol", "acetaminophen",
    "ibuprofen", "antihistamine", "cough syrup", "pharmacist", "pharmacy",

    # Arabic
    "دواء", "أدوية", "ادوية", "دوائي", "حبة", "حبوب", "قرص", "أقراص",
    "اقراص", "كبسولة", "كبسولات", "مسكن", "باراسيتامول", "بنادول",
    "ايبوبروفين", "إيبوبروفين", "جرعة", "كم حبة", "كم قرص",
    "ماذا آخذ", "ماذا اخذ", "شنو آخذ", "شنو اخذ", "وش آخذ", "صيدلية",
    "صيدلي",
]


def contains_arabic(text: str) -> bool:
    return bool(re.search(r"[\u0600-\u06FF]", text or ""))


def looks_like_care_search(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    has_care = any(term.lower() in lowered for term in CARE_TERMS)
    if not has_care:
        return False
    has_search = any(term.lower() in lowered for term in CARE_SEARCH_TERMS)
    has_location = " in " in lowered or " في " in text
    return has_search or has_location


def asks_near_me(text: str) -> bool:
    lowered = (text or "").lower()
    return any(term.lower() in lowered for term in NEAR_ME_TERMS)


def has_named_location(text: str) -> bool:
    if not text:
        return False
    if re.search(r"\bin\s+[a-zA-Z]", text, re.IGNORECASE):
        return True
    return " في " in text


def looks_like_medication_question(text: str) -> bool:
    lowered = (text or "").lower()
    return any(term.lower() in lowered for term in MEDICATION_TERMS)


def clean_care_query(text: str) -> str:
    query = text.strip()
    replacements = [
        "find me", "find a", "find an", "i need a", "i need an", "i need",
        "looking for", "please find", "can you find", "could you find",
        "ابحث لي عن", "أبحث لي عن", "دور لي على", "أبي", "ابي", "أحتاج",
        "احتاج", "أريد", "اريد",
    ]
    for phrase in replacements:
        query = re.sub(re.escape(phrase), "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+", " ", query).strip(" ,.-")
    return query or text.strip()


def extract_group_mention(raw_text: str, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot.username or "Pulseaihealthbot"
    mention = f"@{bot_username}"
    pattern = re.compile(rf"(?<!\w){re.escape(mention)}(?!\w)", re.IGNORECASE)
    if not pattern.search(raw_text):
        return None
    return pattern.sub("", raw_text).strip()


# ==================================================
# DATABASE / PLAN HELPERS
# ==================================================

async def get_plan_and_usage(user_id: int):
    plan = await db.get_plan(user_id)
    user = await db.get_user(user_id)
    limit = PLUS_QUESTION_LIMIT if plan == "PLUS" else FREE_QUESTION_LIMIT
    used = int(user["questions_used"])
    return plan, used, limit


async def create_plus_invoice(context: ContextTypes.DEFAULT_TYPE):
    return await context.bot.create_invoice_link(
        title="Pulse Plus",
        description="Pulse Plus membership with increased Pulse AI access. Renews every 30 days.",
        payload=PULSE_PLUS_PAYLOAD,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label="Pulse Plus - 30 days", amount=PULSE_PLUS_PRICE)],
        subscription_period=PULSE_SUBSCRIPTION_PERIOD,
    )


async def send_upgrade_offer(update: Update, context: ContextTypes.DEFAULT_TYPE, limit_reached=False):
    try:
        invoice_link = await create_plus_invoice(context)
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton(f"⭐ Pulse Plus — {PULSE_PLUS_PRICE} Stars", url=invoice_link)]]
        )
    except Exception as exc:
        logger.exception("Could not create invoice: %s", exc)
        markup = None

    if limit_reached:
        message = (
            f"💚 You've used your {FREE_QUESTION_LIMIT} free Pulse AI questions.\n\n"
            "Thank you for helping us test Pulse AI.\n\n"
            "Upgrade to Pulse Plus to continue.\n\n"
            f"⭐ {PULSE_PLUS_PRICE} Telegram Stars\n"
            "📅 Renews every 30 days\n"
            f"💬 Up to {PLUS_QUESTION_LIMIT} questions\n"
            "📷 Image support\n🎙 Voice support\n📍 Care Finder\n💊 Medication guidance\n"
            "🌐 English + Arabic\n\n"
            "⚠️ Never delay emergency medical care because of a Pulse AI usage limit."
        )
    else:
        message = (
            "💚 PULSE PLUS\n\n"
            f"⭐ {PULSE_PLUS_PRICE} Telegram Stars\n"
            "📅 Renews every 30 days\n"
            f"💬 Up to {PLUS_QUESTION_LIMIT} questions\n"
            "📷 Image analysis\n🎙 Voice questions\n📍 Care Finder\n💊 Medication guidance\n"
            "🌐 English + Arabic\n\n"
            "Pulse AI provides general health information and is not a doctor."
        )

    await update.effective_message.reply_text(message, reply_markup=markup)


async def check_question_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
    plan, used, limit = await get_plan_and_usage(user.id)
    if used < limit:
        return True
    if plan == "FREE":
        await send_upgrade_offer(update, context, limit_reached=True)
    else:
        await update.effective_message.reply_text(
            "💚 You've reached the current Pulse Plus testing allowance."
        )
    return False


async def record_and_show_usage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    used = await db.increment_usage(user.id)
    plan = await db.get_plan(user.id)
    limit = PLUS_QUESTION_LIMIT if plan == "PLUS" else FREE_QUESTION_LIMIT
    remaining = max(limit - used, 0)

    if plan == "FREE" and remaining == 0:
        await send_upgrade_offer(update, context, limit_reached=True)
    else:
        prefix = "⭐ Pulse Plus" if plan == "PLUS" else "🧪 Pulse AI Test"
        await update.effective_message.reply_text(
            f"{prefix}: {used}/{limit} used • {remaining} remaining"
        )


# ==================================================
# MEDICATION GUIDANCE + PHARMACY ACTIONS
# ==================================================

def medication_action_keyboard(arabic: bool = False):
    if arabic:
        pharmacy_text = "💊 ابحث عن صيدلية قريبة"
        reminder_text = "⏰ إعداد تذكير للدواء"
    else:
        pharmacy_text = "💊 Find nearby pharmacy"
        reminder_text = "⏰ Set medication reminder"

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    pharmacy_text,
                    callback_data="med_find_pharmacy",
                )
            ],
            [
                InlineKeyboardButton(
                    reminder_text,
                    callback_data="med_set_reminder",
                )
            ],
        ]
    )


async def handle_medication_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user_message: str,
):
    try:
        await update.effective_message.chat.send_action(action="typing")

        response = await client.responses.create(
            model=HEALTH_MODEL,
            instructions=SYSTEM_PROMPT + "\n\n" + MEDICATION_PROMPT,
            input=user_message,
            max_output_tokens=900,
            store=False,
        )

        answer = (
            response.output_text
            or "I couldn't generate reliable medication guidance for that question. "
               "Please confirm with a pharmacist or doctor."
        )

        arabic = contains_arabic(user_message)

        if arabic:
            footer = (
                "\n\n💊 سلامة الدواء\n"
                "هذه معلومات عامة وليست وصفة طبية أو علاجاً مؤكداً. "
                "اتبع تعليمات العبوة، وتأكد من الصيدلي أو الطبيب إذا كنت غير متأكد، "
                "أو لديك حساسية أو أمراض مزمنة أو تستخدم أدوية أخرى، أو في حالة الحمل "
                "والرضاعة، أو عند إعطاء الدواء لطفل."
            )
        else:
            footer = (
                "\n\n💊 Medication Safety\n"
                "This is general information, not a prescription or a cure. "
                "Follow the medicine label and confirm the medicine and dose with a "
                "pharmacist or doctor if you are unsure, have allergies, take other "
                "medicines, are pregnant/breastfeeding, have medical conditions, "
                "or are treating a child."
            )

        await update.effective_message.reply_text(
            answer + footer,
            reply_markup=medication_action_keyboard(arabic),
        )

        await record_and_show_usage(update, context)

    except Exception as exc:
        logger.exception("Medication guidance error: %s", exc)
        await update.effective_message.reply_text(
            "💊 Pulse AI couldn't complete the medication guidance right now. "
            "Please try again or confirm with a pharmacist."
        )


async def handle_med_find_pharmacy(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if query:
        await query.answer()

    if update.effective_chat.type != "private":
        await send_private_care_link(update, context)
        return

    search_text = (
        "صيدلية قريبة مني"
        if contains_arabic((query.message.text if query and query.message else "") or "")
        else "pharmacy near me"
    )

    await perform_care_search(
        update,
        context,
        search_text,
    )


# ==================================================
# MEDICATION REMINDER SETUP
# ==================================================

async def start_medication_reminder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if update.callback_query:
        await update.callback_query.answer()

    if update.effective_chat.type != "private":
        bot_username = context.bot.username or "Pulseaihealthbot"
        markup = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "🔒 Open private reminder setup",
                    url=f"https://t.me/{bot_username}?start=reminder",
                )
            ]]
        )
        await update.effective_message.reply_text(
            "🔒 Medication reminders are set up in private chat.",
            reply_markup=markup,
        )
        return ConversationHandler.END

    context.user_data["med_reminder_draft"] = {}

    await update.effective_message.reply_text(
        "⏰ Medication Reminder Setup\n\n"
        "Type the exact medicine name shown on the original package or confirmed "
        "by your doctor/pharmacist.\n\n"
        "Example: Paracetamol\n\n"
        "Send /cancel to stop setup."
    )
    return MED_NAME


async def med_name_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text("Please type the exact medicine name.")
        return MED_NAME

    context.user_data["med_reminder_draft"]["medicine_name"] = name

    await update.message.reply_text(
        "Now type the exact strength/concentration shown on the package.\n\n"
        "Examples:\n"
        "• 500 mg\n"
        "• 250 mg/5 mL\n"
        "• 10 mg\n\n"
        "If strength does not apply, type: N/A"
    )
    return MED_STRENGTH


async def med_strength_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    strength = (update.message.text or "").strip()
    if not strength:
        await update.message.reply_text("Please enter the strength or type N/A.")
        return MED_STRENGTH

    context.user_data["med_reminder_draft"]["medicine_strength"] = strength

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Yes — confirmed",
                    callback_data="med_source_yes",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ No / not sure",
                    callback_data="med_source_no",
                )
            ],
        ]
    )

    await update.message.reply_text(
        "Safety check:\n\n"
        "Was the dosing schedule you want me to remind you about given by your "
        "doctor, pharmacist, or written on the medicine label?\n\n"
        "Pulse AI will not invent a medication schedule.",
        reply_markup=markup,
    )
    return MED_SOURCE


async def med_source_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if query.data == "med_source_no":
        context.user_data.pop("med_reminder_draft", None)
        await query.message.reply_text(
            "No reminder was created.\n\n"
            "Please confirm the medicine schedule with a pharmacist, doctor, or "
            "the medicine label first. Then you can use /remindmedicine again."
        )
        return ConversationHandler.END

    context.user_data["med_reminder_draft"]["source_confirmed"] = True

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Every 6 hours", callback_data="med_interval_6"),
                InlineKeyboardButton("Every 8 hours", callback_data="med_interval_8"),
            ],
            [
                InlineKeyboardButton("Every 12 hours", callback_data="med_interval_12"),
                InlineKeyboardButton("Once daily", callback_data="med_interval_24"),
            ],
            [
                InlineKeyboardButton("Custom interval", callback_data="med_interval_custom"),
            ],
        ]
    )

    await query.message.reply_text(
        "Select the schedule you were actually given.\n\n"
        "Do not choose a schedule based on what seems convenient.",
        reply_markup=markup,
    )
    return MED_INTERVAL


async def med_interval_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    value = query.data.replace("med_interval_", "")

    if value == "custom":
        await query.message.reply_text(
            "Type the number of hours between doses exactly as you were instructed.\n\n"
            "Example: 4\n\n"
            "For schedules that are not a regular hourly interval, please confirm "
            "with a pharmacist before using this beta reminder."
        )
        return MED_INTERVAL_CUSTOM

    interval_hours = float(value)
    draft = context.user_data["med_reminder_draft"]
    draft["interval_hours"] = interval_hours
    draft["schedule_text"] = (
        "Once daily" if interval_hours == 24 else f"Every {int(interval_hours)} hours"
    )

    return await ask_med_start(query.message, context)


async def med_interval_custom_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    raw = (update.message.text or "").strip()

    try:
        interval_hours = float(raw)
    except ValueError:
        await update.message.reply_text(
            "Please enter only the number of hours, for example: 4"
        )
        return MED_INTERVAL_CUSTOM

    if interval_hours < 1 or interval_hours > 168:
        await update.message.reply_text(
            "For this beta reminder, enter an interval between 1 and 168 hours."
        )
        return MED_INTERVAL_CUSTOM

    draft = context.user_data["med_reminder_draft"]
    draft["interval_hours"] = interval_hours
    draft["schedule_text"] = f"Every {interval_hours:g} hours"

    return await ask_med_start(update.message, context)


async def ask_med_start(
    message,
    context: ContextTypes.DEFAULT_TYPE,
):
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ I just took the dose",
                    callback_data="med_start_taken_now",
                )
            ],
            [
                InlineKeyboardButton(
                    "⏰ Dose is due now",
                    callback_data="med_start_due_now",
                )
            ],
        ]
    )

    await message.reply_text(
        "When should the reminder cycle start?\n\n"
        "For this beta, reminders use a relative interval from the time you confirm "
        "the schedule, so no timezone is required.",
        reply_markup=markup,
    )
    return MED_START


async def med_start_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    draft = context.user_data["med_reminder_draft"]
    interval = float(draft["interval_hours"])
    now = datetime.now(timezone.utc)

    if query.data == "med_start_taken_now":
        draft["next_due_at"] = now + timedelta(hours=interval)
        draft["start_text"] = f"Next reminder in {interval:g} hours"
    else:
        draft["next_due_at"] = now
        draft["start_text"] = "First reminder is due now"

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("1 day", callback_data="med_duration_1"),
                InlineKeyboardButton("3 days", callback_data="med_duration_3"),
                InlineKeyboardButton("5 days", callback_data="med_duration_5"),
            ],
            [
                InlineKeyboardButton("7 days", callback_data="med_duration_7"),
                InlineKeyboardButton("14 days", callback_data="med_duration_14"),
                InlineKeyboardButton("30 days", callback_data="med_duration_30"),
            ],
            [
                InlineKeyboardButton(
                    "Until I stop it",
                    callback_data="med_duration_open",
                )
            ],
        ]
    )

    await query.message.reply_text(
        "How long should Pulse keep sending this reminder?\n\n"
        "Choose the duration that matches the instructions you were given.",
        reply_markup=markup,
    )
    return MED_DURATION


async def med_duration_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    draft = context.user_data["med_reminder_draft"]
    value = query.data.replace("med_duration_", "")

    if value == "open":
        draft["end_at"] = None
        draft["duration_text"] = "Until you stop it"
    else:
        days = int(value)
        draft["end_at"] = datetime.now(timezone.utc) + timedelta(days=days)
        draft["duration_text"] = f"{days} day" if days == 1 else f"{days} days"

    name = draft["medicine_name"]
    strength = draft["medicine_strength"]
    schedule = draft["schedule_text"]
    start_text = draft["start_text"]
    duration_text = draft["duration_text"]

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Confirm reminder",
                    callback_data="med_confirm_yes",
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel",
                    callback_data="med_confirm_no",
                )
            ],
        ]
    )

    await query.message.reply_text(
        "⏰ Confirm Medication Reminder\n\n"
        f"Medicine: {name}\n"
        f"Strength: {strength}\n"
        f"Schedule: {schedule}\n"
        f"Start: {start_text}\n"
        f"Duration: {duration_text}\n\n"
        "⚠️ Pulse AI is only reminding you of the schedule you confirmed came "
        "from your doctor, pharmacist, or medicine label. It is not prescribing "
        "or changing your treatment.",
        reply_markup=markup,
    )
    return MED_CONFIRM


async def med_confirm_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    if query.data == "med_confirm_no":
        context.user_data.pop("med_reminder_draft", None)
        await query.message.reply_text("❌ Medication reminder cancelled.")
        return ConversationHandler.END

    user = update.effective_user
    draft = context.user_data.get("med_reminder_draft", {})

    if not user or not draft.get("source_confirmed"):
        await query.message.reply_text(
            "I couldn't verify the reminder setup. Please start again with /remindmedicine."
        )
        context.user_data.pop("med_reminder_draft", None)
        return ConversationHandler.END

    reminder_id = await db.create_medication_reminder(
        telegram_user_id=user.id,
        medicine_name=draft["medicine_name"],
        medicine_strength=draft["medicine_strength"],
        schedule_text=draft["schedule_text"],
        next_due_at=draft["next_due_at"],
        interval_hours=draft["interval_hours"],
        end_at=draft["end_at"],
    )

    await query.message.reply_text(
        "✅ Medication reminder saved.\n\n"
        f"Reminder ID: {reminder_id}\n"
        f"Medicine: {draft['medicine_name']} {draft['medicine_strength']}\n"
        f"Schedule: {draft['schedule_text']}\n\n"
        "Use /reminders to view active reminders.\n\n"
        "⚠️ Continue following the instructions from your doctor, pharmacist, "
        "or medicine label."
    )

    context.user_data.pop("med_reminder_draft", None)
    return ConversationHandler.END


async def cancel_medication_reminder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    context.user_data.pop("med_reminder_draft", None)
    await update.effective_message.reply_text("❌ Medication reminder setup cancelled.")
    return ConversationHandler.END


async def reminders_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    if not user:
        return

    reminders = await db.list_active_reminders(user.id)

    if not reminders:
        await update.message.reply_text(
            "⏰ You don't have any active medication reminders.\n\n"
            "Use /remindmedicine to create one."
        )
        return

    await update.message.reply_text(
        f"⏰ Active Medication Reminders: {len(reminders)}"
    )

    for reminder in reminders:
        strength = reminder["medicine_strength"] or ""
        text = (
            f"💊 {reminder['medicine_name']} {strength}\n"
            f"Schedule: {reminder['schedule_text']}\n"
            f"Reminder ID: {reminder['id']}"
        )
        markup = InlineKeyboardMarkup(
            [[
                InlineKeyboardButton(
                    "🛑 Stop reminder",
                    callback_data=f"rem_stop:{reminder['id']}",
                )
            ]]
        )
        await update.message.reply_text(text, reply_markup=markup)


async def _owns_active_reminder(user_id: int, reminder_id: int) -> bool:
    reminders = await db.list_active_reminders(user_id)
    return any(int(row["id"]) == reminder_id for row in reminders)


async def handle_reminder_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user:
        return

    try:
        action, raw_id = query.data.split(":", 1)
        reminder_id = int(raw_id)
    except (ValueError, AttributeError):
        return

    if not await _owns_active_reminder(user.id, reminder_id):
        await query.message.reply_text(
            "This reminder is no longer active or does not belong to this account."
        )
        return

    if action == "rem_taken":
        await db.mark_reminder_taken(reminder_id)
        await query.message.reply_text("✅ Marked as taken.")

    elif action == "rem_snooze":
        await db.advance_reminder(
            reminder_id,
            datetime.now(timezone.utc) + timedelta(minutes=15),
        )
        await query.message.reply_text("⏰ Snoozed for 15 minutes.")

    elif action == "rem_stop":
        await db.stop_reminder(reminder_id, user.id)
        await query.message.reply_text("🛑 Medication reminder stopped.")


async def medication_reminder_worker(application: Application):
    while True:
        try:
            due_reminders = await db.get_due_reminders(limit=100)
            now = datetime.now(timezone.utc)

            for reminder in due_reminders:
                reminder_id = int(reminder["id"])
                user_id = int(reminder["telegram_user_id"])
                medicine = reminder["medicine_name"]
                strength = reminder["medicine_strength"] or ""
                schedule = reminder["schedule_text"]
                interval = float(reminder["interval_hours"] or 0)
                end_at = reminder["end_at"]

                markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "✅ Taken",
                                callback_data=f"rem_taken:{reminder_id}",
                            ),
                            InlineKeyboardButton(
                                "⏰ Snooze 15 min",
                                callback_data=f"rem_snooze:{reminder_id}",
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                "🛑 Stop reminder",
                                callback_data=f"rem_stop:{reminder_id}",
                            )
                        ],
                    ]
                )

                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "💊 Pulse Medication Reminder\n\n"
                            f"It's time for your confirmed medication:\n"
                            f"{medicine} {strength}\n\n"
                            f"Confirmed schedule: {schedule}\n\n"
                            "Follow the instructions given by your doctor, pharmacist, "
                            "or medicine label.\n\n"
                            "⚠️ Pulse AI is reminding you of the schedule you confirmed; "
                            "it is not prescribing or changing your medication."
                        ),
                        reply_markup=markup,
                    )

                    if interval <= 0:
                        await db.stop_reminder(reminder_id, user_id)
                        continue

                    next_due = reminder["next_due_at"] + timedelta(hours=interval)
                    while next_due <= now:
                        next_due += timedelta(hours=interval)

                    if end_at is not None and next_due > end_at:
                        await db.stop_reminder(reminder_id, user_id)
                    else:
                        await db.advance_reminder(reminder_id, next_due)

                except Exception as exc:
                    logger.exception(
                        "Could not deliver medication reminder %s: %s",
                        reminder_id,
                        exc,
                    )
                    # Avoid retrying every 30 seconds after a transient delivery error.
                    await db.advance_reminder(
                        reminder_id,
                        datetime.now(timezone.utc) + timedelta(minutes=5),
                    )

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Medication reminder worker error: %s", exc)

        await asyncio.sleep(REMINDER_POLL_SECONDS)



# ==================================================
# CARE FINDER
# ==================================================

def haversine_km(lat1, lon1, lat2, lon2):
    radius = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


async def google_places_search(query, latitude=None, longitude=None):
    if not GOOGLE_MAPS_API_KEY:
        raise RuntimeError("GOOGLE_MAPS_API_KEY is missing.")

    body = {
        "textQuery": clean_care_query(query),
        "maxResultCount": MAX_CARE_RESULTS,
        "languageCode": "ar" if contains_arabic(query) else "en",
    }
    if latitude is not None and longitude is not None:
        body["locationBias"] = {
            "circle": {
                "center": {"latitude": latitude, "longitude": longitude},
                "radius": CARE_SEARCH_RADIUS_METERS,
            }
        }

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": GOOGLE_FIELD_MASK,
    }

    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.post(GOOGLE_PLACES_TEXT_SEARCH_URL, headers=headers, json=body)
        if response.status_code != 200:
            logger.error("Google Places error %s", response.status_code)
            raise RuntimeError("Google Places search failed.")
        data = response.json()

    places = [
        p for p in data.get("places", [])
        if p.get("businessStatus") != "CLOSED_PERMANENTLY"
    ]

    if latitude is not None and longitude is not None:
        for place in places:
            loc = place.get("location", {})
            if loc.get("latitude") is not None and loc.get("longitude") is not None:
                place["_distance_km"] = haversine_km(
                    latitude, longitude, loc["latitude"], loc["longitude"]
                )
        places.sort(key=lambda p: p.get("_distance_km", 99999))

    return places[:MAX_CARE_RESULTS]


async def verify_places_on_web(places, original_query):
    if not places:
        return None

    candidates = []
    for i, place in enumerate(places[:3], 1):
        name = place.get("displayName", {}).get("text", "Unknown")
        address = place.get("formattedAddress", "")
        website = place.get("websiteUri", "")
        candidates.append(
            f"{i}. {name}\nAddress: {address}\nKnown website: {website or 'none'}"
        )

    prompt = f"""
You are verifying healthcare facility contact information for Pulse AI Care Finder.

User request:
{original_query}

Facilities:
{chr(10).join(candidates)}

Use web search. Prefer official facility websites and government directories.
Do not invent an email address or infer an email pattern.
If no official public email can be verified, say "Email not publicly verified."
Briefly confirm whether the requested specialty/service appears to be offered when reliable evidence exists.
Do not call any provider medically "the best".
Provide official source URLs when possible.
Return a concise numbered list matching facility numbers 1-3.
"""

    try:
        response = await client.responses.create(
            model=HEALTH_MODEL,
            tools=[{"type": "web_search"}],
            input=prompt,
            max_output_tokens=650,
            store=False,
        )
        return response.output_text or None
    except Exception as exc:
        logger.warning("Care Finder web verification unavailable: %s", exc)
        return None


def format_care_results(places, query):
    arabic = contains_arabic(query)
    lines = [
        "📍 نتائج Pulse Care Finder" if arabic else "📍 Pulse Care Finder",
        "",
        "هذه خيارات تطابق بحثك بناءً على المعلومات العامة المتوفرة حالياً."
        if arabic else
        "These options currently match your search based on publicly available information.",
        "",
    ]
    buttons = []

    for i, place in enumerate(places, 1):
        name = place.get("displayName", {}).get("text", "Unknown facility")
        kind = place.get("primaryTypeDisplayName", {}).get("text", "")
        address = place.get("formattedAddress", "Address not listed")
        phone = (
            place.get("internationalPhoneNumber")
            or place.get("nationalPhoneNumber")
            or ("غير متوفر" if arabic else "Not publicly listed")
        )
        rating = place.get("rating")
        rating_count = place.get("userRatingCount")
        open_now = place.get("currentOpeningHours", {}).get("openNow")
        distance = place.get("_distance_km")

        lines.append(f"{i}. {name}")
        if kind:
            lines.append(f"🏥 {kind}")
        if distance is not None:
            lines.append(f"📏 {distance:.1f} km")
        lines.append(f"📍 {address}")
        lines.append(f"☎️ {phone}")

        if open_now is True:
            lines.append("🟢 مفتوح الآن" if arabic else "🟢 Open now")
        elif open_now is False:
            lines.append("🔴 مغلق الآن" if arabic else "🔴 Closed now")
        else:
            lines.append("🕐 ساعات العمل غير مؤكدة" if arabic else "🕐 Current hours not confirmed")

        if rating is not None:
            rating_line = f"⭐ {rating}"
            if rating_count:
                rating_line += f" ({rating_count} reviews)"
            lines.append(rating_line)
        lines.append("")

        row = []
        if place.get("googleMapsUri"):
            row.append(
                InlineKeyboardButton(
                    f"🗺 {'خريطة' if arabic else 'Map'} {i}",
                    url=place["googleMapsUri"],
                )
            )
        if place.get("websiteUri"):
            row.append(
                InlineKeyboardButton(
                    f"🌐 {'موقع' if arabic else 'Website'} {i}",
                    url=place["websiteUri"],
                )
            )
        if row:
            buttons.append(row)

    lines.extend(
        [
            "ℹ️ الترتيب ليس تصنيفاً طبياً للأفضلية. تحقق من الجهة مباشرة قبل الذهاب."
            if arabic else
            "ℹ️ This is not a medical ranking of who is 'best'. Confirm services and availability directly before travelling."
        ]
    )
    return "\n".join(lines), InlineKeyboardMarkup(buttons) if buttons else None


async def request_user_location(update: Update, context: ContextTypes.DEFAULT_TYPE, care_query=None):
    if care_query:
        context.user_data["pending_care_query"] = care_query
        context.user_data["awaiting_care_location"] = True

    keyboard = [[KeyboardButton("📍 Share My Location", request_location=True)]]
    await update.effective_message.reply_text(
        "📍 To find healthcare options near you, share your current location.\n\n"
        "Your location is used for the nearby search. You can clear it later with /forgetlocation.\n\n"
        "Or type a city/country, for example:\nPediatrician in Doha\nDentist in London\nمستشفى أطفال في دبي",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True),
    )


async def send_private_care_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot.username or "Pulseaihealthbot"
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📍 Open Pulse Care Finder", url=f"https://t.me/{bot_username}?start=care")]]
    )
    await update.effective_message.reply_text(
        "📍 For an accurate nearby healthcare search, please continue privately with Pulse AI.",
        reply_markup=markup,
    )


async def perform_care_search(update: Update, context: ContextTypes.DEFAULT_TYPE, query):
    if not await check_question_access(update, context):
        return

    if not GOOGLE_MAPS_API_KEY:
        await update.effective_message.reply_text(
            "📍 Pulse Care Finder is ready in the code, but the Google Maps/Places API key has not been added to Railway yet."
        )
        return

    user = update.effective_user
    if not user:
        return

    explicit_location = has_named_location(query)
    saved_location = None if explicit_location else await db.get_location(user.id)
    latitude = saved_location["latitude"] if saved_location else None
    longitude = saved_location["longitude"] if saved_location else None

    if asks_near_me(query) and not saved_location:
        if update.effective_chat.type == "private":
            await request_user_location(update, context, care_query=query)
        else:
            await send_private_care_link(update, context)
        return

    if not explicit_location and not saved_location:
        if update.effective_chat.type == "private":
            await request_user_location(update, context, care_query=query)
        else:
            await update.effective_message.reply_text(
                "📍 Please include a city/country, for example: @Pulseaihealthbot pediatrician in Doha"
            )
        return

    try:
        await update.effective_message.chat.send_action(action="typing")
        places = await google_places_search(query, latitude, longitude)
        if not places:
            await update.effective_message.reply_text(
                "📍 I couldn't find a strong healthcare match. Try another specialty, city, or search area."
            )
            return

        text, markup = format_care_results(places, query)
        await update.effective_message.reply_text(text, reply_markup=markup)

        verification = await verify_places_on_web(places, query)
        if verification:
            await update.effective_message.reply_text(
                "🔎 Official-web contact check\n\n"
                + verification
                + "\n\nEmails are shown only when publicly verified; Pulse AI will not guess them."
            )

        await record_and_show_usage(update, context)
    except Exception as exc:
        logger.exception("Care Finder error: %s", exc)
        await update.effective_message.reply_text(
            "📍 Pulse Care Finder couldn't complete the search right now. Please try again shortly."
        )


# ==================================================
# COMMANDS
# ==================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        mode = context.args[0].lower()

        if mode == "care":
            await update.message.reply_text(
                "📍 Welcome to Pulse Care Finder. Tell me what you need, such as "
                "pediatrician, dentist, hospital, specialist, or pharmacy."
            )
            await request_user_location(update, context)
            return

        if mode == "reminder":
            await update.message.reply_text(
                "⏰ Medication reminders are set up privately.\n\n"
                "Send /remindmedicine to begin."
            )
            return

    await update.message.reply_text(
        f"💓 Welcome to {PROJECT_NAME}\n\n"
        "You can use:\n"
        "💬 Health questions\n"
        "📷 Health-related photos and medicine packaging\n"
        "🎙 Voice notes\n"
        "📍 Hospitals, clinics, specialists and pharmacies\n"
        "💊 General OTC medication guidance\n"
        "⏰ Medication reminders based on schedules you confirm\n"
        "🌐 English or Arabic\n\n"
        f"🎁 You have {FREE_QUESTION_LIMIT} free test questions.\n\n"
        "⚠️ Pulse AI provides general health information and is not a doctor, "
        "pharmacist, or emergency service."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💓 Pulse AI Help\n\n"
        "💬 Ask a health question\n"
        "📷 Send a health-related photo or medicine package with an explanation\n"
        "🎙 Send a voice note\n"
        "📍 Ask: 'Find a pediatrician near me' or 'Dentist in Doha'\n"
        "💊 Ask about common OTC medicine information\n"
        "⏰ Use /remindmedicine to set a reminder for a schedule already confirmed "
        "by your doctor, pharmacist, or medicine label\n"
        "📋 Use /reminders to view active reminders\n\n"
        f"Support: {PROJECT_TELEGRAM_SUPPORT}\n"
        f"Email: {PROJECT_SUPPORT_EMAIL}\n"
        f"X: {PROJECT_X}\n\n"
        "⚠️ For emergencies, seek immediate professional medical care."
    )


async def privacy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔐 View Privacy Policy", url=PRIVACY_POLICY_URL)]]
    )
    await update.message.reply_text(
        "🔐 Pulse AI Privacy\n\n"
        "Health questions, images, voice notes, location and medication-reminder information can be sensitive.\n\n"
        "Only share what is needed. You can clear saved Care Finder location with /forgetlocation.\n\n"
        f"Privacy/support contact: {PROJECT_SUPPORT_EMAIL}",
        reply_markup=markup,
    )


async def emergency(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚨 EMERGENCY\n\n"
        "Seek immediate emergency medical care for severe difficulty breathing, severe chest pain, loss of consciousness, seizure, signs of stroke, severe bleeding, severe allergic reaction, or another potentially life-threatening condition.\n\n"
        "Do not wait for an AI response in an emergency.\n\n"
        "إذا كانت هناك حالة طبية خطيرة أو مهددة للحياة، اطلب المساعدة الطبية الطارئة فوراً."
    )


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_upgrade_offer(update, context, limit_reached=False)


async def usage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    plan, used, limit = await get_plan_and_usage(user.id)
    await update.message.reply_text(
        f"💓 PULSE AI USAGE\n\nPlan: {plan}\nQuestions used: {used}/{limit}\nQuestions remaining: {max(limit-used, 0)}\n\n"
        "Text, image, voice and successful Care Finder searches each count as one question."
    )


async def account_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    plan, used, limit = await get_plan_and_usage(user.id)
    row = await db.get_user(user.id)
    expiry = row["subscription_expiry"]
    if plan == "PLUS":
        expiry_text = expiry.strftime("%d %B %Y at %H:%M UTC") if expiry else "Active"
        text = (
            "💚 PULSE AI ACCOUNT\n\n⭐ Plan: Pulse Plus\n✅ Status: Active\n"
            f"💬 Usage: {used}/{limit}\n💬 Remaining: {max(limit-used, 0)}\n"
            f"📅 Current period ends: {expiry_text}"
        )
    else:
        text = (
            "💚 PULSE AI ACCOUNT\n\nPlan: Free Test\n"
            f"💬 Usage: {used}/{limit}\n💬 Remaining: {max(limit-used, 0)}\n\n"
            "Use /upgrade to view Pulse Plus."
        )
    await update.message.reply_text(text)


async def paysupport_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💳 PULSE AI PAYMENT SUPPORT\n\n"
        f"Telegram: {PROJECT_TELEGRAM_SUPPORT}\nEmail: {PROJECT_SUPPORT_EMAIL}\n\n"
        "Never send passwords, authentication codes, credit-card information, wallet seed phrases, or private keys."
    )


async def findcare_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text(
            "📍 Pulse Care Finder\n\nExamples:\n/findcare pediatrician near me\n/findcare dentist in Doha\n/findcare ENT specialist in London\n/findcare pharmacy near me"
        )
        return
    await perform_care_search(update, context, query)


async def location_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await request_user_location(update, context)


async def forget_location_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
    await db.clear_location(user.id)
    context.user_data.pop("pending_care_query", None)
    context.user_data["awaiting_care_location"] = False
    await update.message.reply_text(
        "✅ Your saved Pulse Care Finder location has been cleared.",
        reply_markup=ReplyKeyboardRemove(),
    )


# ==================================================
# TELEGRAM STARS
# ==================================================

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if not query:
        return
    if query.invoice_payload != PULSE_PLUS_PAYLOAD:
        await query.answer(ok=False, error_message="This Pulse AI payment could not be verified.")
        return
    if query.currency != "XTR":
        await query.answer(ok=False, error_message="Pulse Plus uses Telegram Stars.")
        return
    if query.total_amount != PULSE_PLUS_PRICE:
        await query.answer(ok=False, error_message="The payment amount could not be verified.")
        return
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.successful_payment:
        return
    payment = update.message.successful_payment
    user = update.effective_user
    if not user:
        return
    if (
        payment.currency != "XTR"
        or payment.invoice_payload != PULSE_PLUS_PAYLOAD
        or payment.total_amount != PULSE_PLUS_PRICE
    ):
        return

    await db.activate_plus(
        user.id,
        payment.subscription_expiration_date,
        payment.telegram_payment_charge_id,
    )

    await update.message.reply_text(
        "✅ PULSE PLUS ACTIVATED\n\n"
        "Your Telegram Stars payment was successful. 💚\n\n"
        f"⭐ Plan: Pulse Plus\n💬 Allowance: {PLUS_QUESTION_LIMIT} questions\n"
        "📷 Image support\n🎙 Voice support\n📍 Care Finder\n💊 Medication guidance\n"
        "🌐 English + Arabic\n📅 Renews every 30 days"
    )


# ==================================================
# LOCATION HANDLER
# ==================================================

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.location:
        return
    if update.effective_chat.type != "private":
        return
    user = update.effective_user
    if not user:
        return

    location = update.message.location
    await db.save_location(user.id, location.latitude, location.longitude)

    await update.message.reply_text(
        "✅ Location received. Pulse AI can now use it for nearby Care Finder searches.\n\nUse /forgetlocation anytime to clear it.",
        reply_markup=ReplyKeyboardRemove(),
    )

    pending = context.user_data.pop("pending_care_query", None)
    context.user_data["awaiting_care_location"] = False
    if pending:
        await perform_care_search(update, context, pending)


# ==================================================
# TEXT / IMAGE / VOICE
# ==================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    user_message = update.message.text.strip()
    if not user_message:
        return

    chat = update.effective_chat
    if chat.type in ("group", "supergroup"):
        user_message = extract_group_mention(user_message, context)
        if user_message is None:
            return
        if not user_message:
            await update.message.reply_text("💓 Mention me together with your health question.")
            return

    if chat.type == "private" and context.user_data.get("awaiting_care_location"):
        pending = context.user_data.get("pending_care_query")
        if pending:
            query = user_message if looks_like_care_search(user_message) else f"{pending} in {user_message}"
            context.user_data["awaiting_care_location"] = False
            context.user_data.pop("pending_care_query", None)
            await update.message.reply_text("📍 Searching...", reply_markup=ReplyKeyboardRemove())
            await perform_care_search(update, context, query)
            return

    if looks_like_care_search(user_message):
        await perform_care_search(update, context, user_message)
        return

    if looks_like_medication_question(user_message):
        if not await check_question_access(update, context):
            return

        await handle_medication_question(
            update,
            context,
            user_message,
        )
        return

    if not await check_question_access(update, context):
        return

    try:
        await update.message.chat.send_action(action="typing")

        response = await client.responses.create(
            model=HEALTH_MODEL,
            instructions=SYSTEM_PROMPT,
            input=user_message,
            max_output_tokens=800,
            store=False,
        )
        answer = response.output_text or "Sorry, I couldn't generate a response. Please try again."

        await update.message.reply_text(answer)
        await record_and_show_usage(update, context)
    except Exception as exc:
        logger.exception("Text AI error: %s", exc)
        await update.message.reply_text("💓 Pulse AI is temporarily unable to answer. Please try again shortly.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.photo:
        return

    caption = (update.message.caption or "").strip()
    if update.effective_chat.type in ("group", "supergroup"):
        caption = extract_group_mention(caption, context)
        if caption is None:
            return

    if not await check_question_access(update, context):
        return

    photo = update.message.photo[-1]
    if photo.file_size and photo.file_size > MAX_MEDIA_BYTES:
        await update.message.reply_text("📷 This image is too large for the current testing version.")
        return

    try:
        await update.message.chat.send_action(action="typing")
        tg_file = await photo.get_file()
        image_bytes = await tg_file.download_as_bytearray()
        image_base64 = base64.b64encode(bytes(image_bytes)).decode("utf-8")
        image_data_url = "data:image/jpeg;base64," + image_base64

        question = (
            f"The user provided this explanation with the image:\n\n{caption}\n\n"
            "Review the image together with the explanation and provide cautious guidance."
            if caption
            else
            "Review this health-related image. Describe what is reasonably visible, possible concerns, important follow-up questions, and when in-person care may be appropriate. If it appears to be medication packaging, read only clearly visible label information and ask the user to confirm the exact medicine name and strength."
        )

        response = await client.responses.create(
            model=VISION_MODEL,
            instructions=SYSTEM_PROMPT + "\n\n" + VISION_PROMPT + "\n\n" + MEDICATION_PROMPT,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": question},
                        {"type": "input_image", "image_url": image_data_url, "detail": "high"},
                    ],
                }
            ],
            max_output_tokens=900,
            store=False,
        )
        answer = response.output_text or "I couldn't reliably analyze this image."

        med_related = (
            looks_like_medication_question(caption)
            or looks_like_medication_question(answer)
        )

        if med_related:
            arabic = contains_arabic(caption) or contains_arabic(answer)

            if arabic:
                safety_note = (
                    "\n\n💊 تنبيه: لا تعتمد على شكل الحبة وحده. تأكد من اسم الدواء "
                    "والتركيز من العبوة الأصلية أو الصيدلي/الطبيب قبل الاستخدام أو "
                    "إعداد أي تذكير."
                )
            else:
                safety_note = (
                    "\n\n💊 Important: Do not rely on pill appearance alone. Confirm "
                    "the exact medicine name and strength from the original package, "
                    "pharmacist, or doctor before taking it or creating a reminder."
                )

            await update.message.reply_text(
                "📷 Pulse AI Image Review\n\n" + answer + safety_note,
                reply_markup=medication_action_keyboard(arabic),
            )
        else:
            await update.message.reply_text(
                "📷 Pulse AI Image Review\n\n" + answer
            )

        await record_and_show_usage(update, context)
    except Exception as exc:
        logger.exception("Image error: %s", exc)
        await update.message.reply_text("📷 Pulse AI couldn't process this image right now. Please try again.")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.voice:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("🎙 For privacy, please send medical voice notes directly to Pulse AI in private chat.")
        return
    if not await check_question_access(update, context):
        return

    voice = update.message.voice
    if voice.duration > VOICE_MAX_SECONDS:
        await update.message.reply_text("🎙 Please keep voice notes under 2 minutes during testing.")
        return
    if voice.file_size and voice.file_size > MAX_MEDIA_BYTES:
        await update.message.reply_text("🎙 This voice file is too large for the current testing version.")
        return

    temp_path = None
    try:
        tg_file = await voice.get_file()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
            temp_path = temp_file.name
        await tg_file.download_to_drive(temp_path)

        with open(temp_path, "rb") as audio_file:
            transcript = await client.audio.transcriptions.create(
                model=TRANSCRIPTION_MODEL,
                file=audio_file,
            )
        transcript_text = (transcript.text if transcript else "").strip()
        if not transcript_text:
            await update.message.reply_text("🎙 I couldn't clearly understand that voice note. Please try again or type your question.")
            return

        if looks_like_care_search(transcript_text):
            await perform_care_search(update, context, transcript_text)
            return

        is_medication = looks_like_medication_question(transcript_text)

        instructions = SYSTEM_PROMPT
        if is_medication:
            instructions += "\n\n" + MEDICATION_PROMPT

        response = await client.responses.create(
            model=HEALTH_MODEL,
            instructions=instructions,
            input=(
                "The user sent the following voice note. Respond naturally in the same language:\n\n"
                + transcript_text
            ),
            max_output_tokens=850,
            store=False,
        )
        answer = response.output_text or "I understood the voice note but couldn't generate a response."

        if is_medication:
            arabic = contains_arabic(transcript_text)
            if arabic:
                footer = (
                    "\n\n💊 هذه معلومات عامة وليست وصفة طبية. اتبع تعليمات العبوة "
                    "وتأكد من الصيدلي أو الطبيب عند الشك."
                )
            else:
                footer = (
                    "\n\n💊 This is general information, not a prescription. Follow "
                    "the medicine label and confirm with a pharmacist or doctor if unsure."
                )

            await update.message.reply_text(
                "🎙 Pulse AI Voice Reply\n\n" + answer + footer,
                reply_markup=medication_action_keyboard(arabic),
            )
        else:
            await update.message.reply_text(
                "🎙 Pulse AI Voice Reply\n\n" + answer
            )

        await record_and_show_usage(update, context)
    except Exception as exc:
        logger.exception("Voice error: %s", exc)
        await update.message.reply_text("🎙 Pulse AI couldn't process this voice note right now.")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


# ==================================================
# DATABASE LIFECYCLE
# ==================================================

async def startup(application: Application):
    await db.init_db(DATABASE_URL)

    reminder_task = asyncio.create_task(
        medication_reminder_worker(application)
    )
    application.bot_data["medication_reminder_task"] = reminder_task

    logger.info("Pulse AI database connected.")
    logger.info("Medication reminder worker started.")


async def shutdown(application: Application):
    reminder_task = application.bot_data.get(
        "medication_reminder_task"
    )

    if reminder_task:
        reminder_task.cancel()

        try:
            await reminder_task
        except asyncio.CancelledError:
            pass

    close_db_func = getattr(db, "close_db", None)

    if close_db_func is not None:
        await close_db_func()
        logger.info("Pulse AI database connection closed.")
    else:
        logger.warning(
            "database.db.close_db() was not found. "
            "Skipping database shutdown cleanup."
        )

    logger.info("Medication reminder worker stopped.")


# ==================================================
# RUN
# ==================================================

def main():
    application = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(startup)
        .post_shutdown(shutdown)
        .build()
    )

    private_only = filters.ChatType.PRIVATE

    medication_conversation = ConversationHandler(
        entry_points=[
            CommandHandler(
                "remindmedicine",
                start_medication_reminder,
                filters=private_only,
            ),
            CallbackQueryHandler(
                start_medication_reminder,
                pattern=r"^med_set_reminder$",
            ),
        ],
        states={
            MED_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    med_name_message,
                )
            ],
            MED_STRENGTH: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    med_strength_message,
                )
            ],
            MED_SOURCE: [
                CallbackQueryHandler(
                    med_source_callback,
                    pattern=r"^med_source_(yes|no)$",
                )
            ],
            MED_INTERVAL: [
                CallbackQueryHandler(
                    med_interval_callback,
                    pattern=r"^med_interval_(6|8|12|24|custom)$",
                )
            ],
            MED_INTERVAL_CUSTOM: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    med_interval_custom_message,
                )
            ],
            MED_START: [
                CallbackQueryHandler(
                    med_start_callback,
                    pattern=r"^med_start_(taken_now|due_now)$",
                )
            ],
            MED_DURATION: [
                CallbackQueryHandler(
                    med_duration_callback,
                    pattern=r"^med_duration_(1|3|5|7|14|30|open)$",
                )
            ],
            MED_CONFIRM: [
                CallbackQueryHandler(
                    med_confirm_callback,
                    pattern=r"^med_confirm_(yes|no)$",
                )
            ],
        },
        fallbacks=[
            CommandHandler(
                "cancel",
                cancel_medication_reminder,
            )
        ],
        allow_reentry=True,
    )

    # Medication reminder conversation must be registered before
    # the generic text handler.
    application.add_handler(medication_conversation)

    # Private commands
    application.add_handler(CommandHandler("start", start, filters=private_only))
    application.add_handler(CommandHandler("help", help_command, filters=private_only))
    application.add_handler(CommandHandler("privacy", privacy, filters=private_only))
    application.add_handler(CommandHandler("emergency", emergency, filters=private_only))
    application.add_handler(CommandHandler("usage", usage_command, filters=private_only))
    application.add_handler(CommandHandler("upgrade", upgrade_command, filters=private_only))
    application.add_handler(CommandHandler("account", account_command, filters=private_only))
    application.add_handler(CommandHandler("paysupport", paysupport_command, filters=private_only))
    application.add_handler(CommandHandler("findcare", findcare_command, filters=private_only))
    application.add_handler(CommandHandler("location", location_command, filters=private_only))
    application.add_handler(CommandHandler("forgetlocation", forget_location_command, filters=private_only))
    application.add_handler(CommandHandler("reminders", reminders_command, filters=private_only))

    # Medication action buttons
    application.add_handler(
        CallbackQueryHandler(
            handle_med_find_pharmacy,
            pattern=r"^med_find_pharmacy$",
        )
    )
    application.add_handler(
        CallbackQueryHandler(
            handle_reminder_action,
            pattern=r"^rem_(taken|snooze|stop):\d+$",
        )
    )

    # Telegram Stars
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_callback,
        )
    )

    # Media / location / normal messages
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    logger.info("Pulse AI medication + pharmacy + reminder beta is running...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
