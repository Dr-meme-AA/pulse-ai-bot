"""Deterministic Telegram routing, with no external calls or state.

The emergency shortcut recognizes a finite set of explicitly reported red flags
and a few standalone urgent phrases. It cannot assess severity or rule out an
emergency from text, speech, or a picture. /emergency is always available.
"""

import re
import unicodedata
from functools import lru_cache


EMERGENCY_MESSAGE = (
    "🚨 If someone may be in immediate danger, call your local emergency "
    "number now and ask for an ambulance. Do not wait for a chat response. "
    "Pulse cannot call an ambulance for you.\n\n"
    "🚨 إذا كانت حياتك أو حياة شخص آخر في خطر، اتصل الآن برقم الطوارئ المحلي "
    "واطلب الإسعاف. لا تنتظر الرد في المحادثة. لا يستطيع Pulse طلب الإسعاف نيابةً عنك."
)

PREPROCESSING_NOTICE = (
    "If this could be an emergency, call local emergency services now. Do not wait for Pulse.\n"
    "إذا احتمل أن تكون الحالة طارئة، اتصل بالطوارئ المحلية الآن. لا تنتظر رد Pulse."
)

_QUOTA_EMERGENCY = (
    "If someone may be in immediate danger, call your local emergency number now.\n"
    "إذا كان أحد في خطر فوري، اتصل برقم الطوارئ المحلي الآن."
)
_FREE_ENTRY = "/emergency is always free. أمر /emergency مجاني دائماً."
MEDIA_NOT_ASSESSED = (
    f"{_QUOTA_EMERGENCY}\n\nPulse has not assessed this attachment. "
    f"لم يقيّم Pulse هذا المرفق.\n{_FREE_ENTRY}"
)
QUESTION_NOT_ASSESSED = (
    f"{_QUOTA_EMERGENCY}\n\nPulse has not assessed this question. "
    f"لم يقيّم Pulse هذا السؤال.\n{_FREE_ENTRY}"
)


@lru_cache(maxsize=256)
def _english_term(term):
    # Match English tokens and natural plurals, e.g. ENT, dentist(s), pharmacies.
    stem = re.escape(term)
    inflected = stem[:-1] + r"(?:y|ies)" if stem.endswith("y") else stem + r"(?:s|es)?"
    return re.compile(r"(?<!\w)" + inflected + r"(?!\w)", re.IGNORECASE)


def contains_term(text, terms):
    lowered = (text or "").casefold().replace("’", "'")
    for term in terms:
        if term.isascii():
            if _english_term(term).search(lowered):
                return True
        elif term in lowered:
            # Arabic articles and conjunctions may be attached to a word.
            return True
    return False


_ARABIC_NORMALIZATION = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ـ": None,
})


def _normalize(text):
    plain = unicodedata.normalize("NFKC", text or "").casefold().replace("’", "'")
    plain = plain.translate(_ARABIC_NORMALIZATION)
    return re.sub(r"[\u064b-\u065f\u0670]", "", plain)


def has_named_place(text):
    text = _normalize(text)
    # A named place wins over generic words such as "nearest". A relative
    # expression alone needs a city or an explicitly shared location.
    return bool(re.search(
        r"\b(?:in|near)\s+(?!(?:me|us|here|there|my|our|the\s+area|this\s+area)\b)[^\W\d_]"
        r"|(?:^|\s)(?:في|قرب|بالقرب من|قريب من)\s+"
        r"(?!(?:منطقتي|منطقتنا|موقعي|موقعنا|مكاني|مكاننا|هنا)\b)[\u0621-\u064a]",
        text,
    ))


