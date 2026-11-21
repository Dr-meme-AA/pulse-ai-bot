"""Build provider-directory requests from local, controlled facility and place names."""
from dataclasses import dataclass, replace
from functools import lru_cache
import re
import unicodedata
from zoneinfo import available_timezones


# Order matters: match specific services before generic doctors or facilities.
# Each entry has an English search label, an Arabic label and accepted aliases.
FACILITIES = (
    ("children's hospital", "مستشفى أطفال", ("children's hospital", "childrens hospital", "pediatric hospital", "مستشفى الاطفال")),
    ("orthodontist", "طبيب تقويم أسنان", ("orthodontics", "تقويم اسنان", "تقويم الاسنان", "دكتور تقويم")),
    ("pediatrician", "طبيب أطفال", ("paediatrician", "pediatric clinic", "طب اطفال", "دكتور اطفال", "طبيبة اطفال", "اطباء اطفال")),
    ("ENT specialist", "طبيب أنف وأذن وحنجرة", ("ent", "otolaryngologist", "ear nose and throat", "انف واذن", "اذن وانف")),
    ("dermatologist", "طبيب جلدية", ("dermatology", "جلدية", "جلديه")),
    ("cardiologist", "طبيب قلب", ("cardiology", "دكتور قلب", "عيادة القلب", "امراض القلب")),
    ("orthopedic specialist", "طبيب عظام", ("orthopaedic", "orthopedic", "orthopedist", "عظام")),
    ("ophthalmologist", "طبيب عيون", ("eye doctor", "عيون")),
    ("gynecologist", "طبيب نساء وولادة", ("gynaecologist", "gynecology", "نساء وولادة")),
    ("psychiatrist", "طبيب نفسي", ("psychiatry", "دكتور نفسي")),
    ("psychologist", "أخصائي نفسي", ("psychology", "اخصائية نفسية")),
    ("physiotherapist", "علاج طبيعي", ("physio", "physiotherapy", "physical therapist")),
    ("dentist", "طبيب أسنان", ("dental clinic", "dentistry", "دكتور اسنان", "عيادة اسنان", "اسنان")),
    ("surgeon", "طبيب جراحة", ("surgery clinic", "جراح", "عيادة جراحة")),
    ("pharmacy", "صيدلية", ("pharmacies", "drugstore", "صيدليات")),
    ("emergency department", "قسم الطوارئ", ("emergency room", "urgent care", "طوارئ")),
    ("hospital", "مستشفى", ("hospitals", "مستشفيات")),
    ("health center", "مركز صحي", ("health centre", "medical center", "medical centre", "مركز طبي")),
    ("clinic", "عيادة", ("clinics", "عيادات")),
    ("specialist", "أخصائي", ("اختصاصي", "اخصائية")),
    ("doctor", "طبيب", ("doctors", "اطباء", "general practitioner", "family doctor", "دكتور", "دكتورة", "طبيبة")),
)

# The local vocabulary limits what can leave the bot. Add reviewed aliases as
# markets expand. Shared coordinates work worldwide, including smaller towns.
PLACES = (
    ("Doha", "الدوحة|دوحة|الدوحه"), ("Al Wakrah", "الوكرة|الوكَرة|الوكره|al wakra|al wakrah|wakrah|wakra"),
    ("Lusail", "لوسيل"), ("Al Rayyan", "الريان|ar rayyan|rayyan"), ("Al Khor", "الخور"),
    ("Manama", "المنامة|المنامه|منامة"), ("Muharraq", "المحرق|al muharraq"), ("Riffa", "الرفاع|ar riffa"),
    ("Hamad Town", "مدينة حمد"), ("Isa Town", "مدينة عيسى"), ("Sitra", "سترة|ستره"),
    ("Riyadh", "الرياض"), ("Jeddah", "جدة|جده|jedda"), ("Dammam", "الدمام"),
    ("Al Khobar", "الخبر|khobar"), ("Makkah", "مكة|مكة المكرمة|mecca"), ("Madinah", "المدينة المنورة|medina"),
    ("Buraidah", "بريدة|بريده|buraydah"), ("Dubai", "دبي"), ("Abu Dhabi", "أبوظبي|أبو ظبي"),
    ("Sharjah", "الشارقة|الشارقه"), ("Ajman", "عجمان"), ("Al Ain", "العين"),
    ("Kuwait City", "مدينة الكويت"), ("Salmiya", "السالمية|السالميه"),
    ("Muscat", "مسقط"), ("Salalah", "صلالة|صلاله"), ("Sohar", "صحار"),
    ("Bangkok", "بانكوك|بانكوك تايلاند|กรุงเทพมหานคร|กรุงเทพ"), ("Chiang Mai", "شيانغ ماي|تشيانغ ماي|เชียงใหม่"),
    ("Pattaya", "باتايا|พัทยา"), ("Phuket", "بوكيت|ภูเก็ต"),
    ("Hong Kong", "هونغ كونغ|هونج كونج|香港"), ("Beijing", "بكين|北京"),
    ("Shanghai", "شنغهاي|上海"), ("Shenzhen", "شنتشن|深圳"), ("Guangzhou", "قوانغتشو|广州"),
    ("London", "لندن"), ("New York", "نيويورك|new york city|nyc"), ("Paris", "باريس"),
    ("Singapore", "سنغافورة|سنغافوره"), ("Istanbul", "إسطنبول|استانبول"), ("Cairo", "القاهرة|القاهره"),
    ("Amman", "مدينة عمان"), ("Manchester", "مانشستر"), ("Toronto", "تورنتو"), ("Sydney", "سيدني"),
    ("Qatar", "قطر"), ("Bahrain", "البحرين"), ("Saudi Arabia", "السعودية|السعوديه|المملكة العربية السعودية|ksa"),
    ("United Arab Emirates", "الإمارات|الإمارات العربية المتحدة|uae"), ("Kuwait", "الكويت"),
    ("Oman", "سلطنة عمان"), ("Thailand", "تايلاند|ประเทศไทย"), ("China", "الصين|中国"),
    ("United Kingdom", "المملكة المتحدة|بريطانيا|uk"), ("United States", "الولايات المتحدة|usa"),
    ("Canada", "كندا"), ("France", "فرنسا"), ("Jordan", "الأردن"), ("Egypt", "مصر"),
)

