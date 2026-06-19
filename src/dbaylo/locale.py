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
    "Ось що я вмію:\n\n"
    "/start — знайомство з Дбайлом\n"
    "/help — це повідомлення\n"
    "/checkin — швидкий щоденний чек-ін\n"
    "/goal — поставити ціль для здоров'я\n"
    "/goals — переглянути свої цілі\n"
    "/problem — додати те, що турбує (щоденні чек-іни, поки актуально)\n"
    "/problems — переглянути активні проблеми\n"
    "/medication — додати нагадування про ліки\n"
    "/reminders — переглянути й вимкнути нагадування\n"
    "/history — твої збережені аналізи (файли, результати, видалення)\n"
    "/trend — динаміка одного показника, напр. /trend глюкоза\n"
    "/price — ціна на конкретні названі ліки\n"
    "/coverage — чи покриває ПМГ послугу\n\n"
    "А ще можеш просто надіслати мені фото або PDF аналізів — я зчитаю їх.\n\n"
    f"{DISCLAIMER}"
)
# Owner lock: shown once to anyone who is not the configured owner.
PRIVATE_BOT = "Вибач, це приватний бот — він працює лише для свого власника. 🔒"

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

# Dose / prescription phrasing Дбайло must never *say* (rail #1). Re-anchored in
# Stage 3 to verb/intent as the PRIMARY signal; a bare number+unit is no longer a
# hard fail (it became the weak secondary signal ``DOSE_UNIT_SOFT_PATTERN`` below),
# so benign companion numerics — body weight ("80 кг") and hydration ("1500 мл на
# день", "пий ~2 л на день") — pass. The per-time rule is MASS-only {мг, мкг, г}
# so fluid volumes {мл, л} never hard-fail; liquid-med dosing in мл is caught
# instead by the medication-verb / counted-frequency rules ("приймай 5 мл", "5 мл
# тричі на день"), which is the intended, acceptable catch.
# Shared sub-patterns: mass-amount units (NOT fluid volumes) and dosage forms.
_DOSE_FORM = r"таблетк\w*|пігулк\w*|капсул\w*|крапл\w*|крапел\w*|доз\w*"
_DOSE_MASS = r"мг|мкг|г|грам\w*|од|мо"
_COUNTED_FREQ = r"(?:раз|двічі|тричі|\d+\s+раз\w*)\s+на\s+(?:день|добу|тиждень)"

DOSE_DIRECTIVE_PATTERNS: tuple[str, ...] = (
    # medication / apply verb + number (приймай 2, вживай 3, використовуй 5 мл)
    r"\b(?:прийма\w+|прийми|прийня\w+|вживай\w*|вжива\w+|вжий|використов\w+|застосов\w+|закап\w+)"
    r"\s+(?:по\s+)?\d+",
    # drink verb + number + mass/form ONLY (випий 1 таблетку, випий 500 мг). A
    # hydration "пий 2 л" is NOT caught here — л is a fluid volume (see note above).
    rf"\b(?:випий|випийте|випити|пий)\s+(?:по\s+)?\d+(?:[.,]\d+)?\s?(?:{_DOSE_MASS}|{_DOSE_FORM})\b",
    # "по N <mass|form>" (по 500 мг, по 2 таблетки) — fluid "по 2 л" excluded.
    rf"\bпо\s+\d+(?:[.,]\d+)?\s?(?:{_DOSE_MASS}|{_DOSE_FORM})\b",
    # number + dosage form (2 таблетки, 10 крапель, 1 доза)
    rf"\b\d+\s?(?:{_DOSE_FORM})\b",
    # MASS unit + per-time (500 мг/добу, 500 мг на добу, 5 г на тиждень). мл/л are
    # excluded, so hydration "1500 мл на день" passes and "140 г/л" passes (л∉time).
    rf"\b\d+(?:[.,]\d+)?\s?(?:{_DOSE_MASS})\s*(?:/|на\s+)\s*(?:добу|день|тиждень|годин\w*)\b",
    # (mass|мл) + counted frequency (400 мг двічі на день, 5 мл тричі на день,
    # 200 мг кожні 8 годин). Bare "1500 мл на день" / "2 л щодня" lack a *counted*
    # frequency, so hydration is not caught.
    rf"\b\d+(?:[.,]\d+)?\s?(?:{_DOSE_MASS}|мл)\s+(?:{_COUNTED_FREQ}|кожні\s+\d+\s+годин\w*)\b",
    # medication verb (NOT пий/випий — those are also hydration) + a counted
    # frequency, even with no number ("приймай ліки тричі на день"). "пий воду
    # тричі на день" and "гуляй тричі на день" are deliberately not caught.
    rf"\b(?:прийма\w+|прийми|прийня\w+|вживай\w*|вжива\w+|застосов\w+|закап\w+)\b[^.!?]{{0,40}}?{_COUNTED_FREQ}",
    # prescribe / recommend a dose (призначаю дозу, рекомендую по 2)
    r"\b(?:признач\w+|рекоменд\w+)\s+(?:дозу|дозування|по\s+\d+|\d+)",
)

