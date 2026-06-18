"""Single source of truth for all user-facing Ukrainian text.

Дбайло speaks Ukrainian to the user; the codebase (identifiers, enum tokens,
rule ids, docstrings) stays English. Every string the bot can show — command
replies, triage guidance, the disclaimer, the safety-guard vocabulary — lives
here so the guard and the tests read from exactly one place.

The safety vocabulary is Ukrainian too: ``FORBIDDEN_REASSURANCES`` (phrases that
would tell the user they can skip care) and ``DOSE_DIRECTIVE_PATTERNS`` (dose /
prescription phrasing). The patterns deliberately require a dose object or a
number, so legitimate *negated* copy — e.g. the disclaimer's "я не призначаю
лікування" or advice like "не приймай ліки без призначення лікаря" — is not
falsely flagged. They match what Дбайло must never *say*, not what a user stores.
"""

from __future__ import annotations

# --- Disclaimer (attached to every triage outcome and bot reply) ----------------

DISCLAIMER = (
    "Я Дбайло — турботливий друг, а не лікар. Я не ставлю діагнозів і не призначаю "
    "лікування. Коли є сумніви — порадься з лікарем."
)

# --- Triage: care-oriented floor when no red flag matches -----------------------

FLOOR_MESSAGE = (
    "Я не бачу явних тривожних ознак у тому, що ти описав, але оцінити це замість "
    "тебе я не можу. Стеж за самопочуттям і звернись до лікаря, якщо стан "
    "триватиме, погіршиться або турбуватиме тебе."
)

# --- Triage: kidney-stone red-flag messages (keyed to rule ids) -----------------

KS_INABILITY_TO_URINATE = (
    "Якщо ти не можеш помочитися, це може означати закупорку, і це треба оглянути "
    "негайно. Будь ласка, звернись по екстрену допомогу або виклич швидку."
)
KS_FEVER_CHILLS = (
    "Висока температура з ознобом може свідчити про інфекцію, яка потребує швидкої "
    "уваги. Будь ласка, звернись по медичну допомогу сьогодні."
)
KS_FEVER_CHILLS_FLANK = (
    "Температура й озноб разом із болем у боці можуть означати інфекцію в нирці із "
    "закупоркою — це невідкладний стан. Будь ласка, негайно звернись по екстрену "
    "допомогу або виклич швидку."
)
KS_UNCONTROLLED_VOMITING = (
    "Блювання, яке не вдається зупинити, може призвести до зневоднення і потребує "
    "швидкої допомоги. Будь ласка, звернись по медичну допомогу сьогодні."
)
KS_BLOOD_FIRST_TIME = (
    "Кров у сечі, яку ти бачиш уперше, завжди має оглянути лікар. Будь ласка, "
    "запишись на прийом, щоб це перевірити."
)

# --- Bot command replies --------------------------------------------------------

START_TEXT = (
    "Привіт! Я Дбайло — твій турботливий помічник у питаннях здоров'я. 🌿\n\n"
    "Я допомагаю стежити за самопочуттям, помічати тривожні ознаки й формувати "
    "звички, які залишаються надовго.\n\n"
    f"{DISCLAIMER}\n\n"
    "Напиши /help, щоб побачити, що я вмію."
)
HELP_TEXT = (
    "Ось що я вмію поки що:\n\n"
    "/start — знайомство з Дбайлом\n"
    "/help — це повідомлення\n"
    "/checkin — швидкий щоденний чек-ін (скоро)\n\n"
    f"{DISCLAIMER}"
)
CHECKIN_STUB_TEXT = (
    "Щоденні чек-іни вже в дорозі. 🛠️\n\n"
    "Незабаром я питатиму про твій сон, воду, тренування, настрій і самопочуття — "
    "і м'яко підкажу, якщо щось варте уваги лікаря.\n\n"
    f"{DISCLAIMER}"
)

# --- Safety vocabulary (Ukrainian) ----------------------------------------------

# Phrases that amount to "you're fine / you can skip care". The engine can only
# escalate up, so none of these may ever appear in a message it emits.
FORBIDDEN_REASSURANCES: tuple[str, ...] = (
    "все добре",
    "усе добре",
    "ти здоровий",
    "ти здорова",
    "немає чого хвилюватися",
    "нема чого хвилюватися",
    "не хвилюйся",
    "немає приводу для хвилювань",
    "можеш не йти до лікаря",
    "можна не йти до лікаря",
    "не треба до лікаря",
    "не треба йти до лікаря",
    "не потрібно до лікаря",
    "лікар не потрібен",
    "лікаря не потрібно",
    "це пусте",
    "це дрібниця",
    "нічого страшного",
)

# Dose / prescription phrasing Дбайло must never produce. Each pattern requires a
# dose object or a number (see module docstring on why negated copy is safe).
DOSE_DIRECTIVE_PATTERNS: tuple[str, ...] = (
    # number + unit of measure (400 мг, 5 мл, 2,5 г)
    r"\b\d+(?:[.,]\d+)?\s?(?:мг|мкг|мл|г|грам\w*|од|мо)\b",
    # number + dosage form (2 таблетки, 3 капсули, 10 крапель, 1 доза)
    r"\b\d+\s?(?:таблетк\w*|пігулк\w*|капсул\w*|крапл\w*|доз\w*)\b",
    # "по N <form/unit>" (по 2 таблетки, по 5 мл)
    r"\bпо\s+\d+\s?(?:таблетк\w*|пігулк\w*|капсул\w*|крапл\w*|мг|мл|г)\b",
    # dosing verb + number (приймай 2, випий 1) — bare "не приймай ліки" is safe
    r"\b(?:прийма\w+|прийми|прийняти|випий|випийте|випити|пий)\s+\d+",
    # frequency (двічі на день, 3 рази на день)
    r"\b(?:раз|двічі|тричі|\d+\s+раз\w*)\s+на\s+день\b",
    # prescribe / recommend a dose (призначаю дозу, рекомендую по 2)
    r"\b(?:признач\w+|рекоменд\w+)\s+(?:дозу|дозування|по\s+\d+|\d+)",
)
