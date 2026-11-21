"""Only exact navigation words select an action. All other content is discarded."""

WORDS = {
    "en": {"menu": "menu", "hello": "menu", "hi": "menu", "help": "menu", "english": "menu",
           "account": "account", "privacy": "privacy", "human": "human", "support": "human"},
    "ar": {"القائمة": "menu", "مرحبا": "menu", "هلا": "menu", "العربية": "menu", "مساعدة": "menu",
           "حسابي": "account", "الخصوصية": "privacy", "موظف": "human", "الدعم": "human"},
    "th": {"เมนู": "menu", "สวัสดี": "menu", "ภาษาไทย": "menu",
           "บัญชี": "account", "ความเป็นส่วนตัว": "privacy", "เจ้าหน้าที่": "human"},
    "zh": {"菜单": "menu", "你好": "menu", "中文": "menu", "帮助": "menu",
           "账户": "account", "隐私": "privacy", "人工": "human"},
}
TEXT = {
    "en": {"menu": "Welcome to Pulse AI support. Type Account, Privacy or Human.",
           "account": "For access or billing questions, contact the Pulse AI support team.",
           "privacy": "This inbox handles general support. Keep health details and attachments out of this chat.",
           "human": "You can reach the Pulse AI support team using the contact below.",
           "other": "This inbox handles general support. Please keep symptoms, recordings, photos, location and medication details private.",
           "end": "In an emergency, contact your local emergency service now.", "contact": "Human support"},
    "ar": {"menu": "مرحباً بك في دعم Pulse AI. اكتب «حسابي» أو «الخصوصية» أو «موظف».",
           "account": "للاستفسار عن حسابك أو الفواتير، تواصل مع فريق دعم Pulse AI.",
           "privacy": "هذه المحادثة للدعم العام. احتفظ بمعلوماتك الصحية والمرفقات خارجها.",
           "human": "يمكنك التواصل مع فريق دعم Pulse AI عبر وسيلة التواصل أدناه.",
           "other": "هذه المحادثة للدعم العام. حافظ على خصوصية الأعراض والتسجيلات والصور والموقع وتفاصيل الأدوية.",
           "end": "في حالة الطوارئ، اتصل بخدمات الطوارئ المحلية فوراً.", "contact": "التواصل مع فريق الدعم"},
    "th": {"menu": "ยินดีต้อนรับสู่ฝ่ายช่วยเหลือ Pulse AI พิมพ์ บัญชี ความเป็นส่วนตัว หรือ เจ้าหน้าที่",
           "account": "หากมีคำถามเรื่องบัญชีหรือการชำระเงิน โปรดติดต่อฝ่ายช่วยเหลือ Pulse AI",
           "privacy": "แชตนี้สำหรับการช่วยเหลือทั่วไป โปรดเก็บข้อมูลสุขภาพและไฟล์แนบไว้เป็นส่วนตัว",
           "human": "ติดต่อเจ้าหน้าที่ Pulse AI ได้ตามช่องทางด้านล่าง",
           "other": "แชตนี้สำหรับการช่วยเหลือทั่วไป โปรดเก็บข้อมูลอาการ เสียง ภาพ ตำแหน่งที่ตั้ง และยาไว้เป็นส่วนตัว",
           "end": "หากเกิดเหตุฉุกเฉิน ให้ติดต่อบริการฉุกเฉินในพื้นที่ทันที", "contact": "ติดต่อเจ้าหน้าที่"},
    "zh": {"menu": "欢迎联系 Pulse AI 支持。请输入“账户”、“隐私”或“人工”。",
           "account": "如有账户或账单问题，请联系 Pulse AI 人工支持。", "privacy": "此处提供一般支持。请勿在此发送健康信息或附件。",
           "human": "可通过以下方式联系 Pulse AI 人工支持。", "other": "此处提供一般支持。请勿发送症状、录音、照片、位置或用药信息。",
           "end": "如遇紧急情况，请立即联系当地急救服务。", "contact": "人工支持"},
}


def opted_out(value):
    return isinstance(value, str) and value.strip().casefold() in {
        "stop", "unsubscribe", "stop messages", "إلغاء", "الغاء", "إيقاف", "ايقاف", "توقف",
        "إلغاء الاشتراك", "الغاء الاشتراك", "إيقاف الرسائل", "ايقاف الرسائل",
        "หยุด", "ยกเลิก", "停止", "取消订阅",
    }


def selection(value, default):
    text = value.strip().casefold() if isinstance(value, str) else ""
    for language, choices in WORDS.items():
        if text in choices:
            return language, choices[text]
    # Select language locally without capturing or transmitting the message.
    for char in text[:80]:
        if "\u0600" <= char <= "\u06ff":
            return "ar", "other"
        if "\u0e00" <= char <= "\u0e7f":
            return "th", "other"
        if "\u3400" <= char <= "\u9fff":
            return "zh", "other"
        if "a" <= char <= "z":
            return "en", "other"
    return default, "other"


def render(language, action, contact):
    words = TEXT[language]
    return f"{words[action]}\n{words['contact']}: {contact}\n{words['end']}"