# Weak SECONDARY signal: a bare number + dose-ish unit, with no verb/intent. It
# does NOT hard-fail ``assert_safe_output`` (that is what lets "80 кг" / "2000 мл"
# pass); it is exposed via ``safety.contains_dose_unit_mention`` for soft routing.
DOSE_UNIT_SOFT_PATTERN: str = r"\b\d+(?:[.,]\d+)?\s?(?:мг|мкг|мл|г|грам\w*|од|мо)\b"

# Rail #6: diet / restriction phrasing Дбайло must never *say*. Mirrors the dose
# philosophy — each pattern requires a number, an imperative, or a named protocol,
# so benign cautionary copy ("голодування виснажує") and ALLOWED health-literacy
# ranges (sleep hours, hydration л/мл, activity frequency) are not flagged.
DIET_PRESCRIPTION_PATTERNS: tuple[str, ...] = (
    # restrictive calorie target (1000 ккал, 500 калорій)
    r"\b\d{2,4}\s?(?:ккал|калор\w*)\b",
    # macro-gram target (150 г білка, 30 г вуглеводів)
    r"\b\d+\s?г\s+(?:вуглевод\w*|білк\w*|білок|жир\w*|клітковин\w*|цукр\w*)",
    # fasting protocol: imperative, a duration, or a named protocol
    r"\b(?:голодуй\w*|поголодуй\w*|постуй\w*)\b",
    r"\bголодуванн\w*\s+(?:на\s+|по\s+)?\d+",
    r"\b(?:інтервальн\w+|сухе|періодичн\w+|лікувальн\w+)\s+голодуванн\w*",
)

# --- Stage 2: lab intake / confirmation loop ------------------------------------

LAB_RECEIVED = "Отримав файл. Зчитую результати… ⏳"
LAB_EXTRACTION_FAILED = (
    "Не вдалося розпізнати результати. Надішли, будь ласка, чіткіше фото або PDF, "
    "або введи значення вручну."
)
LAB_UNSUPPORTED_FILE = (
    "Я вмію читати фото (JPEG/PNG) або PDF з результатами аналізів. Спробуй надіслати такий файл."
)
LAB_CONFIRM_PROMPT = "Усе правильно?"
LAB_NORM_LABEL = "норма"
LAB_DATE_LABEL = "Дата"
LAB_LAB_LABEL = "Лабораторія"
LAB_DATE_UNKNOWN = "невідома"
LAB_LAB_UNKNOWN = "невідома"

BTN_CONFIRM_ALL = "✅ Підтвердити все"
BTN_EDIT = "✏️ Виправити"
BTN_CANCEL = "🗑 Скасувати"

LAB_EDIT_PICK = "Що виправити? Надішли номер рядка (1–{n}), або «дата» / «лабораторія»."
LAB_EDIT_NEW_VALUE = "Введи правильне значення для «{name}» (тільки число):"
LAB_EDIT_NEW_DATE = "Введи дату звіту у форматі РРРР-ММ-ДД:"
LAB_EDIT_NEW_LAB = "Введи назву лабораторії:"
LAB_EDIT_BAD_ROW = "Такого рядка немає. Спробуй ще раз."
LAB_EDIT_BAD_VALUE = "Не зрозумів число. Введи значення на кшталт 5.4."
LAB_EDIT_BAD_DATE = "Не зрозумів дату. Потрібен формат РРРР-ММ-ДД (наприклад, 2026-05-12)."
LAB_CONFIRMED = "Готово! Зберіг результати. 📈"
LAB_CANCELLED = "Скасував. Нічого не зберігаю."
LAB_NOTHING_TO_TREND = (
    "Зберіг результати. Для динаміки потрібно щонайменше два виміри одного показника."
)

