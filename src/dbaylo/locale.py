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
    "Я Дбайло — поруч, щоб піклуватися про твоє здоров'я й підказати, коли час до "
    "лікаря. Сам я не лікар — діагнозів не ставлю й лікування не призначаю, тож "
    "коли є сумніви, не відкладай візит."
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
    "Привіт! Я Дбайло — твій друг у питаннях здоров'я. 🌿 Пояснюю просто, без зайвої "
    "серйозності, але чесно.\n\n"
    "Розповідай, як почуваєшся, кидай аналізи — допоможу розібратися, підкажу й нагадаю, "
    "коли треба.\n\n"
    "Тільки пам'ятай: я не лікар — діагнозів не ставлю й лікування не призначаю. "
    "Коли є сумніви, порадься з лікарем.\n\n"
    "Тисни /help — покажу, що вмію."
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

LAB_RECEIVED = (
    "Отримав файл. Зчитую результати… ⏳ Великий багатосторінковий звіт може читатися "
    "кілька хвилин — зачекай, будь ласка."
)
# Shown when the exact same file (same bytes) was already confirmed — no re-extraction, no copy.
LAB_DUPLICATE = "Цей файл я вже додавав ({date}) — не дублюю. Ось збережений аналіз:"
BTN_VIEW_SAVED = "📊 Показати збережений"
BTN_DELETE_PREV = "🗑 Видалити попередню версію"  # remove the saved report so it can be re-uploaded
LAB_EXTRACTION_FAILED = (
    "Не вдалося розпізнати результати. Надішли, будь ласка, чіткіше фото або PDF, "
    "або введи значення вручну."
)
LAB_UNSUPPORTED_FILE = (
    "Я вмію читати фото (JPEG/PNG) або PDF з результатами аналізів. Спробуй надіслати такий файл."
)
LAB_CONFIRM_PROMPT = "Усе правильно?"
# Problems-first confirmation view (mirrors the history "Показники" redesign): a compact
# header + summary, only the rows that need a look, the in-range rows collapsed.
LAB_CONFIRM_HEADER = "🔬 {date} · {lab}"
LAB_CONFIRM_COUNT = "{n} показників"
LAB_CONFIRM_OOR = "⚠️ {n} поза нормою"
LAB_CONFIRM_ATTENTION = "⚠️ {n} потребують уваги"  # used when some rows are also unreadable
LAB_CONFIRM_ATT_HEADER = "⚠️ Перевір ці значення ({n}):"
LAB_CONFIRM_NORMAL_AGG = "✅ Решта {n} — у межах норми"
LAB_CONFIRM_ALL_NORMAL = "✅ Усі {n} — у межах норми"
LAB_CONFIRM_NORMAL_HEADER = "✅ У межах норми:"  # when a few in-range rows are listed by name
LAB_CONFIRM_VERIFY = "Звір позначені значення з бланком. Усе правильно?"
LAB_CONFIRM_FULL_HEADER = "📋 Усі показники:"
LAB_NORM_LABEL = "норма"
# Honest fallback when the lab flagged a row but its value (often a boxed qualitative word)
# wasn't captured — so a ⚠️ is never shown with a bare "—". Re-extraction recovers the word.
LAB_VALUE_MARKED = "поза нормою (позначено лабораторією)"
# Header before each panel's rows (combined reports: blood vs urine stay visually apart).
LAB_SECTION_HEADER = "▸ {section}"
LAB_DATE_LABEL = "Дата"
LAB_LAB_LABEL = "Лабораторія"
LAB_DATE_UNKNOWN = "невідома"
LAB_LAB_UNKNOWN = "невідома"

BTN_CONFIRM_ALL = "✅ Підтвердити все"
BTN_EDIT = "✏️ Виправити"
BTN_CANCEL = "🗑 Скасувати"
BTN_CONFIRM_SHOW_ALL = "📋 Усі {n} показників"  # expand the collapsed in-range rows
BTN_EDIT_DATE = "📅 Дата"  # one-tap edit of the two most-corrected fields
BTN_EDIT_LAB = "🔬 Лабораторія"

