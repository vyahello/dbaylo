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
    "Я не лікар — діагнозів не ставлю й лікування не призначаю. Є сумніви — "
    "не відкладай візит до лікаря. 🩺"
)

# A compact reminder for CONTINUATION turns of a live chat/consult thread — the full disclaimer is
# shown once (the first turn / any escalation); repeating the whole paragraph every turn reads like
# a bot. The not-a-doctor framing still rides every message, just shorter.
DISCLAIMER_SHORT = "Нагадую: я не лікар, це не діагноз. 🩺"

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
    "📸 Найпростіше — просто надішли мені фото або PDF аналізів. Я їх зчитаю, збережу, "
    "покажу динаміку й поясню, що до чого. А ще — розповідай, як почуваєшся: підкажу й "
    "нагадаю, коли треба.\n\n"
    "Тільки пам'ятай: я не лікар — діагнозів не ставлю й лікування не призначаю. "
    "Коли є сумніви, порадься з лікарем.\n\n"
    "Унизу зʼявилось меню розділів 👇 Тисни «❓ Довідка», і я покажу все, що вмію."
)
HELP_TEXT = (
    "❓ Як зі мною працювати\n\n"
    "Я Дбайло — твій помічник зі здоров'я. Не треба нічого вчити напам'ять, є три прості шляхи:\n\n"
    "📸 Надсилай фото або PDF аналізів — зчитаю, збережу, покажу динаміку й поясню, що до чого.\n"
    "💬 Просто пиши, як почуваєшся чи що турбує — підкажу і, якщо треба, скерую до лікаря.\n"
    "👇 Або тисни розділ унизу (чи кнопку нижче) — я сам аналізую твої дані й пропоную, а ти "
    "підтверджуєш одним тапом.\n\n"
    "Команди «/» теж працюють — їхній список у меню «/» біля поля вводу.\n\n"
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
# Auto-routing: a freely-dropped photo read as a prescription, not lab results — switching flows.
LAB_LOOKS_LIKE_PRESCRIPTION = (
    "📋 Це більше схоже на рецепт / лист призначень, ніж на аналізи — зчитую ліки. 💊"
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
GOAL_LIST_EMPTY = "Цілей поки немає. Тисни «➕ Нова ціль» і напиши, чого хочеш досягти. 🌱"

# --- "Цілі = the agent suggests" (the AI-driven goals screen) --------------------
GOAL_PROPOSE_HEADER = (
    "🎯 Ось що я можу запропонувати як цілі — з твоїх аналізів і для гарного самопочуття. "
    "Тисни, щоб узяти собі:"
)
GOAL_ALL_SET = (
    "Поки не бачу, що підказати окремо. Тисни «➕ Своя ціль» — і напиши, чого хочеш досягти. 🌱"
)
# Neutral, data-framed goal for an out-of-range finding (no method/dose/diet implied).
GOAL_SUGGEST_NORMALIZE = "Привести {name} до норми"
BTN_GOAL_OWN = "➕ Своя ціль"
GOAL_ADOPTED_TOAST = "Взяв у цілі! 🎯 Питатиму, як просувається."
GOAL_NOT_ADOPTED = "Цю ціль не вийшло взяти — спробуй сформулювати інакше."
GOAL_ACHIEVED_TOAST = "Вітаю — ціль досягнута! 🎉"
GOAL_REMOVED_TOAST = "Прибрав ціль."
# --- Goals master-detail: short subjects in the list, full title + history in the detail ----------
GOAL_MASTER_HEADER = (
    "🎯 Цілі — над чим ти працюєш. Я памʼятаю їх, згадую в розмові та в щоденному чек-іні "
    "й питаю, як просувається. Тисни ціль, щоб глянути чи відмітити:"
)
GOAL_MASTER_SUGGEST_LABEL = "💡 Пропоную взяти:"
GOAL_MASTER_MINE_LABEL = "📌 Твої активні цілі:"
# Each suggestion / adopted goal is also listed as a text line under its label (so the headers
# aren't empty + the full goal and its 🩸/🔬/⚗️ group are readable, not just the trimmed button).
GOAL_MASTER_ITEM_LINE = "• {goal}"
BTN_GOAL_VIEW_SUG = "🎯 {subject}"  # a suggestion in the master (tap -> its detail)
BTN_GOAL_VIEW = "📌 {subject}"  # an adopted goal in the master (tap -> its detail)
GOAL_DETAIL_SUG_TITLE = "🎯 <b>{goal}</b>"  # suggestion detail
GOAL_DETAIL_MINE_TITLE = "📌 <b>{goal}</b>"  # adopted-goal detail
GOAL_DETAIL_CURRENT = "Зараз: {value} (норма {ref}) — {direction}"
GOAL_DETAIL_DIR_HIGH = "вище норми"
GOAL_DETAIL_DIR_LOW = "нижче норми"
GOAL_DETAIL_DIR_OOR = "поза нормою"
GOAL_DETAIL_HISTORY_HEADER = "Коли були поза нормою:"
GOAL_HISTORY_LINE = "• {date}: {value} (норма {ref}) {mark}"
GOAL_HISTORY_MARK_OOR = "⚠️"
GOAL_HISTORY_MARK_OK = "✓"
GOAL_DETAIL_GENERIC = "Гарна звичка для щоденного самопочуття — без чисел і поспіху."
GOAL_DETAIL_NO_HISTORY = "Поки немає виміряної динаміки по цьому показнику."
BTN_GOAL_ADOPT_DETAIL = "🎯 Взяти ціль"
BTN_GOAL_ACHIEVE_DETAIL = "✅ Досягнута"
BTN_GOAL_REMOVE_DETAIL = "🗑 Прибрати"
BTN_GOAL_BACK = "◀ Назад"
BTN_GOAL_BACK_TO_HEALTH = "◀ До проблем і цілей"  # goals view → the unified screen
# Closed-goals archive (achieved / abandoned), with restore.
BTN_GOAL_ARCHIVE = "🗄 Закриті цілі — {n}"
GOAL_ARCHIVE_HEADER = (
    "🗄 Закриті цілі — досягнуті (🎉) і прибрані (🗑). Натисни, щоб повернути ціль у роботу:"
)
BTN_GOAL_REOPEN = "↩️ {mark} {subject}"  # mark = 🎉 / 🗑 (the closed status)
GOAL_REOPEN_TOAST = "Повернув ціль у роботу. ↩️"
GOAL_ARCHIVE_MARK_ACHIEVED = "🎉"
GOAL_ARCHIVE_MARK_ABANDONED = "🗑"

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
# Shown the moment you tap 📝 Чек-ін, while the grounded prompt (a multi-second LLM call) is built —
# so the wait is explained, not blank. Edited into the real check-in question when ready.
CHECKIN_ANALYZING = (
    "🔎 Хвилинку, готую персональний чек-ін — переглядаю твої аналізи й самопочуття за останні дні…"
)
CHECKIN_SAVED = "Дякую, що поділився(-лась) 💚 Занотував."
# The single, gentle follow-up — sent once if no check-in arrived; never nags.
CHECKIN_NUDGE = "Я тут, якщо захочеш розповісти, як минув день. Без поспіху 🌿"
# A later-day SECOND touch when the user ALREADY checked in — gentle, opt-out, never guilt.
CHECKIN_FOLLOWUP = (
    "Як ти зараз? Щось змінилося відтоді? Якщо все так само — можеш не відповідати 🌿"
)
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
PROBLEM_RESOLVED = "Готово 💚 Прибрав з-під нагляду — більше не нагадуватиму про це."
PROBLEM_ASK_RENAME = "Введи нову назву для цієї проблеми:"
PROBLEM_RENAMED = "Готово, оновив назву."
BTN_PROBLEM_RESOLVED = "✅ Вирішено"
BTN_PROBLEM_RENAME = "✏️ Перейменувати"
BTN_PROBLEM_RENAME_SHORT = "✏️"  # next to the named resolve button in the consolidated list
# Draft name for a concern proposed from an out-of-range lab value (user can rename).
PROBLEM_LAB_DRAFT = "{analyte} поза нормою"

# --- "Проблеми = the agent proposes" (the AI-driven concerns screen) -------------
# The agent reads ALL labs and shows what IT sees as off — the user confirms with one tap, instead
# of typing problems by hand.
# Grouped problems screen (category master-detail): a top-level digest of what's off, grouped by
# clinical category, then drill into one group. Keeps the screen from being a scary wall.
PROBLEM_GROUP_HEADER = (
    "🔎 Я переглянув усі твої аналізи. Ось картина — за розділами:\n"
    "• категорії (🩸🔬🧪…) — що ПОЗА НОРМОЮ; тисни, щоб глянути й стежити\n"
    "• 📈 на межі — ще в нормі, але близько\n"
    "• ✅ під наглядом / 🙈 відкладені / ✔️ вирішені — що я веду / що ти відклав / що закрив\n\n"
    "🎯 Цілі — окремою кнопкою в «Моє здоровʼя»."
)
PROBLEM_GROUP_NOTHING_OFF = "🔎 Зараз нічого поза нормою."  # header when only watch/tracked remain
BTN_PROBLEM_CATEGORY = "{label} — {n}"  # label already carries an emoji (CATEGORY_NAMES)
BTN_PROBLEM_WATCH = "📈 На межі — {n}"
BTN_PROBLEM_TRACKED = "✅ Під наглядом — {n}"
BTN_PROBLEM_GOALS = "🎯 Мої цілі — {n}"  # the goals group, folded into the unified screen (#merge)
BTN_PROBLEM_DISMISSED = "🙈 Відкладені — {n}"
BTN_PROBLEM_RESOLVED_LIST = "✔️ Вирішені — {n}"  # the closed-concerns archive (re-openable)
BTN_PROBLEM_BACK = "◀ Назад"
# Category / watch / tracked / dismissed detail headers.
PROBLEM_CAT_HEADER = (
    "{label} — поза нормою.\n"
    "👁 — взяти під нагляд: нагадаю про це в щоденному чек-іні й питатиму, як воно.\n"
    "✖ — приховати, не турбувати (повернути завжди можна)."
)
PROBLEM_WATCH_HEADER = (
    "📈 На межі норми — ще в нормі, але повзе до краю. Я пильную; взяти під нагляд (👁) чи ні (✖)?"
)
PROBLEM_TRACKED_HEADER = (
    "✅ Під наглядом — показники, які я веду: нагадую про них у щоденному чек-іні й згадую в "
    "розмові.\nТисни на показник, щоб позначити ВИРІШЕНИМ (прибрати з нагляду), або ✏️ — "
    "перейменувати."
)
PROBLEM_DISMISSED_HEADER = (
    "🙈 Відкладені — це показники з аналізів, які ти відклав (✖ «не турбує»). Я про них не "
    "нагадую й не пропоную. Натисни ↩️, щоб повернути показник під нагляд:"
)
PROBLEM_RESOLVED_HEADER = (
    "✔️ Вирішені — те, що ти вже закрив. Я про них не нагадую, але памʼятаю. "
    "Натисни ↩️, щоб знову взяти під нагляд:"
)
BTN_PROBLEM_REOPEN = "↩️ {name}"
PROBLEM_REOPEN_TOAST = "Повернув під нагляд — знову нагадуватиму. ↩️"
PROBLEM_ALL_CLEAR = (
    "Зараз усе в межах норми, активних проблем немає. Я сам пригляну за новими аналізами "
    "й підкажу, якщо щось зміниться. 💚"
)
# One finding line in a detail body (plain text — analyte names are safe, no HTML).
PROBLEM_LINE_HIGH = "⚠️ {name}: {value} (норма {ref}) — вище норми"
PROBLEM_LINE_LOW = "⚠️ {name}: {value} (норма {ref}) — нижче норми"
PROBLEM_LINE_WATCH = "📈 {name}: {value} (норма {ref}) — наближається до межі"
PROBLEM_LINE_FLAG = "⚠️ {name}: {value} (норма {ref}) — позначено лабораторією"
BTN_PROBLEM_TRACK = "👁 {name}"  # the button carries the finding's name so rows aren't identical
BTN_PROBLEM_DISMISS = "✖"
BTN_PROBLEM_RESTORE = "↩️ {name}"
BTN_PROBLEM_ADD_MANUAL = "➕ Своя проблема"
PROBLEM_TRACK_TOAST = "Взяв під нагляд 👁 — шукай у «✅ Вже відстежую»."
PROBLEM_DISMISS_TOAST = "Гаразд, не турбуватиму цим. Передумаєш — поверну з «🙈 Приховані». ✖"
PROBLEM_RESTORE_TOAST = "Повернув під нагляд. ↩️"
# Persistent confirmation lines prepended to the re-rendered detail after a 👁/✖ tap, so the user
# SEES where the finding went (it was vanishing silently). {name} is the finding's display name.
PROBLEM_TRACK_NOTE = (
    "✅ Взяв «{name}» під нагляд — тепер він у «✅ Вже відстежую». "
    "Нагадаю про нього в щоденному чек-іні.\n\n"
)
PROBLEM_DISMISS_NOTE = "🙈 Приховав «{name}» — не турбуватиму. Повернути: «🙈 Приховані».\n\n"

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
# Shown at the TOP of the picker (rendered as HTML): a clean bold count of the report's out-of-range
# indicators. The flagged ones with dynamics are the ⚠️ buttons below; any WITHOUT a chart/table (a
# single measurement so far) are named on the second line so they are not lost.
CHART_PICK_FLAGGED = "⚠️ <b>Поза нормою: {n}</b> — нижче позначені ⚠️."
CHART_PICK_FLAGGED_NODYN = "Поки без динаміки (1 вимір): {names}."
BTN_CHART_ALL = "📋 Звіт по динаміці"  # one scannable text report, not a flood of chart images
BTN_CHART_PDF = "📊 PDF з усіма графіками"  # one PDF: every chart + a short description
# Same label/emoji as BTN_DYN_PDF on purpose — both buttons export a "PDF з усіма графіками", so
# they must read identically (per-report vs per-category differs only by which view they sit in).
# Carousel nav UNDER each chart photo, so you flip indicators without scrolling back to the picker.
BTN_CHART_PREV = "⬅️"
BTN_CHART_NEXT = "➡️"
# The middle button BOTH shows your position (i of n) AND taps back to the full list — one button,
# no duplicate "list" affordance.
CHART_NAV_POSITION = "📋 {i}/{n}"
CHART_FLAGGED_PREFIX = "⚠️ "  # marks an out-of-range analyte in the picker (listed first)
DYN_TREND_PREFIX = "📈 "  # marks an analyte that has a multi-date trend

# Dynamics browser: indicators grouped by clinical category, across all labs.
CATEGORY_NAMES: dict[str, str] = {
    "blood": "🩸 Кров",
    "urine": "🔬 Сеча",
    "biochem": "⚗️ Біохімія",
    "hormones": "🧬 Гормони",
    "markers": "🎗️ Онкомаркери",
    "infection": "🦠 Інфекції",
    "coagulation": "🩹 Згортання",
    "semen": "🧫 Спермограма",
    "other": "📋 Інше",
    "imaging": "🩻 Описові (МРТ/УЗД)",
}
# Compact, emoji-free category names for the report-list button ("про що аналіз": Кров/Сеча/…).
CATEGORY_SHORT: dict[str, str] = {
    "blood": "Кров",
    "urine": "Сеча",
    "biochem": "Біохімія",
    "hormones": "Гормони",
    "markers": "Онкомаркери",
    "infection": "Інфекції",
    "coagulation": "Згортання",
    "semen": "Спермограма",
    "other": "Інше",
    "imaging": "Опис",
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
MED_ASK_TIMES = (
    "Скільки разів на день приймати? Напр.: «3 рази на день» або «2 таблетки 2 рази» — "
    "я сам розкладу зручні години. (Хочеш конкретні — напиши «08:00, 20:00».)"
)
MED_BAD_TIMES = (
    "Не зрозумів. Напиши, скільки разів на день — напр. «3 рази на день» — "
    "або точні години: «08:00, 20:00»."
)
MED_ADDED = (
    "Додав ліки «{name}» — нагадуватиму о {times}. Без зазначення дози: "
    "приймай за призначенням лікаря."
)

# --- "Ліки з фото рецепта" (the agent reads a prescription) ----------------------
# The agent OCRs a doctor's prescription, the user confirms, then it sets reminders. The dose is
# stored as record-keeping and SHOWN in the confirm (it's the user's own prescription) but never put
# in a reminder and never advised by the bot (rail #1).
MED_FROM_PHOTO_ASK = (
    "Надішли фото або PDF рецепта (листа призначень) — я зчитаю ліки й час прийому, "
    "а ти підтвердиш. 📷"
)
PRESCRIPTION_RECEIVED = "Читаю рецепт… ⏳"
PRESCRIPTION_FAILED = (
    "Не вдалося зчитати рецепт. Спробуй чіткіше фото — або додай ліки вручну через «➕ Додати»."
)
PRESCRIPTION_NONE = "Не побачив тут ліків — це точно рецепт? Можеш додати вручну через «➕ Додати»."
PRESCRIPTION_CONFIRM_HEADER = (
    "Ось що я зчитав із рецепта. Перевір і підтвердь — і я налаштую нагадування "
    "(дозу лишаю як запис, у нагадуваннях її не буде):"
)
PRESCRIPTION_LINE_NO_TIME = "час не вказано — додай вручну через «➕ Додати»"
BTN_PRESCRIPTION_CONFIRM = "✅ Підтвердити"
PRESCRIPTION_SAVED = "Готово! Налаштував нагадування: {names}. 💊"
PRESCRIPTION_SAVED_SKIPPED = "Без часу (додай їх вручну через «➕ Додати»): {names}."
PRESCRIPTION_NOTHING_SAVED = (
    "У жодних ліків не було часу прийому, тож нагадувань не створив. "
    "Додай вручну через «➕ Додати» — там вкажеш час."
)

# --- Tier 1.1: reminders management ---------------------------------------------

REMINDERS_HEADER = "Твої активні нагадування (натисни, щоб переглянути):"
REMINDERS_EMPTY = "Активних нагадувань немає."
REMINDER_ITEM_CHECKIN = "🌙 Щоденний чек-ін — {when}"
# The daily check-in is AGENT-managed (it follows your active problems), so it is shown as an info
# line above the list — not as a deletable reminder (deleting it would just bring it back).
REMINDER_CHECKIN_MANAGED = (
    "🌙 Щоденний догляд: питаю, як ти, сам (наступний — {when}). Керую цим я."
)
REMINDERS_NONE_MANUAL = "Своїх нагадувань поки немає — додати можна через 💊 Ліки."
REMINDER_ITEM_MEDICATION = "💊 {name} ({times}) — {when}"
REMINDER_ITEM_REPEAT_LAB = "🧪 Повтор аналізів ({name}) — {when}"
REMINDER_ITEM_CONSULT = "🔔 {name} — {when}"
REMINDER_NEXT_UNKNOWN = "час не визначено"
REMINDER_TURNED_OFF = "🔕 Вимкнув нагадування про ці ліки."  # the /medication list's soft turn-off
REMINDER_DELETED = "🗑 Видалив нагадування."  # the reminder card's hard delete (no re-enable)
BTN_REMINDER_DELETE = "🗑 Видалити"
BTN_REMINDER_BACK = "◀ Назад"
# Reminder detail card (tap a reminder to read it; deleting it is the explicit 🗑 button — a
# turned-off reminder can't be turned back on, so the card removes it outright).
REMINDER_CARD_HINT = "Нагадаю тобі вчасно. Якщо більше не потрібно — натисни «🗑 Видалити»."
REMINDER_CARD_NEXT = "🗓 Наступне: {when}"

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
HIST_BTN_REPORT = "🔬 {date} · {kind}{lab} · {count}{flags}"  # kind = "Кров+Сеча · " or ""
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
    "порівняти. Поточні значення цього аналізу дивись у розділі «📊 Показники»."
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
# Chart caption: the analyte NAME is already the chart title, so the caption drops it and leads with
# the movement (deterministic), then an optional short educational note + a micro-disclaimer. The
# period is appended so "вимірів: 13" reads with the span it covers (the x-axis labels only a few
# dates to stay readable, so the count alone looked wrong).
CHART_DYNAMICS_LINE = "📈 {value} — {movement} · вимірів: {n}{period}"
CHART_PERIOD_SUFFIX = " за {span}"  # e.g. " за 2021–2026" / " за 2026"
# Caption under a QUALITATIVE indicator's table image (the parallel of CHART_DYNAMICS_LINE).
CHART_QUAL_DYNAMICS_LINE = "📋 {value} — {movement} · вимірів: {n}{period}"
CHART_QUAL_STABLE = "тримається стабільно"  # qualitative result hasn't changed across dates
CHART_QUAL_PREFIX = "📋 "  # marks a qualitative (table-timeline) indicator in the picker
CHART_NOTE_DISCLAIMER = "ℹ️ Загальна інформація, не діагноз — тлумачить лікар."
# Drawn on a chart whose analyte has no numeric reference captured — so a band-less plot is not
# mistaken for a broken one (the points are still coloured by the lab's own out-of-range flag).
CHART_NO_REFERENCE = "норму не вказано в аналізі"
# When a chart is opened FROM a specific report, this line keeps that context visible and the
# report's own point is ringed on the chart as "цей аналіз", so you never lose where you are.
CHART_SOURCE_CONTEXT = "🔬 З аналізу {date} · {lab}"
CHART_THIS_REPORT = "цей аналіз"  # ring label on the opened report's point
CHART_LEGEND_THIS = "цей аналіз"  # legend entry for that ring

# Dynamics REPORT (replaces the old "dump every chart"): one scannable message, problems first.
CHART_REPORT_HEADER = "📋 Динаміка — {n} показників"
CHART_REPORT_FLAGGED_HEADER = "⚠️ Поза нормою:"
CHART_REPORT_OK_HEADER = "✅ У межах норми / покращення:"
CHART_REPORT_ROW = "• {analyte}: {value} — {movement}"
CHART_REPORT_HINT = "Щоб побачити графік — обери показник вище 👆"

# One-PDF export: every chart + a short description, saved as a file.
CHART_PDF_HEADING = "Динаміка показників"
CHART_PDF_REPORT_LINE = "За аналізом від {date} · {lab}"  # the report this PDF is built from
# Cover body — a plain "what is inside this document" so it reads at a glance. (No emoji: the PDF
# font has no emoji glyphs.) Charts = numeric; tables = qualitative ("виявлено / не виявлено").
CHART_PDF_INTRO = "У звіті {n} показників. Ось як показано їхню динаміку:"
CHART_PDF_ON_CHARTS = "Графіки — {n} показників із числовим значенням"
CHART_PDF_IN_TABLES = "Таблиці — {n} якісних показників (виявлено / не виявлено)"
CHART_PDF_SINGLE_LINE = "Поки без динаміки — {n} (лише один вимір)"
CHART_PDF_SECTIONS = "Розділи: {names}"  # only when the report spans more than one category
# Full, readable category names for the PDF cover (the short "🔬 Сеча" chip reads as crude prose).
CHART_PDF_CATEGORY_LABELS: dict[str, str] = {
    "blood": "Аналіз крові",
    "urine": "Аналіз сечі",
    "biochem": "Біохімія крові",
    "hormones": "Гормони",
    "markers": "Онкомаркери",
    "infection": "Інфекції",
    "coagulation": "Згортання крові",
    "semen": "Спермограма",
    "other": "Інші показники",
    "imaging": "Описові дослідження",
}
CHART_PDF_QUAL_HEADING = "Якісні показники в динаміці"
CHART_PDF_QUAL_CHANGED = "значення змінювалося"  # a real qualitative change across dates
CHART_PDF_QUAL_COL_DATE = "Дата"  # qualitative timeline TABLE: left column header
CHART_PDF_QUAL_COL_VALUE = "Значення"  # qualitative timeline TABLE: right column header
CHART_PDF_PREPARING = "Готую PDF із графіками… ⏳"
# Per-category PDF (from the dynamics-by-category browser): one document per category, not one
# giant file across everything — easier to read. Heading carries the readable category name.
CHART_PDF_CATEGORY_HEADING = "{category} — динаміка"
CHART_PDF_CATEGORY_SUBTITLE = "За всіма твоїми аналізами"
CHART_PDF_CATEGORY_FILENAME = "Дбайло-динаміка-{category}.pdf"
BTN_DYN_PDF = "📊 PDF з усіма графіками"
CHART_PDF_EMPTY = "Поки нема даних для PDF — потрібно щонайменше два виміри одного показника."
# Filenames tie the export to a specific report — what it is (kind), when, and where (lab, no city).
CHART_PDF_FILENAME = "Дбайло-динаміка-{kind}{date}-{lab}.pdf"  # {kind} already ends with "-" or ""
CHART_SOURCE_FILENAME = (
    "Аналіз-{kind}{date}-{lab}{ext}"  # the original uploaded file, sensibly named
)
# A single trend-chart PNG: a multi-date trend of ONE indicator, so the analyte is its context
# (a single date would mislead). "Дбайло-динаміка-Еритроцити.png".
CHART_PNG_FILENAME = "Дбайло-динаміка-{analyte}.png"
CHART_PNG_FALLBACK = "показник"  # when the analyte name is empty / unreadable

# --- Contextual consultation ("Запитати Дбайло") --------------------------------
# A button on a chart / indicator / report reading that opens a grounded, multi-turn consultation
# about THAT subject — the bot answers from the real data. Every user turn is screened by the gate.
BTN_CONSULT = "💬 Запитати Дбайло"
CONSULT_PROMPT_INDICATOR = (
    "Питай, що хочеш дізнатися про «{subject}» — поясню простими словами, що це означає "
    "саме для тебе. 🩺"
)
CONSULT_PROMPT_REPORT = (
    "Питай про цей аналіз ({subject}) — що означають результати, на що звернути увагу, "
    "що робити далі. Розберемо разом. 🩺"
)
CONSULT_PROMPT_SECTION = (
    "Питай про «{subject}» з цього розбору — розберемо детальніше саме цей бік. 🩺"
)
# Appended to the consult prompt when there are past conversations about this analysis — so it's
# clear the memory is folded into "Запитати Дбайло" (a separate «Памʼять» button was confusing).
CONSULT_PROMPT_MEMORY_NOTE = (
    "\n\n💭 Памʼятаю наші попередні розмови про цей аналіз — можеш спитати «що ми вже "
    "обговорювали?» або просто продовжити."
)
CONSULT_BTN_END = "✅ Завершити розмову"
CONSULT_BTN_REMIND = "🔔 Нагадати"
CONSULT_BTN_CLINICS = "🏥 Де зробити"
# The subject label of a whole-picture consultation entered from general chat (#6).
CONSULT_GENERAL_LABEL = "твій загальний стан"
CONSULT_BTN_RESUME = "↩️ Назад до розмови"
CONSULT_ENDED = "Гаразд, завершили. Звертайся, коли матимеш ще питання. 🙂"
CONSULT_RESUMED = "Гаразд, повертаємось до розмови. Питай далі. 🩺"
# #4d — set a reminder for something agreed in the consultation (an exam / recheck / visit).
CONSULT_REMIND_ASK_LABEL = (
    "Про що нагадати? Напиши коротко — наприклад «повторити аналіз сечі», «УЗД нирок» "
    "чи «консультація уролога»."
)
CONSULT_REMIND_ASK_WHEN = (
    "Коли нагадати про «{label}»? Обери нижче або напиши дату (напр. 2026-09-01) "
    "чи період («через 2 місяці»)."
)
CONSULT_REMIND_BAD_DATE = "Не зрозумів дату 🤔 Спробуй формат РРРР-ММ-ДД або «через 2 місяці»."
CONSULT_REMIND_SET = (
    "🔔 Готово — нагадаю про «{label}» {when}. Можеш писати далі або завершити розмову."
)
# When the user asked to be "booked": we can't call the clinic, so we save the reminder WELL ahead
# of the visit (the slot isn't arranged yet — time to call + agree). {when}=fires, {visit}=visit.
CONSULT_REMIND_SET_BOOKING = (
    "🔔 Нагадаю про «{label}» {when} — завчасно, щоб ти точно встиг подзвонити й домовитися "
    "про візит на {visit} (його ж іще треба узгодити з клінікою).\n\nЗабронювати сам я не можу "
    "(немає доступу до запису клінік) — подзвони в клініку, а контакти знайду кнопкою 🏥 «Де "
    "зробити». Можеш писати далі або завершити."
)
# Asking for the same reminder twice doesn't create a second one.
CONSULT_REMIND_DUP = (
    "🔔 Таке нагадування вже стоїть — «{label}». Не дублюю. Можеш писати далі або завершити."
)
CONSULT_BTN_WHEN_1W = "Через тиждень"
CONSULT_BTN_WHEN_2W = "Через 2 тижні"
CONSULT_BTN_WHEN_1M = "Через місяць"
CONSULT_BTN_WHEN_3M = "Через 3 місяці"
CONSULT_BTN_WHEN_OTHER = "📅 Інша дата"
# #3 — the 🏥 finder: a real web search for clinics in the user's city (addresses/ratings/contacts).
CONSULT_CLINICS_ASK_CITY = (
    "У якому місті шукати заклади? Напиши, наприклад «Львів» — і я знайду конкретні варіанти "
    "з адресами й контактами. 🏥"
)
CONSULT_CLINICS_SEARCHING = "Шукаю заклади у відкритих джерелах… 🔎"
CONSULT_CLINICS_FALLBACK = (
    "Не вийшло знайти заклади зараз 🤔 Спробуй ще раз трохи пізніше, або глянь на Google Maps "
    "(«<послуга> <місто>») чи на сайті НСЗУ."
)
# Reminder fired for a consult-set item (an exam / recheck / visit) — no dose, no diagnosis.
REMINDER_CONSULT = "🔔 Нагадування: {name}. Подбай про себе вчасно. 🌿"
CONSULT_EMPTY = "Напиши, будь ласка, своє питання текстом. 🙂"
CONSULT_GONE = "Не вдалося відкрити цей показник — можливо, звіт уже видалено."
# Deterministic, safe-by-construction reply used when the LLM is unavailable or trips the guard.
CONSULT_FALLBACK = (
    "Зараз не виходить розібрати це детально. Спробуй, будь ласка, ще раз за хвилину. "
    "А якщо щось турбує — варто показати ці результати лікарю, він прочитає їх у твоєму контексті."
)

# --- Consult memory: grouped view (per analysis) + forget-all / forget-one -------
MEMORY_VIEW_HEADER = "🧠 <b>Памʼять консультацій</b>"
# Groups list: one entry per analysis we talked about, plus the general (non-anchored) chats.
MEMORY_GROUPS_INTRO = (
    "Я памʼятаю наші розмови в консультаціях. Обери, яку переглянути — кожна привʼязана до "
    "свого аналізу:"
)
MEMORY_GROUPS_COUNT = "Усього збережено реплік: {total}."
MEMORY_GROUP_ANALYTE = "📊 {name} — {n}"  # button: a conversation about one indicator's chart
MEMORY_GROUP_REPORT = "📄 {what} — {n}"  # button: a per-analysis conversation
MEMORY_GROUP_REPORT_DELETED = "📄 видалений аналіз — {n}"
MEMORY_GROUP_GENERAL = "💬 Загальні розмови — {n}"  # button: consults not tied to one subject
MEMORY_GROUP_GENERAL_TITLE = "💬 Загальні розмови"
MEMORY_REPORT_TITLE = "💭 <b>Розмови про {what}</b>"
MEMORY_VIEW_EMPTY = (
    "🧠 <b>Памʼять консультацій</b>\n\nПоки що порожня — мені ще нема чого памʼятати про наші "
    "розмови. Усе, що ми обговоримо в консультації («💬 Запитати Дбайло»), збережеться тут."
)
MEMORY_VIEW_COUNT = "Реплік у цій розмові: {total}."
MEMORY_VIEW_SHOWN = "Показую останні {shown}:"
MEMORY_ROLE_USER = "👤"
MEMORY_ROLE_BOT = "🩺"
MEMORY_BTN_BACK = "◀ Назад"
MEMORY_BTN_FORGET_ALL = "🗑 Забути все"
MEMORY_BTN_FORGET_ONE = "🗑 Забути цю розмову"
MEMORY_FORGET_CONFIRM = (
    "Забути <b>всю</b> памʼять консультацій ({total})? Я більше не памʼятатиму, про що ми "
    "говорили раніше. Це не можна скасувати."
)
MEMORY_FORGET_ONE_CONFIRM = (
    "Забути цю розмову ({total} реплік)? Памʼять про інші аналізи це не зачепить. "
    "Скасувати не можна."
)
MEMORY_BTN_FORGET_YES = "Так, забути все"
MEMORY_BTN_FORGET_ONE_YES = "Так, забути цю"
MEMORY_BTN_FORGET_NO = "Скасувати"
MEMORY_FORGET_DONE = (
    "Готово — памʼять консультацій очищено ({total}). Починаємо з чистого аркуша. 🌿"
)
MEMORY_FORGET_ONE_DONE = "Готово — цю розмову забув ({total}). Решта памʼяті лишається. 🌿"
MEMORY_FORGET_EMPTY = "Памʼять і так порожня — нема чого забувати. 🙂"
MEMORY_FORGET_CANCELLED = "Гаразд, нічого не видаляю — памʼять лишається. 🙂"

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

# Command prompts — after a button tap the dialog is already waiting, so just ask for the name (the
# user types only the drug/service, never a "/command").
NAV_ASK_DRUG = "Назви ліки, ціну яких перевірити — наприклад, «парацетамол». 💊"
NAV_ASK_SERVICE = (
    "Назви послугу — наприклад, «пологи» чи «УЗД нирок». Перевірю, чи покриває ПМГ. 🏥"
)
# 💊 Ціна ліків — propose the user's own meds (one-tap), or type another.
NAV_PRICE_OPTIONS = "💊 Перевірити ціну. Обери свої ліки або напиши інше:"
BTN_PRICE_MED = "💊 {name}"
BTN_PRICE_TYPE = "✏️ Інші ліки"

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
# The НСЗУ public dashboards (where a service is provided under ПМГ, the facility map) live under
# the official site's e-data section. We link the stable homepage — a deep path 404s when the site
# is restructured; the homepage always resolves and leads to the dashboards / facility map.
NSZU_DASHBOARD_URL = "https://nszu.gov.ua"

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

# Persistent reply-keyboard labels. Matched by EXACT equality and routed before the history-NL /
# companion handlers; they are also reset triggers for the command-cancel middleware (a menu tap
# aborts an in-progress dialog), so MENU_LABELS is the single source of truth for "what counts as a
# menu tap". The menu is ~5 AGENT-DRIVEN sections: 🩺 Моє здоровʼя aggregates analyses · problems ·
# goals · check-in, and 💊 Ліки й нагадування aggregates medications + reminders.
MENU_HEALTH = "🩺 Моє здоровʼя"
MENU_CARE = "💊 Ліки та нагадування"
MENU_PRICES = "💰 Ціни / НСЗУ"
MENU_MEMORY = "🧠 Памʼять"
MENU_HELP = "❓ Довідка"

# Legacy single-purpose labels — no longer on the keyboard (folded into the two hubs above) but kept
# as constants: their handlers still answer (so a cached old keyboard keeps working) and they remain
# inline-button captions inside the hubs.
MENU_LABS = "📊 Аналізи"
MENU_GOALS = "🎯 Цілі"
MENU_PROBLEMS = "⚕️ Проблеми"
MENU_MEDS = "💊 Ліки"
MENU_REMINDERS = "🔔 Нагадування"
MENU_CHECKIN = "📝 Чек-ін"
# The previous hub label (renamed й → та); kept so a cached old keyboard still opens the hub.
MENU_CARE_LEGACY = "💊 Ліки й нагадування"

# Current keyboard labels PLUS the legacy ones — so a tap of either (new label, or an old label from
# a keyboard a client still has cached) aborts an in-progress dialog.
MENU_LABELS: frozenset[str] = frozenset(
    {
        MENU_HEALTH,
        MENU_CARE,
        MENU_PRICES,
        MENU_MEMORY,
        MENU_HELP,
        MENU_LABS,
        MENU_GOALS,
        MENU_PROBLEMS,
        MENU_MEDS,
        MENU_REMINDERS,
        MENU_CHECKIN,
        MENU_CARE_LEGACY,
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
    ("memory", "Памʼять консультацій: перегляд і очищення"),
    ("price", "Ціна на названі ліки"),
    ("coverage", "Чи покриває ПМГ послугу"),
    ("help", "Що я вмію"),
)

# Hub intros (each shown with its inline destination buttons).
MENU_HEALTH_INTRO = (
    "🩺 Твоя картина здоровʼя в одному місці — обери, що відкрити "
    "(а новий аналіз додаси, надіславши фото або PDF):"
)
MENU_CARE_INTRO = "💊 Твої ліки та нагадування про них."
# Section-screen intros (each shown with its inline action buttons).
MENU_LABS_INTRO = "Аналізи — обери, що показати (а новий додаси, надіславши фото або PDF):"
MENU_GOALS_INTRO = "Твої цілі для здоров'я."
MENU_PROBLEMS_INTRO = "Те, що зараз турбує (за активними проблемами я роблю щоденні чек-іни)."
MENU_MEDS_INTRO = "Твої ліки та нагадування про них."
MENU_PRICES_INTRO = "Ціни на ліки та покриття за Програмою медичних гарантій (ПМГ)."

# Hub destination-button labels (the 🩺 Моє здоровʼя and 💊 Ліки й нагадування screens).
BTN_MENU_ANALYSES = "📊 Аналізи"
BTN_MENU_PROBLEMS = "⚕️ Проблеми"
BTN_MENU_GOALS = "🎯 Мої цілі"  # its own hub button (the full goals screen: suggest · archive)
BTN_MENU_CHECKIN = "📝 Чек-ін"
BTN_MENU_REMINDERS = "🔔 Нагадування"
# Section inline-button labels.
BTN_MENU_HISTORY = "📋 Переглянути історію"
BTN_MENU_GOALS_LIST = "📋 Мої цілі"
BTN_MENU_GOAL_NEW = "➕ Нова ціль"
BTN_MENU_PROB_LIST = "📋 Активні"
BTN_MENU_PROB_NEW = "➕ Додати"
BTN_MENU_MED_LIST = "📋 Мої ліки"
BTN_MENU_MED_NEW = "➕ Додати ліки"
BTN_MENU_MED_PHOTO = "📷 З фото рецепта"
BTN_MENU_PRICE = "💊 Ціна ліків"
BTN_MENU_COVERAGE = "🏥 Покриття НСЗУ"

# The single shared dialog-cancel button (clears whatever FSM is active, saves nothing).
BTN_DIALOG_CANCEL = "✖️ Скасувати"
DIALOG_CANCELLED = "Скасовано — нічого не зберіг."

# "Мої ліки" list view (reuses the per-medication turn-off button).
MED_LIST_HEADER = "💊 Твої ліки. Натисни, щоб переглянути:"
MED_LIST_EMPTY = (
    "Ліків поки немає. Тисни «➕ Додати» — назви ліки й час прийому, і я нагадуватиму. 💊"
)
MED_LIST_ITEM = "💊 {name} ({times})"
BTN_MED_VIEW = (
    "💊 {name}"  # a medication in the list -> opens its card (read, not a destructive tap)
)
# Medication card (master-detail): name + dose (record-keeping) + times + next run. The dose is a
# record here only — a REMINDER never carries a dose (rail #1).
MED_CARD_TITLE = "💊 <b>{name}</b>"
MED_CARD_DOSE = "Доза: {dose}"
MED_CARD_TIMES = "Час прийому: {times}"
MED_CARD_NEXT = "🗓 Наступне нагадування: {when}"
MED_CARD_HINT = (
    "Нагадаю тобі вчасно. Дозу тримаю як запис — у самих нагадуваннях її немає (так безпечніше)."
)
BTN_MED_TURN_OFF = "🔕 Вимкнути нагадування"
MED_TURNED_OFF_TOAST = "Вимкнув нагадування про ці ліки."
BTN_MED_FILE = "📄 Фото рецепта"  # shown only for a med read from a prescription photo
MED_FILE_GONE = "Оригінал рецепта не знайшов — можливо, його прибрали."
