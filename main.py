import os
import re
import math
import base64
import tempfile
import logging
from datetime import datetime, timezone

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
When the user asks what medicine to take, asks about a tablet, or sends
medicine information, follow these medication-safety rules:

1. Medication information is educational and for symptom relief only;
   it is not a prescription or a cure.
2. Prefer common over-the-counter options only when appropriate.
3. Do not recommend starting prescription antibiotics, prescription
   steroids, controlled medicines, sedatives, or changing an existing
   prescription without clinician direction.
4. Before giving a specific tablet count or dose, make sure the necessary
   details are known. Depending on the medicine this can include age,
   exact active ingredient, exact strength/concentration, allergies,
   pregnancy/breastfeeding, liver/kidney disease, stomach ulcers,
   blood thinners, and other regular medicines.
5. For children, do not provide a calculated dose unless the child's age,
   current weight, exact medicine, and exact concentration/strength are
   known. Encourage confirmation with a pharmacist or clinician.
6. If essential details are missing, ask for them instead of guessing.
7. When a standard OTC label dose is appropriate and enough information
   is available, it may be explained cautiously, together with maximum
   label limits and contraindication warnings.
8. Always remind the user to follow the package label and confirm with a
   pharmacist or doctor if uncertain, if they have medical conditions,
   take other medicines, have allergies, are pregnant/breastfeeding, or
   are treating a child.
9. After useful medication guidance, offer to help find a nearby pharmacy
   by telling the user they can say: "Find a pharmacy near me".
10. Medication reminders must reflect a schedule the user confirms came
    from their doctor, pharmacist, or medicine label; Pulse AI must not
    invent a treatment schedule.
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
    "medicine", "medication", "tablet", "tablets", "pill", "pills",
    "painkiller", "dose", "dosage", "what can i take", "what should i take",
    "paracetamol", "acetaminophen", "ibuprofen", "دواء", "دواء", "دوائي",
    "حبوب", "حبة", "جرعة", "مسكن", "ماذا آخذ", "وش آخذ", "شنو آخذ",
    "باراسيتامول", "بنادول", "ايبوبروفين", "إيبوبروفين",
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
    if context.args and context.args[0].lower() == "care":
        await update.message.reply_text(
            "📍 Welcome to Pulse Care Finder. Tell me what you need, such as pediatrician, dentist, hospital, specialist, or pharmacy."
        )
        await request_user_location(update, context)
        return

    await update.message.reply_text(
        f"💓 Welcome to {PROJECT_NAME}\n\n"
        "You can use:\n"
        "💬 Health questions\n"
        "📷 Health-related photos\n"
        "🎙 Voice notes\n"
        "📍 Hospitals, clinics, specialists and pharmacies\n"
        "💊 General OTC medication guidance\n"
        "🌐 English or Arabic\n\n"
        f"🎁 You have {FREE_QUESTION_LIMIT} free test questions.\n\n"
        "⚠️ Pulse AI provides general health information and is not a doctor or emergency service."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💓 Pulse AI Help\n\n"
        "💬 Ask a health question\n"
        "📷 Send a health-related photo with an explanation\n"
        "🎙 Send a voice note\n"
        "📍 Ask: 'Find a pediatrician near me' or 'Dentist in Doha'\n"
        "💊 Ask about a common OTC medicine or send a clear photo of its packaging\n\n"
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

    if not await check_question_access(update, context):
        return

    try:
        await update.message.chat.send_action(action="typing")
        instructions = SYSTEM_PROMPT
        if looks_like_medication_question(user_message):
            instructions += "\n\n" + MEDICATION_PROMPT

        response = await client.responses.create(
            model=HEALTH_MODEL,
            instructions=instructions,
            input=user_message,
            max_output_tokens=800,
            store=False,
        )
        answer = response.output_text or "Sorry, I couldn't generate a response. Please try again."

        if looks_like_medication_question(user_message):
            answer += (
                "\n\n💊 If you'd like, I can also help you find a nearby pharmacy. "
                "Send: “Find a pharmacy near me”."
            )

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
        answer += (
            "\n\n💊 If this is a medicine and your doctor/pharmacist has confirmed the exact name, strength and schedule, "
            "Pulse AI can later help you set reminders for that confirmed schedule."
        )
        await update.message.reply_text("📷 Pulse AI Image Review\n\n" + answer)
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

        instructions = SYSTEM_PROMPT
        if looks_like_medication_question(transcript_text):
            instructions += "\n\n" + MEDICATION_PROMPT

        response = await client.responses.create(
            model=HEALTH_MODEL,
            instructions=instructions,
            input=(
                "The user sent the following voice note. Respond naturally in the same language:\n\n"
                + transcript_text
            ),
            max_output_tokens=800,
            store=False,
        )
        answer = response.output_text or "I understood the voice note but couldn't generate a response."
        if looks_like_medication_question(transcript_text):
            answer += "\n\n💊 You can also ask me to find a pharmacy near you."
        await update.message.reply_text("🎙 Pulse AI Voice Reply\n\n" + answer)
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
    logger.info("Pulse AI database connected.")


async def shutdown(application: Application):
    await db.close_db()
    logger.info("Pulse AI database connection closed.")


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

    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Pulse AI database-enabled beta is running...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