_EN_REPORT = (
    r"(?:(?:i|we|he|she|they|my (?:child|baby|mother|mom|father|dad|friend|son|daughter))\s+"
    r"(?:have|has|am having|is having|are having|feel|feels|am experiencing|is experiencing)"
    r"|i'm having|i've got|she's having|he's having)\s+"
)
_EN_SIGN = (
    r"(?:(?:(?:a|an)\s+)?(?:severe|sudden|crushing|heavy|intense)\s+)?chest pain"
    r"|(?:severe |crushing |sudden )?pain in (?:my |the |his |her )?chest"
    r"|(?:severe\s+)?(?:difficulty|trouble) breathing|severe bleeding"
    r"|(?:a |an )?(?:seizure(?!\s+(?:disorder|history|plan|diary|medicine))|severe allergic reaction)"
    r"|(?:a |an )?(?:stroke|heart attack)|sudden facial droop"
)
_EN_IMMEDIATE = re.compile(
    r"(?<!\w)(?:"
    r"(?:i|we|she|he|they|baby|child|son|daughter|mom|dad|mother|father|someone)\s+"
    r"(?:suddenly\s+)?(?:can't|cannot|can not|can barely)\s+(?:breathe|catch (?:my|her|his|their) breath)"
    r"(?!\s+(?:through|out of)\s+(?:(?:my|her|his|their|one|the)\s+)?(?:nose|nostril))"
    r"|(?:i'm|he's|she's|(?:i|he|she|we|they|baby|child|son|daughter|mom|dad|mother|father|someone)"
    r"\s+(?:is|am|are))\s+(?:choking|unconscious|not breathing|bleeding heavily|gasping for (?:air|breath))"
    r"|(?:he|she|baby|child|son|daughter|mom|dad|mother|father)\s+"
    r"(?:isn't breathing|won't wake up|will not wake up|(?:has )?stopped breathing)"
    r"|(?:my|her|his) (?:face is drooping|chest (?:really )?hurts)"
    r"|(?:the |my )?bleeding (?:won't|will not) stop"
    r"|i (?:just )?took an overdose|i(?:'m| am) about to (?:kill myself|end my life)"
    r"|i (?:intend|plan|want) to (?:kill myself|end my life)"
    r"|" + _EN_REPORT + r"(?:" + _EN_SIGN + r"))(?!\w)"
)
_EN_KIN = r"(?:child|baby|toddler|wife|husband|mother|father|mom|mum|dad|brother|sister|son|daughter|friend|partner)"
_EN_PERSON = r"(?:i|he|she|they|we|someone|(?:my|our|the) " + _EN_KIN + r")"
_EN_POSSESSIVE = r"(?:my|his|her|their|(?:my|our|the) " + _EN_KIN + r"'s)"
_EN_ACUTE = re.compile(
    r"(?<!\w)(?:" + _EN_PERSON + r"(?:'m|'s|'re| (?:is|are|am)) (?:now |suddenly )?"
    r"(?:struggling to breathe|struggling for (?:air|breath)|unable to breathe|turning (?:blue|grey|gray))"
    r"|" + _EN_POSSESSIVE + r" (?:lips|face) (?:are |is |have |has )?(?:now |suddenly )?"
    r"(?:turning |turned |gone |become )?(?:blue|grey|gray|bluish)"
    r"|" + _EN_POSSESSIVE + r" (?:face|mouth) (?:has |is )?(?:suddenly )?"
    r"(?:drooped|drooping|droops|fallen on one side)"
    r"|" + _EN_PERSON + r" (?:suddenly )?(?:can't|cannot|is unable to|am unable to) (?:lift|raise|move) "
    r"(?:(?:my|his|her|their|the) )?(?:one|left|right) arm"
    r"|(?:there is|there's|i (?:have|feel)|i'm feeling) (?:a |an )?(?:crushing|severe|heavy|squeezing) "
    r"(?:pressure|tightness|weight|pain) (?:in|on|across) (?:my|the) chest"
    r"|" + _EN_PERSON + r" (?:just )?(?:collapsed|passed out|fainted) and (?:(?:she|he|they) )?"
    r"(?:won't|will not|doesn't|does not|isn't|is not) (?:wake up|respond|responding|waking up))(?!\w)"
)
_AR_SUBJECT = r"(?:انا|هو|هي|طفلي|طفلتي|ولدي|بنتي|ابني|ابنتي|زوجي|زوجتي|امي|ابوي|ابي|اخوي|اخي|اختي)"
_AR_CANT_BREATHE = (
    r"(?:ما\s*(?:اقدر|يقدر|تقدر)|(?:مو|مب|مش|ماني|موب|مب\s+قاعد|ما)\s+قادر[ه]?"
    r"|لا\s+(?:استطيع|يستطيع|تستطيع|اقدر|يقدر|تقدر))\s+(?:ان\s+)?"
    r"(?:(?:ا|ي|ت)تنفس|التنفس|(?:ا|يا|تا)خذ\s+نفس(?:ي|ه|ها)?)(?!\s+من\s+(?:انفي|خشمي))"
)
_AR_SIGN = (
    r"(?:الم|وجع|ضغط)\s+(?:و?(?:شديد|قوي|حاد|ضاغط|عاصر)\s+){0,3}(?:(?:في|ف)\s+)?"
    r"(?:ب?صدري|بالصدر|الصدر)|نزيف\s+(?:شديد|قوي|ما\s*يوقف)"
    r"|ضيق\s+(?:شديد\s+في\s+(?:النفس|التنفس)|(?:نفس|تنفس)\s+شديد)"
    r"|صعوبه\s+شديده\s+في\s+التنفس"
)
_AR_ACUTE = re.compile(
    r"(?<!\w)و?(?:(?:شفايف|شفاه|شفتا|شفتي)(?:ي|ه|ها|هم)?\s+"
    r"(?:(?:صارت|صار|بدت)\s+)?(?:زرقا[ءه]?|مزرق[ه]?|مزرقين|ازرقت)"
    r"|(?:اعوج|مال|التوي)\s+(?:فمي|فمه|فمها|وجهي|وجهه|وجهها)"
    r"|(?:فمي|فمه|فمها|وجهي|وجهه|وجهها)\s+(?:اعوج|مايل|مائل|معوج|مال)"
    r"|(?:ما\s*(?:اقدر|يقدر|تقدر)|(?:مو|مب|مش)\s+قادر[ه]?)\s+"
    r"(?:احرك|يحرك|تحرك|ارفع|يرفع|ترفع)\s+(?:ا?يد(?:ي|ه|ها)|ذراع(?:ي|ه|ها))"
    r"|" + _AR_SUBJECT + r"\s+(?:فجاه\s+)?(?:طاح|طاحت|وقع|وقعت|انهار|انهارت)\s+"
    r"(?:وما|ولا|و?مو|و?مب)\s*(?:يرد|ترد|يتجاوب|تتجاوب|يفيق|تفيق|يصحى|تصحى|يصحي|تصحي))"
    r"(?!\w)"
)
_AR_IMMEDIATE = re.compile(
    r"(?<!\w)و?(?:" + _AR_CANT_BREATHE
    + r"|" + _AR_SUBJECT + r"\s+(?:(?:قاعد[ه]?\s+)?(?:اختنق|يختنق|تختنق)"
    r"|(?:ما\s*(?:يتنفس|تتنفس)|لا\s+(?:يتنفس|تتنفس))|فاقد[ه]?\s+الوعي"
    r"|(?:مغمي|اغمي)\s+عليه[ا]?|طاح\s+وما\s+يرد)"
    r"|(?:عندي|عنده|عندها|احس\s+ب|احس\s+|(?:اعاني|يعاني|تعاني)\s+من)\s*(?:" + _AR_SIGN
    + r")|(?:صدري|صدره|صدرها)\s+(?:يعورني|يوجعني|يؤلمني)\s+(?:وايد|بقوه)"
    r"|(?:النزيف|الدم)\s+(?:لا\s+يتوقف|ما\s*(?:يوقف|يتوقف))"
    r"|(?:ابي|ابغي|ناوي|ناويه|اريد)\s+(?:ان\s+)?(?:اقتل نفسي|انهي حياتي)"
    r"|(?:اخذت|تناولت)\s+جرعه\s+زائده)(?!\w)"
)
_STANDALONE = re.compile(
    r"(?:(?:help|urgent|please help|الحقوني|ساعدوني|النجده)[\s:!]+)?(?:"
    r"(?:(?:severe|crushing|sudden) chest pain|severe (?:difficulty breathing|bleeding)"
    r"|can't breathe|cannot breathe|not breathing)"
    r"|(?:الم|وجع)\s+(?:شديد|قوي|حاد)\s+(?:في\s+(?:الصدر|صدري)|بالصدر|بصدري)"
    r"|نزيف شديد|فاقد الوعي|اختنق|قاعد اختنق)"
    r"(?:\s+(?:right now|now|الحين|الان))?", re.IGNORECASE
)
_CLAUSE_SPLIT = re.compile(r"[.,!?;\n،؛؟]+|\b(?:but|however|لكن|ولكن|بس)\b")
_HYPOTHETICAL = re.compile(
    r"\b(?:if|hypothetically|imagine|suppose|translate|translation|example of|"
    r"what (?:are the|does|is meant by)|how (?:do|would) (?:i|you) say|"
    r"اذا|لو|مثال|ترجم|ترجمه|معني|معناه|اسباب|اعراض)\b"
)
_PAST_ONLY = re.compile(
    r"\b(?:yesterday|last (?:week|month|year)|(?:days?|weeks?|months?|years?) ago|as a child"
    r"|امس|سابقا|زمان|السنه الماضيه|الاسبوع الماضي|قبل (?:يومين|اسبوع|شهر|سنه|سنين))\b"
)
_CURRENT = re.compile(r"\b(?:now|today|currently|الحين|الان|حاليا|اليوم)\b")
_NEGATION_BEFORE = re.compile(
    r"(?:\bnot|\bno|\bwithout|\bnever|(?:^|\s)و?(?:مو|ما|مب|مش|ليس|لم|لا))\s*$"
)


def reports_urgent_sign(text):
    for clause in _CLAUSE_SPLIT.split(_normalize(text)):
        clause = " ".join(clause.split())
        if not clause or (_PAST_ONLY.search(clause) and not _CURRENT.search(clause)):
            continue
        if _STANDALONE.fullmatch(clause):
            return True
        for signs in (_EN_IMMEDIATE, _EN_ACUTE, _AR_IMMEDIATE, _AR_ACUTE):
            for match in signs.finditer(clause):
                prefix = clause[:match.start()]
                if not _HYPOTHETICAL.search(prefix) and not _NEGATION_BEFORE.search(prefix):
                    return True
    return False