LAB_EDIT_PICK = "Що виправити? Надішли номер рядка (1–{n}), або «дата» / «лабораторія»."
LAB_EDIT_NEW_VALUE = "Введи правильне значення для «{name}» (тільки число):"
# These two fields ARE recognised automatically; the buttons only correct a misread, so the
# prompt shows what was recognised and frames the input as an optional fix.
LAB_EDIT_NEW_DATE = (
    "Я розпізнав дату звіту: {current}.\nЯкщо вона помилкова — введи правильну (РРРР-ММ-ДД):"
)
LAB_EDIT_NEW_LAB = "Я розпізнав лабораторію: {current}.\nЯкщо назва помилкова — введи правильну:"
BTN_EDIT_KEEP = "↩️ Лишити як є"  # back out of a field edit without changing anything
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
# Shown when the lab itself flags a row (its "зона підвищеної уваги") or a value is out
# of range — "worth a look", not a verdict. A row with no flag is shown as ✅ ("ok").
FLAG_ATTENTION = "⚠️"

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

# --- Stage 5: lab interpretation & advice ---------------------------------------
# The lab's own overall conclusion (shown in the confirm table when present).
LAB_CONCLUSION_LABEL = "📋 Висновок лабораторії"
# Deterministic fallback for the expert summary (used when the LLM is unavailable /
# its output trips the safety guard). Phrasing stays in DATA terms — never "все добре"
# / "ти здоровий" (forbidden reassurances), so the guard accepts it.
LAB_INTERPRET_ALL_NORMAL = "Показники — в межах норми."
LAB_INTERPRET_FLAGGED_HEADER = "Варто звернути увагу на:"
LAB_INTERPRET_FLAGGED_ITEM = "• {analyte}: {value}"
LAB_INTERPRET_ASK_DOCTOR = (
    "Найкраще обговорити повну картину з лікарем — він зможе оцінити її разом із твоєю історією."
)
# Per-section deterministic fragments — used when ONE parallel section fails (the rest stay LLM).
LAB_INTERPRET_OVERALL_ATTENTION = (
    "Частина показників — поза межами норми. Деталі нижче, у розділі «Варто звернути увагу»."
)
LAB_INTERPRET_HELP_GENERIC = (
    "Загальні орієнтири: збалансоване харчування, достатньо води, повноцінний сон і регулярний "
    "рух. Конкретні зміни варто обговорити з лікарем."
)
# Shown the moment the user confirms a report — the expert interpretation runs an LLM and can
# take a while, so we acknowledge immediately instead of leaving a silent gap.
LAB_INTERPRET_WORKING = "Готую розбір показників і рекомендації — це може зайняти трохи часу… ⏳"
# Startup auto-recovery: a restart interrupted the analysis (summary left pending). The cause
# (an update/reboot of the bot) is intentionally not asserted — just offer to finish it.
ANALYSIS_INTERRUPTED = (
    "Минулого разу розбір аналізу ({date} · {lab}) не завершився. Доробити зараз?"
)
BTN_FINISH_ANALYSIS = "▶️ Доробити розбір"
# Canonical section headers for the expert interpretation. The model is told to print these
# verbatim on their own line; the Telegram renderer (bot.formatting) bolds + emoji-prefixes them.
INTERPRET_SECTION_OVERALL = "Загалом"
INTERPRET_SECTION_ATTENTION = "Варто звернути увагу"
INTERPRET_SECTION_HELP = "Що допоможе"
INTERPRET_SECTION_DOCTOR = "Коли до лікаря"
# Disclaimer is set off as a P.S. block under a divider (bot.formatting).
INTERPRET_DIVIDER = "──────────"
INTERPRET_PS_PREFIX = "P.S."
# Navigable analysis (drill-down): the default message is the overview; one button per section.
# Each label says what its section is ABOUT (mirrors the section header) — "Показники" /
# "До лікаря" were too vague about what one would actually see.
BTN_ANALYSIS_OVERVIEW = "🩺 Огляд"
BTN_ANALYSIS_ATTENTION = "⚠️ Звернути увагу"
BTN_ANALYSIS_HELP = "🌿 Що робити"
BTN_ANALYSIS_DOCTOR = "🧑‍⚕️ Коли до лікаря"