# Flag markers shown next to a value (keyed by ResultFlag value).
FLAG_EMOJI: dict[str, str] = {
    "low": "⬇️",
    "normal": "✅",
    "high": "⬆️",
    "unknown": "❔",
}

# Range-relative movement phrasing (keyed by TrendDirection.name). Deliberately
# describes movement relative to the reference range — never a health verdict
# ("покращується / погіршується"). See dbaylo.labs.trends.
TREND_PHRASES: dict[str, str] = {
    "INSUFFICIENT_DATA": "замало даних, щоб побачити динаміку",
    "UNKNOWN_RANGE": "немає референсних меж, щоб оцінити відносно норми",
    "STABLE_IN_RANGE": "тримається в межах норми",
    "STABLE_OUT_OF_RANGE": "залишається поза межами норми",
    "RETURNED_TO_RANGE": "повернувся в межі норми",
    "LEFT_RANGE": "вийшов за межі норми",
    "APPROACHING_RANGE": "наближається до норми",
    "MOVING_AWAY": "віддаляється від норми",
}

# --- Stage 2: humanized summary (deterministic fallback fragments) --------------

LAB_SUMMARY_HEADER = "Ось що я бачу у твоїх аналізах:"
LAB_SUMMARY_ASK_DOCTOR = "Що з цього варто обговорити з лікарем — найкраще вирішити разом із ним."

# --- Stage 3: companion (L1) — goals --------------------------------------------

# Shown when a dialog ends with no real content (blank input, or a /command that
# aborted the dialog) — so nothing phantom is ever saved silently.
NOTHING_SAVED = "Скасовано — нічого не зберіг."

GOAL_ASK_TEXT = (
    "Яку ціль для здоров'я хочеш поставити? Напиши своїми словами — наприклад «краще "
    "спати», «більше рухатися» чи «пити достатньо води»."
)
GOAL_ACCEPTED = (
    "Чудова ціль — записав її. 🌱 Рухаймося до неї крок за кроком; я нагадуватиму й "
    "підтримуватиму, без поспіху й тиску."
)
# Redirect (Concern.REDIRECT): aggressive target -> sustainable framing, no numbers,
# deliberately NOT presented as clinical authority.
GOAL_REDIRECT_AGGRESSIVE = (
    "Я поруч, щоб допомогти тобі дійти до цього здорово. Такий темп зазвичай надто "
    "різкий — його важко втримати, і він радше виснажує, ніж допомагає. Сталі, "
    "помірні зміни тримаються набагато довше. Хочеш, поставимо м'якшу, плавнішу ціль?"
)
GOAL_LIST_HEADER = "Ось твої цілі:"
GOAL_LIST_EMPTY = "Ти ще не поставив(-ла) жодної цілі. Напиши /goal, щоб додати першу. 🌱"
GOAL_STATUS_LABELS: dict[str, str] = {
    "active": "активна",
    "achieved": "досягнута",
    "paused": "на паузі",
    "abandoned": "облишена",
}

# --- Stage 3: companion (L1) — wellness guardrail support message ----------------

# Concern.SUPPORT: a disordered-pattern signal -> sustainable framing + a gentle
# nudge toward professional help. No numbers, never a restrictive prescription.
GUARDRAIL_SUPPORT = (
    "Дякую, що ділишся цим зі мною. Те, що ти описуєш, звучить виснажливо, і я щиро "
    "хвилююся за тебе. Харчування — це підтримка, а не покарання. Будь ласка, постався "
    "до себе дбайливо й подумай про розмову з фахівцем, який допоможе знайти стійкий і "
    "безпечний шлях. Ти не сам(-а) у цьому."
)

# --- Stage 3: companion (L1) — daily check-in -----------------------------------

CHECKIN_PROMPT = (
    "Привіт 🌙 Як минув твій день? Розкажи коротко: скільки годин ти спав(-ла), скільки "
    "приблизно випив(-ла) води, чи був рух/тренування, який настрій (1–5) і чи турбує "
    "щось у самопочутті."
)
CHECKIN_SAVED = "Дякую, що поділився(-лась) 💚 Занотував."
# The single, gentle follow-up — sent once if no check-in arrived; never nags.
CHECKIN_NUDGE = "Я тут, якщо захочеш розповісти, як минув день. Без поспіху 🌿"
# Periodic "still relevant?" prompt for an active concern (Tier 1.1 §B), with a button.
CHECKIN_REVIEW_PROMPT = (
    "Чи ще турбує тебе «{name}»? Якщо вже вирішилося — познач, і я не нагадуватиму."
)