_REQUEST_START = re.compile(
    r"(?<!\w)(?:find|looking for|recommend|where is|search for|"
    r"ابحث|دور لي|دورلي|ابي|اريد|احتاج|رشح لي|وين اقرب)(?!\w)", re.I
)
_LOCATION_PREFIX = re.compile(r"(?<!\w)(?:in|near|around|في|قرب|بالقرب من|بجوار)\s+", re.I)


def _normalize(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    text = text.translate(str.maketrans("أإآى", "اااي")).replace("ـ", "")
    text = re.sub(r"[\u064b-\u065f\u0670]", "", text)
    return " ".join(text.strip().split())


@lru_cache(maxsize=1)
def _place_index():
    areas = {}
    for zone in sorted(available_timezones()):
        parts = zone.split("/")
        if len(parts) > 1 and parts[0] in {"Africa", "America", "Asia", "Australia", "Europe", "Pacific", "Indian"}:
            name = parts[-1].replace("_", " ")
            areas[_normalize(name)] = name
    for label, aliases in PLACES:
        for name in (label, *aliases.split("|")):
            areas[_normalize(name)] = label
    # Without vowels, عمان can mean Oman or Amman. Ask for a more specific place.
    areas.pop("عمان", None)
    return areas


@lru_cache(maxsize=1)
def _facility_patterns():
    categories = []
    for english, arabic, aliases in FACILITIES:
        terms = {_normalize(word) for word in (english, arabic, *aliases)}
        if english.endswith(("ist", "ian", "or", "er", "on", "al", "ic")):
            terms.add(english + "s")
        words = sorted(terms, key=len, reverse=True)
        expression = r"(?<!\w)(?:" + "|".join(map(re.escape, words)) + r")(?!\w)"
        categories.append((english, arabic, re.compile(expression)))
    return categories


def confirmed_area(text):
    """Accept only complete local place names (optionally city, country)."""
    value = _normalize(text).strip(" .!?؟")
    parts = [part.strip() for part in re.split("[,،]", value)]
    if len(parts) not in (1, 2) or not all(parts):
        return None
    names = [_place_index().get(part) for part in parts]
    if all(names):
        return ", ".join(dict.fromkeys(names))
    return None


@dataclass(frozen=True)
class CareLookup:
    facility: str  # Controlled, language-appropriate output label.
    language: str
    area: str | None
    area_requested: bool

    def in_area(self, name):
        area = confirmed_area(name)
        if not area:
            raise ValueError("Unrecognised directory area")
        return replace(self, area=area, area_requested=True)

    @property
    def text_query(self):
        return f"{self.facility}, {self.area}" if self.area else self.facility


def project_request(text):
    """Project the raw input locally. No raw text is retained on the result."""
    normalized = _normalize(text)
    language = "ar" if re.search(r"[\u0600-\u06ff]", normalized) else "en"
    # Earlier mentions of a doctor or location can be background. Use the
    # explicit request clause when available, and discard any trailing narrative.
    cues = list(_REQUEST_START.finditer(normalized))
    requested = normalized[cues[-1].end():] if cues else normalized
    requested = re.split(r"[.;!?؟؛\n]", requested, maxsplit=1)[0]
    facility = next((ar if language == "ar" else en for en, ar, pattern in _facility_patterns()
                     if pattern.search(requested)), None)
    if not facility:
        return None
    area_requested = False
    for match in reversed(list(_LOCATION_PREFIX.finditer(requested))):
        tail = requested[match.end():].strip(" .!?؟")
        if re.match(r"^(me|مني)(?!\w)", tail):
            continue
        area_requested = True
        # A complete named area is required. Free-form prose, symptoms, street
        # addresses, and unrecognised places get a separate request for a city.
        area = confirmed_area(tail)
        if area:
            return CareLookup(facility, language, area, True)
    return CareLookup(facility, language, None, area_requested)