# --- Stage 6: narrative / imaging documents (МРТ / УЗД / висновок) ---------------
LAB_TYPE_LABEL = "Тип"
LAB_DOC_GENERIC = "медичний документ"
# History list line for a narrative document (no analyte count).
HIST_REPORT_LINE_DOC = "📄 {date} · {lab} · {report_type}"

# --- Stage 6: conversational symptom intake (history-taking) ---------------------
# Safe deterministic fallback when the intake LLM is unavailable / trips the guard.
INTAKE_FALLBACK = (
    "Розкажи трохи більше: де саме турбує, коли почалося, наскільки сильно і чи є ще "
    "якісь симптоми? Якщо стан гострий або швидко погіршується — краще одразу звернутися "
    "по медичну допомогу."
)

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
# One batched review message lists every concern due for review; each gets its own
# "✅ <name>" button. Tapping one resolves that concern and leaves the others tappable.
CHECKIN_REVIEW_HEADER = (
    "Чи ще актуальні ці питання? Познач, що вже вирішилося — і я не нагадуватиму про це."
)
BTN_PROBLEM_RESOLVED_NAMED = "✅ {name}"

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
# Charts are opt-in (shown only when there is a real multi-date trend); {n} is how many.
LAB_CHARTS_OFFER = "📈 Є динаміка за {n} показник(ами) у часі. Показати графіки?"
BTN_SHOW_CHARTS = "📈 Показати графіки"
LAB_CHARTS_EMPTY = (
    "Поки немає динаміки для графіків — потрібно ще принаймні два виміри в різні дати."
)
# Charts are OFFERED (yes/no), never auto-opened — then the picker shows one button per trending
# analyte → its single chart, instead of a wall of images.
LAB_CHARTS_PROMPT = "📈 Показати динаміку показників у часі?"
BTN_CHARTS_SHOW = "📈 Так, показати"
BTN_CHARTS_SKIP = "Ні, дякую"
CHART_PICK_HEADER = "📈 Обери показник, щоб побачити його динаміку:"
BTN_CHART_ALL = "📊 Показати всі графіки"
CHART_FLAGGED_PREFIX = "⚠️ "  # marks an out-of-range analyte in the picker (listed first)
DYN_TREND_PREFIX = "📈 "  # marks an analyte that has a multi-date trend

# Dynamics browser: indicators grouped by clinical category, across all labs.
CATEGORY_NAMES: dict[str, str] = {
    "blood": "🩸 Кров",
    "urine": "🔬 Сеча",
    "biochem": "⚗️ Біохімія",
    "hormones": "🧬 Гормони",
    "semen": "🧫 Спермограма",
    "other": "📋 Інше",
    "imaging": "🩻 Описові (МРТ/УЗД)",
}
DYN_HEADER = "📈 Динаміка показників. Обери категорію:"
DYN_CATEGORY_HEADER = "{category} — обери показник (📈 є динаміка, ⚠️ востаннє поза нормою):"
DYN_IMAGING_HEADER = "🩻 Описові дослідження. Обери, щоб переглянути:"
DYN_EMPTY = "Поки немає збережених показників. Надішли фото або PDF аналізів — і я відстежуватиму."
DYN_BTN_BACK = "◀ Категорії"
BTN_DYN_BROWSE = "📈 Динаміка по категоріях"  # entry from the /history list
# Trend-chart legend (the only user-facing text drawn onto the chart besides the title/unit).
CHART_LEGEND_RANGE = "норма"
CHART_LEGEND_OK = "у нормі"
CHART_LEGEND_OUT = "поза нормою"

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
# A lab button whose flow already ended (state lost on restart / a menu tap reset it).
LAB_OFFER_EXPIRED = (
    "Ця дія вже неактуальна. Аналіз збережено — відкрий /history, щоб попрацювати з ним."
)

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
HIST_REPORT_LINE = "🔬 {date} · {lab} · {count} показників {flags}"
HIST_REPORT_UPLOADED = "   (завантажено {uploaded})"
HIST_MORE = "Показано останні {n}. Уточни: /history synevo · /history 2026-05 · /history травень"
HIST_FILE_GONE = "Файл недоступний — можливо, його переміщено або видалено з диска."
HIST_RESULTS_HEADER = "{date} · {lab}:"
# Premium bold one-line titles for a results view (rendered as <b> at send). The doc form leads
# with the study type and omits an unknown lab (showing "невідома" there was noise).
HIST_TITLE_LAB = "🔬 {date} · {lab}"
HIST_TITLE_DOC = "📄 {parts}"