# --- Tier 1.1: problems (active concerns) ---------------------------------------

PROBLEM_ASK_TEXT = (
    "Що тебе турбує? Опиши проблему словами — наприклад «болить поперек» чи «високий тиск»."
)
PROBLEM_ADDED = (
    "Записав. Поки це актуально, я раз на день м'яко питатиму, як справи — і нагадаю "
    "перевірити, чи вже вирішилося. 🌿"
)
PROBLEM_LIST_HEADER = "Ось що зараз актуально:"
PROBLEM_LIST_EMPTY = "Зараз немає активних проблем — і я нічим не турбуватиму. Додати: /problem"
PROBLEM_RESOLVED = "Радий це чути! Познач було вирішено. 💚"
PROBLEM_ASK_RENAME = "Введи нову назву для цієї проблеми:"
PROBLEM_RENAMED = "Готово, оновив назву."
BTN_PROBLEM_RESOLVED = "✅ Вирішено"
BTN_PROBLEM_RENAME = "✏️ Перейменувати"
# Draft name for a concern proposed from an out-of-range lab value (user can rename).
PROBLEM_LAB_DRAFT = "{analyte} поза нормою"

# --- Tier 1.1: lab-flag concern offer + repeat-lab offer ------------------------

LAB_CONCERN_OFFER = (
    "Деякі показники поза нормою. Відстежувати це як активну проблему (щоденні чек-іни)?"
)
BTN_LAB_CONCERN_YES = "Так, відстежувати"
BTN_LAB_CONCERN_NO = "Ні, дякую"
LAB_REPEAT_OFFER = "Нагадати повторити аналізи згодом?"
BTN_REPEAT_1M = "Через місяць"
BTN_REPEAT_3M = "Через 3 місяці"
BTN_REPEAT_6M = "Через 6 місяців"
BTN_REPEAT_OTHER = "Інший термін"
BTN_REPEAT_NO = "Не треба"
LAB_REPEAT_ASK_CUSTOM = (
    "Через скільки нагадати? Напр.: «через 10 днів», «через 2 тижні», «через рік»."
)
LAB_REPEAT_BAD_CUSTOM = (
    "Не зрозумів термін. Спробуй: «через 10 днів» / «через 2 тижні» / «через рік»."
)
LAB_REPEAT_SET = "Гаразд, нагадаю {when}. 🗓"
LAB_REPEAT_LABEL = "повторні аналізи"

# --- Tier 1.1: medications ------------------------------------------------------

MED_ASK_NAME = "Назва ліків (як на упаковці / у призначенні лікаря)?"
MED_ASK_TIMES = "О котрій годині приймати? Напр.: «08:00» або «08:00, 20:00»."
MED_BAD_TIMES = "Не зрозумів час. Введи у форматі ГГ:ХХ, напр.: «08:00» або «08:00, 20:00»."
MED_ADDED = (
    "Додав ліки «{name}» з нагадуванням о {times}. Нагадуватиму без зазначення дози — "
    "приймай за призначенням лікаря."
)

# --- Tier 1.1: reminders management ---------------------------------------------

REMINDERS_HEADER = "Твої активні нагадування:"
REMINDERS_EMPTY = "Активних нагадувань немає."
REMINDER_ITEM_CHECKIN = "🌙 Щоденний чек-ін — {when}"
REMINDER_ITEM_MEDICATION = "💊 {name} ({times}) — {when}"
REMINDER_ITEM_REPEAT_LAB = "🧪 Повтор аналізів ({name}) — {when}"
REMINDER_NEXT_UNKNOWN = "час не визначено"
REMINDER_TURNED_OFF = "Вимкнув нагадування."
BTN_REMINDER_OFF = "🗑 Вимкнути"

# --- Tier 1.2: history & file retrieval -----------------------------------------

HIST_HEADER = "Твої збережені аналізи:"
HIST_EMPTY = "Поки немає підтверджених аналізів. Надішли фото або PDF — я збережу й відстежуватиму."
HIST_NO_DATE = "без дати"
HIST_REPORT_LINE = "📅 {date} · {lab} · {count} показників {flags}"
HIST_REPORT_UPLOADED = "   (завантажено {uploaded})"
HIST_MORE = "Показано останні {n}. Уточни: /history synevo · /history 2026-05 · /history травень"
HIST_FILE_GONE = "Файл недоступний — можливо, його переміщено або видалено з диска."
HIST_RESULTS_HEADER = "{date} · {lab}:"
BTN_HIST_FILE = "📄 Файл"
BTN_HIST_RESULTS = "📊 Результати"
BTN_HIST_DELETE = "🗑 Видалити"
BTN_HIST_TREND = "📈 Динаміка"

# Delete (two-step; shows what is being removed + any Tier 1.1 coupling).
HIST_DELETE_CONFIRM = (
    "Видалити цей аналіз НАЗАВЖДИ?\n\n{details}\n\n"
    "Зникнуть файл і всі результати. (Він залишиться в нічній резервній копії — це навмисна "
    "підстраховка.)"
)
HIST_DELETE_COUPLING = "⚠️ Разом із ним буде прибрано: {items}."
HIST_COUPLING_CONCERN = "активну проблему «{name}» (позначу вирішеною)"
HIST_COUPLING_REMINDER = "нагадування про повтор аналізів"
HIST_DELETED = "Видалив назавжди. 🗑"
HIST_DELETE_CANCELLED = "Скасував — нічого не видаляю."
BTN_DELETE_YES = "Так, видалити"
BTN_DELETE_NO = "Скасувати"

# Orphaned PENDING uploads (opt-in cleanup).
HIST_PENDING_FOOTER = "🧹 Прибрати незавершені завантаження ({n})"
HIST_PENDING_CLEANED = "Прибрав незавершені завантаження: {n}."

# Single-analyte trend across all reports.
TREND_ASK = "Який показник показати в динаміці? Напр.: /trend глюкоза"
TREND_NOT_FOUND = "Не знайшов такий показник серед твоїх аналізів."
TREND_INSUFFICIENT = "Замало даних для динаміки — потрібно щонайменше два виміри одного показника."
TREND_LINE = "📈 {analyte}: {value} — {movement}. Вимірів: {n}."

# --- Stage 3: companion (L1) — reminders ----------------------------------------

# Medication reminder text never carries a dose (rail #1): it names the medication
# and defers to the doctor's instructions. The dose lives only in the DB record.
REMINDER_MEDICATION = "🔔 Нагадування про твої ліки: {name}. Прийми так, як призначив лікар. 💊"
REMINDER_REPEAT_LAB = (
    "🔔 Нагадування: можливо, час повторити аналізи ({name}). Звернись до лабораторії, "
    "коли буде зручно."
)

# --- Stage 3: companion (L1) — conversation fallback ----------------------------

# Deterministic, safe-by-construction reply used when the LLM is unavailable or its
# output trips the safety guard. Health-literacy ranges only — no prescriptions.
COMPANION_FALLBACK = (
    "Я поруч і радий тебе чути. 🌿 Найкраще для самопочуття — стабільний сон, достатньо "
    "води, трохи руху щодня й харчування без крайнощів. Якщо щось турбує — варто "
    "порадитися з лікарем."
)

# --- Stage 3: symptom routing (deterministic free-text -> Symptom token) --------

# Limited, extensible keyword map (like labs.trends.ANALYTE_ALIASES). Keyed by
# Symptom.value. Disjoint from WELLNESS_SIGNAL_KEYWORDS by design: the vomiting
# entries describe *involuntary, uncontrolled* vomiting and deliberately do NOT
# match the self-induced "викликати блювоту" / "проносне після їжі" purging
# phrasing — triage runs first, so an overlap would mask a purging signal.
SYMPTOM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "fever": ("температура", "висока температура", "жар", "гарячка", "лихоманка"),
    "chills": ("озноб", "морозить", "тіпає від холоду"),
    "flank_pain": (
        "біль у боці",
        "біль в боці",
        "болить бік",
        "біль у попереку",
        "болить поперек",
        "ниркова коліка",
        "болить нирка",
        "болять нирки",
        "біль у нирці",
        "біль у нирках",
    ),
    "severe_pain": ("нестерпний біль", "дуже сильний біль", "гострий нестерпний біль"),
    "inability_to_urinate": (
        "не можу помочитися",
        "не можу сходити в туалет по-маленькому",
        "не виходить сеча",
        "затримка сечі",
        "не можу помочитись",
    ),
    "uncontrolled_vomiting": (
        "не можу зупинити блювоту",
        "безперервна блювота",
        "нестримна блювота",
        "постійно блюю",
        "блюю без зупину",
    ),
    "blood_in_urine": ("кров у сечі", "кров в сечі", "кров при сечовипусканні"),
}
# First-time qualifier near a blood-in-urine mention -> the rule-bearing token.
FIRST_TIME_MARKERS: tuple[str, ...] = ("вперше", "уперше", "перший раз", "перше")