# Master-detail list: one tappable button per report; a card with actions on open.
HIST_LIST_HEADER = "🗂 Твої аналізи ({n}). Обери, щоб переглянути:"
HIST_PAGE_LABEL = "сторінка {page} з {pages}"
HIST_BTN_REPORT = "🔬 {date} · {lab} · {count}{flags}"
HIST_BTN_REPORT_DOC = "📄 {date} · {lab} · {report_type}"
HIST_BTN_REPORT_DOC_NOLAB = "📄 {date} · {report_type}"  # imaging study with no lab brand
HIST_FLAGS_SUFFIX = " ⚠️{n}"  # appended to the button when {n} values are out of range
HIST_CARD = "🔬 {date} · {lab}\n{count} показників · {status}"
HIST_CARD_DOC = "📄 {date} · {lab}\n{report_type}"
HIST_CARD_DOC_NOLAB = "📄 {date}\n{report_type}"
HIST_CARD_FLAGGED = "⚠️ {n} поза нормою"
HIST_CARD_NORMAL = "✅ усі в межах норми"
BTN_HIST_FILE = "📄 Файл"
BTN_HIST_RESULTS = "📊 Показники"
BTN_HIST_RESULTS_ALL = "📋 Усі показники"
BTN_HIST_DYNAMICS = "📈 Динаміка"
BTN_HIST_INTERPRET = "🔬 Розбір"
BTN_HIST_DELETE = "🗑 Видалити"
# Two different navigations must never be confused: the TRIANGLE "◀ Назад" goes UP a level (to the
# card / list), while the LONG arrows ⬅️/➡️ + words page WITHIN the current list. Distinct glyph
# (triangle vs long arrow) AND a word, so the user always knows what each does.
BTN_HIST_BACK = "◀ Назад"
BTN_HIST_PREV = "⬅️ Попередні"
BTN_HIST_NEXT = "Наступні ➡️"

# Problems-first results (only the out-of-range rows; normal ones aggregated).
HIST_PROBLEMS_HEADER = "⚠️ Поза нормою ({n}):"
HIST_PROBLEMS_NORMAL_AGG = "✅ Решта {n} — у межах норми."
HIST_PROBLEMS_NORMAL_HEADER = "✅ У межах норми:"  # when a few in-range rows are listed by name
HIST_NO_PROBLEMS = "✅ Усі показники — в межах норми."
HIST_DYNAMICS_EMPTY = (
    "Поки немає динаміки показників — потрібен ще один аналіз в інший день, щоб було що "
    "порівняти. Щоб подивитися конкретний показник: /trend <назва>."
)
# Cached analysis actions.
BTN_INTERP_REFRESH = "🔄 Оновити розбір"
BTN_INTERP_DELETE = "🗑 Видалити розбір"
HIST_INTERP_DELETED = "Розбір видалено. Натисни «🔬 Розбір», щоб зробити новий."

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


# --- Tier 1.3: button menu (persistent reply keyboard + section screens) ---------