# --- Stage 3: wellness guardrail signal keywords --------------------------------

# Limited, extensible map (like ANALYTE_ALIASES). Keyed by signal id; scans USER
# text (goals / check-ins). The purging entries are disjoint from the vomiting
# SYMPTOM_KEYWORDS above (self-induced vs. involuntary), so triage's earlier pass
# never masks a purging signal.
WELLNESS_SIGNAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "extreme_restriction": (
        "нічого не їм",
        "нічого не їсти",
        "перестав їсти",
        "перестала їсти",
        "майже не їм",
        "морити себе голодом",
        "морю себе голодом",
        "тижнями не їм",
    ),
    "skipped_meals": (
        "пропускаю прийоми їжі",
        "пропускаю їжу",
        "пропускаю сніданок",
        "пропускаю обід",
        "пропускаю вечерю",
        "не їм цілий день",
        "не їм весь день",
        "відмовляюся від їжі",
    ),
    "purging": (
        "викликаю блювоту",
        "викликати блювоту",
        "виблюю після їжі",
        "проносне після їжі",
        "проносне щоб схуднути",
        "очищаюся після їжі",
    ),
    "compulsive_exercise": (
        "відпрацювати з'їдене",
        "відпрацьовую їжу",
        "покарати себе тренуванням",
        "маю спалити все з'їдене",
        "не можу пропустити тренування",
        "тренуюся попри біль",
    ),
    "crash_diet_language": (
        "жорстка дієта",
        "жорстку дієту",
        "жорсткій дієті",
        "жорсткою дієтою",
        "детокс",
        "сидіти на воді",
        "сиджу на воді",
        "схуднути на 10 кг за тиждень",
        "мінус 10 кг за тиждень",
    ),
}

# --- Stage 4: price & НСЗУ navigator (L4) ---------------------------------------

# Named-drug boundary (rail #1): /price looks up the price of an EXPLICITLY named
# medicine; it never picks a drug for a symptom/condition. Refusal message:
NAV_NAMED_DRUG_ONLY = (
    "Я шукаю ціну лише на конкретно названі ліки і не підбираю препарати за симптомом "
    "чи діагнозом. Напиши точну назву ліків — і я знайду ціни. Підбір лікування — це "
    "завжди до лікаря."
)
# Patterns that mark a "pick a drug for a condition" request (-> refuse, no search).
NAV_RECOMMENDATION_REQUEST_PATTERNS: tuple[str, ...] = (
    r"\b(?:ліки|таблетк\w*|пігулк\w*|капсул\w*|засіб|засоби|препарат\w*|щось)\s+(?:від|для|при)\b",
    r"\bщо\s+(?:випити|прийняти|приймати|пити|пропити)\s+(?:від|при|для|коли)\b",
    r"\bчим\s+(?:ліку\w+|лікувати)\b",
    r"\b(?:порадь|підкажи|порекоменду\w*)\s+(?:ліки|препарат\w*|щось)\b",
)

# Command prompts.
NAV_ASK_DRUG = "Напиши точну назву ліків після /price — наприклад: /price парацетамол"
NAV_ASK_SERVICE = "Напиши назву послуги після /coverage — наприклад: /coverage пологи"

# Med prices.
NAV_PRICE_HEADER = "Ось що я знайшов по «{drug}»:"
NAV_PRICE_ITEM = "• {name} — {price} грн ({pharmacy})"
NAV_PRICE_LINK = "  {url}"
NAV_AUTO_READ = "(автоматично зчитано — перевір)"
NAV_NO_RESULTS = (
    "Не вдалося знайти ціни. Можливо, джерела зараз недоступні або назву введено неточно."
)
NAV_SOURCE_UNAVAILABLE = "Не вдалося отримати дані з: {sources}."