# Persistent reply-keyboard labels. These are matched by EXACT equality and routed
# before the history-NL / companion handlers; they are also reset triggers for the
# command-cancel middleware (a menu tap aborts an in-progress dialog), so MENU_LABELS
# must stay the single source of truth for "what counts as a menu tap".
MENU_LABS = "📊 Аналізи"
MENU_GOALS = "🎯 Цілі"
MENU_PROBLEMS = "⚕️ Проблеми"
MENU_MEDS = "💊 Ліки"
MENU_REMINDERS = "🔔 Нагадування"
MENU_PRICES = "💰 Ціни/НСЗУ"
MENU_CHECKIN = "📝 Чек-ін"
MENU_HELP = "❓ Довідка"

MENU_LABELS: frozenset[str] = frozenset(
    {
        MENU_LABS,
        MENU_GOALS,
        MENU_PROBLEMS,
        MENU_MEDS,
        MENU_REMINDERS,
        MENU_PRICES,
        MENU_CHECKIN,
        MENU_HELP,
    }
)

# Native Telegram "/" command menu (set via set_my_commands on startup). Each pair is
# (command, short Ukrainian description). This is the single source of truth for the command
# palette; a parity test asserts every registered command handler has an entry here, so the
# "/" menu can never silently drift out of sync with the handlers. Order = display order.
BOT_COMMANDS: tuple[tuple[str, str], ...] = (
    ("start", "Головне меню і знайомство"),
    ("checkin", "Швидкий щоденний чек-ін"),
    ("history", "Збережені аналізи: файли, результати"),
    ("dynamics", "Динаміка показників по категоріях"),
    ("trend", "Динаміка показника, напр. /trend глюкоза"),
    ("goals", "Мої цілі для здоров'я"),
    ("goal", "Поставити нову ціль"),
    ("problems", "Активні проблеми, що турбують"),
    ("problem", "Додати те, що турбує"),
    ("medication", "Нагадування про ліки"),
    ("reminders", "Переглянути й вимкнути нагадування"),
    ("price", "Ціна на названі ліки"),
    ("coverage", "Чи покриває ПМГ послугу"),
    ("help", "Що я вмію"),
)

# Section-screen intros (each shown with its inline action buttons).
MENU_LABS_INTRO = "Аналізи. Надішли фото або PDF, щоб додати новий — я зчитаю й збережу."
MENU_GOALS_INTRO = "Твої цілі для здоров'я."
MENU_PROBLEMS_INTRO = "Те, що зараз турбує (за активними проблемами я роблю щоденні чек-іни)."
MENU_MEDS_INTRO = "Твої ліки та нагадування про них."
MENU_PRICES_INTRO = "Ціни на ліки та покриття за Програмою медичних гарантій (ПМГ)."

# Section inline-button labels.
BTN_MENU_HISTORY = "📋 Переглянути історію"
BTN_MENU_GOALS_LIST = "📋 Мої цілі"
BTN_MENU_GOAL_NEW = "➕ Нова ціль"
BTN_MENU_PROB_LIST = "📋 Активні"
BTN_MENU_PROB_NEW = "➕ Додати"
BTN_MENU_MED_LIST = "📋 Мої ліки"
BTN_MENU_MED_NEW = "➕ Додати"
BTN_MENU_PRICE = "💊 Ціна ліків"
BTN_MENU_COVERAGE = "🏥 Покриття НСЗУ"

# The single shared dialog-cancel button (clears whatever FSM is active, saves nothing).
BTN_DIALOG_CANCEL = "✖️ Скасувати"
DIALOG_CANCELLED = "Скасовано — нічого не зберіг."

# "Мої ліки" list view (reuses the per-medication turn-off button).
MED_LIST_HEADER = "Твої ліки:"
MED_LIST_EMPTY = "Ліків поки немає. Додай через «➕ Додати» або /medication."
MED_LIST_ITEM = "💊 {name} ({times})"