# Price ceiling (МОЗ граничні ціни). Exists ONLY for the reimbursement subset — for
# anything else we must NOT imply a price is normal or inflated (rail #4 extended).
NAV_CEILING_ABOVE = "⚠️ Вище за граничну (регульовану державою) ціну {limit} грн."
NAV_CEILING_WITHIN = "✅ У межах граничної (регульованої) ціни {limit} грн."
NAV_CEILING_NONE = (
    "Для цих ліків немає регульованої граничної ціни, тож я не можу сказати, завищена вона чи ні."
)

# НСЗУ coverage. The ONLY truthful output is "may be free — verify"; never a
# categorical "безкоштовно" (the data is facility-level, not per-procedure).
NAV_COVERAGE_MAYBE_FREE = (
    "Можливо, цю послугу можна отримати безкоштовно за Програмою медичних гарантій (ПМГ) "
    "у закладі, що має договір із НСЗУ. Це варто перевірити напряму: {url}"
)
NAV_COVERAGE_UNKNOWN = (
    "Не вдалося визначити покриття за ПМГ для цієї послуги. Перевір на дашборді НСЗУ: {url}"
)
NSZU_DASHBOARD_URL = "https://nszu.gov.ua/likuvannya"

# Conservative keyword map of service kinds that are clearly within a ПМГ package
# (publicly defined). A match yields only "may be free — verify", never a
# categorical claim. Keyed by package label (English id); values are Ukrainian
# service-text tokens. Limited and extensible, like SYMPTOM_KEYWORDS.
PMG_PACKAGE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "primary_care": ("сімейн", "терапевт", "первинн", "педіатр"),
    "childbirth": ("пологи", "вагітн", "кесар", "ведення вагітності"),
    "stroke": ("інсульт",),
    "heart_attack": ("інфаркт",),
    "cancer": ("хіміотерап", "променев терап", "онколог"),
    "dialysis": ("діаліз", "гемодіаліз"),
    "mental_health": ("психічн", "психіатр"),
}

# Doctor / clinic transparent aggregation (rail #4). The label is attached
# DETERMINISTICALLY by the render template, never by the LLM.
NAV_PROVIDER_HEADER = "Ось варіанти — остаточний вибір за тобою; це варто обговорити з лікарем:"
REVIEWS_NOT_OUTCOMES = "Це думки пацієнтів, а не результати лікування."
NAV_PROVIDER_NSZU_YES = "має договір із НСЗУ"
NAV_PROVIDER_NSZU_NO = "без договору з НСЗУ"

# Superlative clinical-recommendation phrasing about a named provider that the
# navigator output guard must reject (rail #4: no "best surgeon / operate here").
# Each requires a superlative/ranking AND a provider/medical noun, so neutral copy
# ("найкраще пити воду", "ось список хірургів") passes.
# Superlative adjective must sit close to a provider noun, so "найкраще обговорити
# з лікарем" (adverb + verb) does not trip — only "найкращий хірург" (best surgeon).
# The ranking tokens (№1, топ-1) carry no leading \b because they start with a
# non-word char.
NAV_SUPERLATIVE_PATTERNS: tuple[str, ...] = (
    # superlative adjective -> provider noun (best surgeon / best clinic)
    r"\b(?:найкращ\w+|найдосвідченіш\w+|найвідоміш\w+|найсильніш\w+|найрейтинговіш\w+|"
    r"найпрофесійніш\w+|топов\w+)\b[^.!?]{0,20}?\b(?:хірург\w*|лікар\w*|лікарн\w*|клінік\w*|"
    r"медцентр\w*|спеціаліст\w*|ортопед\w*|кардіолог\w*|нейрохірург\w*|уролог\w*)",
    # provider noun -> ranking (clinic #1 / top-1 surgeon)
    r"\b(?:хірург\w*|лікар\w*|лікарн\w*|клінік\w*|медцентр\w*|спеціаліст\w*)\b"
    r"[^.!?]{0,20}?(?:\bнайкращ\w+|№\s*1|номер\s+один|топ-?\s*1)",
    # "operate / get treated at" directives
    r"\b(?:оперуйс\w*|оперуйтес\w*|лікуйс\w*|лікуйтес\w*)\s+(?:у|в|саме)\b",
    # outcome guarantees about treatment
    r"\bгарантован\w+\s+(?:результат\w*|одужанн\w*|виліковуванн\w*)",
    r"\b100\s*%\s*(?:виліку\w*|успіх\w*|результат\w*|одуж\w*)",
    r"\b(?:точно|напевно)\s+виліку\w+",
)
