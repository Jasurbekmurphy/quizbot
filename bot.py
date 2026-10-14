import asyncio
import logging
import random
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from html import escape
from urllib.parse import quote

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonPollType,
    Message,
    PollAnswer,
    ReplyKeyboardMarkup,
)

import db
from config import ADMIN_IDS, BOT_TOKEN
from importer import MAX_OPTIONS, make_docx_template, make_xlsx_template, parse_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("quiz")

dp = Dispatcher()
private = F.chat.type == "private"

TIME_CHOICES = [10, 15, 20, 30, 45, 60, 120, 180, 300]
DEFAULT_TIME = 30
MIN_TIME, MAX_TIME = 5, 600  # Telegram viktorinasi shu oraliqda yopiladi
PAGE = 10                    # savollar ro'yxatida bir sahifada nechta savol
LETTERS = "ABCDEFGHIJ"
OPT_PREFIX = re.compile(r"^\s*(?:[A-Ja-j]|\d{1,2})\s*[).]\s+")  # «A) », «1. » kabi belgilarni olib tashlash
COUNTDOWN = 3          # boshlashdan oldin kutish (soniya)
PRIVATE_IDLE_STOP = 2  # shaxsiy chatda ketma-ket shuncha savolga javob berilmasa, test to'xtaydi
GROUP_IDLE_STOP = 3    # guruhda ketma-ket shuncha savolga hech kim javob bermasa, test to'xtaydi

BTN_NEW = "➕ Yangi test"
BTN_MY = "📚 Testlarim"
BTN_TEMPLATES = "📄 Shablonlar"
BTN_HELP = "❓ Yordam"
BTN_CANCEL = "❌ Bekor qilish"
BTN_BACK = "⬅️ Orqaga"
BTN_FINISH = "🏁 Tugatish"
BTN_POLL = "📊 Tayyor oynada tuzish"
NOT_MENU = ~F.text.startswith("/") & ~F.text.in_({BTN_NEW, BTN_MY, BTN_TEMPLATES, BTN_HELP, BTN_CANCEL, BTN_BACK, BTN_FINISH})
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_MY)],
        [KeyboardButton(text=BTN_TEMPLATES), KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
)
CANCEL_KB = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)

HELP = """<b>Test boti</b>

Savollarni bitta Excel yoki Word faylda birdaniga yuborasiz yoki botning o'zida birma-bir yozasiz.

<b>Test yaratish</b>
1. Pastdagi «➕ Yangi test» tugmasini bosing va test nomini yozing.
2. Savollar faylini yuboring (Excel, Word yoki .txt; namunasi «📄 Shablonlar» tugmasida) yoki «✍️ Qo'lda kiritish» ni bosib savollarni bittalab yozing.
3. Hamma savollarga bir xil vaqt qo'ying. Biror savolga boshqa vaqt kerak bo'lsa, ro'yxatdan o'sha savol raqamini bosib vaqtini o'zgartiring.
4. Aralashtirishni tanlang. Tayyor!

<b>Testni o'tkazish</b> («📚 Testlarim» → test)
• «▶️ Boshlash» — o'zingiz shu yerda yechasiz.
• «👥 Guruhda boshlash» — guruhni tanlaysiz, u yerda «▶️ Boshlash» bosilganda test boshlanadi va oxirida reyting chiqadi.
• «📤 Ulashish» — havolani boshqalarga yuborasiz, ular testni botda yechadi.

Ketayotgan testni «⏹ To'xtatish» tugmasi bilan to'xtatasiz."""


def can_create(user_id: int) -> bool:
    return not ADMIN_IDS or user_id in ADMIN_IDS


def fmt_secs(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds} soniya"
    m, s = divmod(seconds, 60)
    return f"{m} daq {s} s" if s else f"{m} daqiqa"


def fmt_limit(seconds: int) -> str:
    return fmt_secs(seconds)


def times_text(times: list) -> str:
    if not times:
        return f"har savolga {fmt_limit(DEFAULT_TIME)}"
    lo, hi = min(times), max(times)
    if lo == hi:
        return f"har savolga {fmt_limit(lo)}"
    return f"har savolga alohida vaqt ({fmt_limit(lo)} – {fmt_limit(hi)})"


def quiz_times(quiz) -> list:
    return [q["time_limit"] or quiz["time_limit"] for q in db.quiz_questions(quiz["id"])]


def shuffle_text(quiz) -> str:
    if quiz["shuffle_q"] and quiz["shuffle_a"]:
        return "savollar va javoblar aralashtiriladi"
    if quiz["shuffle_a"]:
        return "faqat javoblar aralashtiriladi"
    if quiz["shuffle_q"]:
        return "faqat savollar aralashtiriladi"
    return "aralashtirilmaydi"


# ---------- Test kartasi ----------

async def quiz_card(bot: Bot, quiz_id: int, for_group: bool = False):
    quiz = db.get_quiz(quiz_id)
    if not quiz:
        return "Bu test topilmadi (o'chirilgan bo'lishi mumkin).", None
    n = db.question_count(quiz_id)
    text = (
        f"📝 <b>{escape(quiz['title'])}</b>\n\n"
        f"🖊 {n} ta savol\n⏱ {times_text(quiz_times(quiz)).capitalize()}\n🔀 {shuffle_text(quiz)}"
    )
    if for_group:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="▶️ Boshlash", callback_data=f"run:{quiz_id}")]])
        return text + "\n\nBoshlash uchun tugmani bosing.", kb
    me = await bot.me()
    link = f"https://t.me/{me.username}?start=q{quiz_id}"
    played = db.result_count(quiz_id)
    if played:
        text += f"\n👤 {played} kishi yechgan"
    text += f"\n\n🔗 Havola: {link}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Boshlash", callback_data=f"run:{quiz_id}")],
        [InlineKeyboardButton(text="👥 Guruhda boshlash", url=f"https://t.me/{me.username}?startgroup=q{quiz_id}")],
        [InlineKeyboardButton(
            text="📤 Ulashish",
            url=f"https://t.me/share/url?url={quote(link)}&text={quote('📝 ' + quiz['title'])}",
        )],
        [InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data=f"set:{quiz_id}"),
         InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"del:{quiz_id}")],
    ])
    return text, kb


# ---------- Boshlash, yordam, shablonlar ----------

@dp.message(CommandStart(deep_link=True))
async def start_deep(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    arg = command.args or ""
    if not (arg.startswith("q") and arg[1:].isdigit()):
        await cmd_start(message, state)
        return
    quiz_id = int(arg[1:])
    group = message.chat.type in ("group", "supergroup")
    text, kb = await quiz_card(bot, quiz_id, for_group=group)
    if not group and kb:  # boshqa birovning testi: faqat boshlash tugmasi
        quiz = db.get_quiz(quiz_id)
        if quiz["owner_id"] != message.from_user.id:
            text, kb = await quiz_card(bot, quiz_id, for_group=True)
    if not group:
        await message.answer("👋 Salom!", reply_markup=MAIN_KB)  # pastki menyu tugmalari
    await message.answer(text, reply_markup=kb)


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if message.chat.type != "private":
        await message.answer("Salom! Testni guruhda boshlash uchun botning shaxsiy chatida «📚 Testlarim» dan testni oching va «👥 Guruhda boshlash» ni bosing.")
        return
    await state.clear()
    if can_create(message.from_user.id):
        await message.answer(HELP, reply_markup=MAIN_KB)
    else:
        await message.answer("Salom! Testni yechish uchun sizga yuborilgan havolani oching.")


@dp.message(private, Command("help", "yordam"))
@dp.message(private, F.text == BTN_HELP)
async def cmd_help(message: Message):
    await message.answer(HELP, reply_markup=MAIN_KB if can_create(message.from_user.id) else None)


@dp.message(private, Command("shablon"))
@dp.message(private, F.text == BTN_TEMPLATES)
async def cmd_templates(message: Message):
    await message.answer_document(
        BufferedInputFile(make_xlsx_template(), filename="test_shablon.xlsx"),
        caption=(
            "📊 <b>Excel shablon</b>\nA ustun — savol, B ustun — <b>to'g'ri javob</b>, "
            "C, D, E... — noto'g'ri javoblar. Bot javoblarni o'zi aralashtiradi."
        ),
    )
    await message.answer_document(
        BufferedInputFile(make_docx_template(), filename="test_shablon.docx"),
        caption=(
            "📄 <b>Word shablon</b>\n<code>? Savol</code>\n<code>+ To'g'ri javob</code>\n<code>- Noto'g'ri javob</code>\n\n"
            "«1. Savol / A) ... *B) to'g'ri» yoki oxirida «Javob: B» ko'rinishi, HEMIS formati "
            "(==== va #) ham qabul qilinadi."
        ),
    )


@dp.message(private, F.text == BTN_CANCEL)
@dp.message(private, Command("bekor"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=MAIN_KB)


# ---------- Yangi test ----------

class NewQuiz(StatesGroup):
    title = State()
    file = State()


class Manual(StatesGroup):
    text = State()
    options = State()
    correct = State()
    time = State()


class QTime(StatesGroup):
    value = State()


def time_buttons(prefix: str, current: int | None = None) -> list:
    buttons = [
        InlineKeyboardButton(text=("✅ " if t == current else "") + fmt_limit(t), callback_data=f"{prefix}{t}")
        for t in TIME_CHOICES
    ]
    return [buttons[i:i + 3] for i in range(0, len(buttons), 3)]


def parse_seconds(text: str) -> int | None:
    """«45», «45 soniya», «2 daqiqa», «1:30» -> soniya. Telegram cheklovi: 5..600."""
    t = text.strip().lower().replace(",", ".")
    m = re.fullmatch(r"(\d+):(\d{1,2})", t)
    if m:
        value = int(m[1]) * 60 + int(m[2])
    else:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|sek|sekund|soniya|m|min|daq|daqiqa)?\.?", t)
        if not m:
            return None
        value = float(m[1]) * (60 if m[2] in ("m", "min", "daq", "daqiqa") else 1)
    value = int(round(value))
    return value if MIN_TIME <= value <= MAX_TIME else None


TIME_HINT = f"yoki istalgan vaqtni yozib yuboring: masalan <code>25</code>, <code>90</code> yoki <code>2 daqiqa</code> ({MIN_TIME}–{MAX_TIME} soniya)."
TIME_ERROR = f"❗️ Vaqtni son bilan yozing, masalan <code>25</code> yoki <code>2 daqiqa</code>. {MIN_TIME} soniyadan 10 daqiqagacha bo'lishi mumkin."


@dp.message(private, Command("yangi"))
@dp.message(private, F.text == BTN_NEW)
async def new_quiz(message: Message, state: FSMContext):
    if not can_create(message.from_user.id):
        await message.answer("Test yaratish faqat adminlarga ruxsat etilgan.")
        return
    await state.clear()
    await state.set_state(NewQuiz.title)
    await message.answer("📝 Test nomini yozing, masalan: <code>Matematika, 9-sinf, 1-chorak</code>", reply_markup=CANCEL_KB)


async def ask_source(message: Message, state: FSMContext):
    await state.set_state(NewQuiz.file)
    await message.answer(
        "Savollarni qanday kiritasiz?\n\n"
        "📎 <b>Fayl bilan</b>: Excel (.xlsx), Word (.docx) yoki .txt faylni shu yerga yuboring. "
        "Namuna fayllar: «📄 Shablonlar» tugmasida.\n\n"
        "✍️ <b>Qo'lda</b>: savollarni birma-bir yozib chiqasiz, har biriga alohida vaqt qo'yasiz.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Qo'lda kiritish", callback_data="man:start")],
        ]),
    )


@dp.message(private, NewQuiz.title, F.text, NOT_MENU)
async def new_title(message: Message, state: FSMContext):
    await state.update_data(title=" ".join(message.text.split())[:120])
    await ask_source(message, state)


@dp.message(private, F.document)
async def on_file(message: Message, state: FSMContext, bot: Bot):
    if not can_create(message.from_user.id):
        await message.answer("Test yaratish faqat adminlarga ruxsat etilgan.")
        return
    filename = message.document.file_name or "test"
    data = (await bot.download(message.document)).read()
    try:
        questions, errors = parse_file(filename, data)
    except Exception:
        log.exception("Faylni o'qib bo'lmadi")
        await message.answer("❌ Faylni o'qib bo'lmadi. U buzilmaganini tekshiring yoki «📄 Shablonlar» dagi namuna bo'yicha tayyorlang.")
        return
    if not questions:
        text = "❌ Faylda birorta ham to'g'ri tuzilgan savol topilmadi."
        if errors:
            text += "\n\n" + "\n".join(escape(e) for e in errors[:15])
        await message.answer(text + "\n\nNamuna fayllar: «📄 Shablonlar» tugmasida.")
        return
    title = (await state.get_data()).get("title") or filename.rsplit(".", 1)[0][:120]
    await state.clear()
    quiz_id = db.create_quiz(message.from_user.id, title, questions, DEFAULT_TIME)
    lines = [f"✅ <b>{len(questions)} ta savol</b> o'qildi."]
    if errors:
        lines.append(f"\n⚠️ {len(errors)} ta savol qo'shilmadi:")
        lines += [escape(e) for e in errors[:15]]
        if len(errors) > 15:
            lines.append(f"... va yana {len(errors) - 15} ta")
    await message.answer("\n".join(lines), reply_markup=MAIN_KB)
    await ask_all_time(message, state, quiz_id, 0, "n", edit=False)


# ----- Savollarni qo'lda kiritish -----

def manual_kb(count: int) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_POLL, request_poll=KeyboardButtonPollType(type="quiz"))]]
    if count:
        rows.insert(0, [KeyboardButton(text=BTN_FINISH)])
    rows.append([KeyboardButton(text=BTN_BACK), KeyboardButton(text=BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def ask_manual(message: Message, state: FSMContext, step: State, note: str = ""):
    """Qo'lda kiritishning navbatdagi qadamini so'raydi."""
    await state.set_state(step)
    d = await state.get_data()
    qs, draft = d.get("qs", []), d.get("draft", {})
    num = len(qs) + 1
    if step == Manual.text:
        text = (
            f"✍️ <b>{num}-savol</b> matnini yozing.\n\n"
            f"Yoki «{BTN_POLL}» tugmasi bilan savolni Telegramning tayyor oynasida tuzing."
        )
        if qs:
            text += f"\n\nSavollar tugagan bo'lsa, «{BTN_FINISH}» ni bosing."
        await message.answer(note + text, reply_markup=manual_kb(len(qs)))
    elif step == Manual.options:
        await message.answer(
            note + f"<b>{num}-savol:</b> {escape(draft['text'][:300])}\n\n"
            "Endi javob variantlarini yozing: <b>har birini yangi qatorda</b> (2 tadan 10 tagacha).\n"
            "Masalan:\n<code>Toshkent\nSamarqand\nBuxoro</code>",
            reply_markup=manual_kb(len(qs)),
        )
    elif step == Manual.correct:
        kb = [[InlineKeyboardButton(text=f"{LETTERS[i]}) {o[:40]}", callback_data=f"man:c:{i}")]
              for i, o in enumerate(draft["options"])]
        await message.answer(note + "✅ <b>Qaysi javob to'g'ri?</b> Tugmani bosing.",
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
    elif step == Manual.time:
        last = qs[-1]["time_limit"] if qs else None
        lines = [f"<b>{num}-savol:</b> {escape(draft['text'][:300])}", ""]
        lines += [("✅ " if i == draft["correct"] else "▫️ ") + escape(o[:80]) for i, o in enumerate(draft["options"])]
        lines += ["", "⏱ <b>Bu savolga qancha vaqt beriladi?</b>", TIME_HINT]
        await message.answer(note + "\n".join(lines),
                             reply_markup=InlineKeyboardMarkup(inline_keyboard=time_buttons("man:t:", last)))


@dp.callback_query(NewQuiz.file, F.data == "man:start")
async def manual_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await state.update_data(qs=[], draft={})
    await ask_manual(call.message, state, Manual.text)


@dp.message(private, Manual.text, F.poll)
async def manual_poll(message: Message, state: FSMContext):
    poll = message.poll
    correct = poll.correct_option_ids or ([poll.correct_option_id] if poll.correct_option_id is not None else [])
    if poll.type != "quiz" or len(correct) != 1:
        await ask_manual(message, state, Manual.text,
                         "❗️ Viktorina (quiz) turidagi, bitta to'g'ri javobi belgilangan savol yuboring.\n\n")
        return
    draft = dict(text=poll.question, options=[o.text for o in poll.options], correct=correct[0],
                 explanation=poll.explanation or "", from_poll=True)
    await state.update_data(draft=draft)
    await ask_manual(message, state, Manual.time)


@dp.message(private, Manual.text, F.text, NOT_MENU)
async def manual_text(message: Message, state: FSMContext):
    await state.update_data(draft={"text": message.text.strip()[:2000]})
    await ask_manual(message, state, Manual.options)


@dp.message(private, Manual.options, F.text, NOT_MENU)
async def manual_options(message: Message, state: FSMContext):
    options = [OPT_PREFIX.sub("", line).strip() for line in message.text.splitlines()]
    options = [o[:500] for o in options if o]
    if not 2 <= len(options) <= MAX_OPTIONS:
        await ask_manual(message, state, Manual.options,
                         f"❗️ {len(options)} ta variant yozildi. 2 tadan {MAX_OPTIONS} tagacha bo'lishi kerak, har biri yangi qatorda.\n\n")
        return
    d = await state.get_data()
    await state.update_data(draft={**d["draft"], "options": options})
    await ask_manual(message, state, Manual.correct)


@dp.callback_query(Manual.correct, F.data.startswith("man:c:"))
async def manual_correct(call: CallbackQuery, state: FSMContext):
    await call.answer()
    d = await state.get_data()
    await state.update_data(draft={**d["draft"], "correct": int(call.data.split(":")[2])})
    await call.message.edit_reply_markup(reply_markup=None)
    await ask_manual(call.message, state, Manual.time)


async def manual_add(message: Message, state: FSMContext, seconds: int):
    d = await state.get_data()
    qs = d.get("qs", []) + [{**d["draft"], "time_limit": seconds}]
    await state.update_data(qs=qs, draft={})
    await ask_manual(message, state, Manual.text, f"✅ {len(qs)}-savol qo'shildi (⏱ {fmt_limit(seconds)}).\n\n")


@dp.callback_query(Manual.time, F.data.startswith("man:t:"))
async def manual_time(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.edit_reply_markup(reply_markup=None)
    await manual_add(call.message, state, int(call.data.split(":")[2]))


@dp.message(private, Manual.time, F.text, NOT_MENU)
async def manual_time_typed(message: Message, state: FSMContext):
    seconds = parse_seconds(message.text)
    if seconds is None:
        await message.answer(TIME_ERROR)
        return
    await manual_add(message, state, seconds)


@dp.message(private, Manual.text, F.text == BTN_BACK)
@dp.message(private, Manual.options, F.text == BTN_BACK)
@dp.message(private, Manual.correct, F.text == BTN_BACK)
@dp.message(private, Manual.time, F.text == BTN_BACK)
async def manual_back(message: Message, state: FSMContext):
    step = await state.get_state()
    d = await state.get_data()
    if step == Manual.text.state:
        if not d.get("qs"):
            await ask_source(message, state)
            return
        last = d["qs"][-1]  # oxirgi savolni qayta ochamiz: vaqtini yoki o'zini o'zgartirish uchun
        await state.update_data(qs=d["qs"][:-1], draft={k: v for k, v in last.items() if k != "time_limit"})
        await ask_manual(message, state, Manual.time)
    elif step == Manual.options.state:
        await ask_manual(message, state, Manual.text)
    elif step == Manual.correct.state:
        await ask_manual(message, state, Manual.options)
    elif not d["draft"].get("from_poll"):
        await ask_manual(message, state, Manual.correct)
    else:  # Telegram oynasida tuzilgan savol: boshidan
        await ask_manual(message, state, Manual.text)


@dp.message(private, Manual.text, F.text == BTN_FINISH)
@dp.message(private, Manual.options, F.text == BTN_FINISH)
@dp.message(private, Manual.correct, F.text == BTN_FINISH)
@dp.message(private, Manual.time, F.text == BTN_FINISH)
async def manual_finish(message: Message, state: FSMContext):
    d = await state.get_data()
    qs = d.get("qs", [])
    if not qs:
        await message.answer("Hali birorta ham savol qo'shilmadi.")
        return
    await state.clear()
    default = Counter(q["time_limit"] for q in qs).most_common(1)[0][0]
    quiz_id = db.create_quiz(message.from_user.id, d.get("title") or "Test", qs, default)
    note = f"✅ {len(qs)} ta savol yozildi."
    if d.get("draft"):
        note += " Oxirgi chala savol qo'shilmadi."
    await message.answer(note, reply_markup=MAIN_KB)
    text, kb = times_view(quiz_id, 0, "n")
    await message.answer(text, reply_markup=kb)


# ----- Savollar ro'yxati va vaqtlari -----
# mode: "n" — yangi test yaratilyapti (oxirida «Davom etish»), "s" — sozlamalardan ochilgan

def times_view(quiz_id: int, page: int, mode: str):
    quiz = db.get_quiz(quiz_id)
    qs = db.quiz_questions(quiz_id)
    pages = max(1, (len(qs) + PAGE - 1) // PAGE)
    page = min(max(page, 0), pages - 1)
    chunk = qs[page * PAGE:(page + 1) * PAGE]
    lines = [f"📋 <b>{escape(quiz['title'])}</b> — {len(qs)} ta savol", ""]
    for q in chunk:
        short = " ".join(q["text"].split())
        short = short[:60] + "…" if len(short) > 60 else short
        lines.append(f"<b>{q['pos'] + 1}.</b> {escape(short)} — ⏱ <b>{fmt_limit(q['time_limit'] or quiz['time_limit'])}</b>")
    lines += ["", "Bitta savolning vaqtini o'zgartirish uchun uning raqamini bosing."]
    if pages > 1:
        lines.append(f"Sahifa {page + 1}/{pages}")
    nums = [InlineKeyboardButton(text=str(q["pos"] + 1), callback_data=f"qe:{q['id']}:{page}:{mode}") for q in chunk]
    rows = [nums[i:i + 5] for i in range(0, len(nums), 5)]
    if pages > 1:
        rows.append([
            InlineKeyboardButton(text="◀️", callback_data=f"qs:{quiz_id}:{(page - 1) % pages}:{mode}"),
            InlineKeyboardButton(text=f"{page + 1}/{pages}", callback_data="noop"),
            InlineKeyboardButton(text="▶️", callback_data=f"qs:{quiz_id}:{(page + 1) % pages}:{mode}"),
        ])
    rows.append([InlineKeyboardButton(text="⏱ Hammasiga bir xil vaqt", callback_data=f"qa:{quiz_id}:{page}:{mode}")])
    if mode == "n":
        rows.append([InlineKeyboardButton(text="➡️ Davom etish", callback_data=f"fin:{quiz_id}")])
    else:
        rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"set:{quiz_id}")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


async def show(message: Message, text: str, kb, edit: bool):
    if edit:
        try:
            await message.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
    await message.answer(text, reply_markup=kb)


async def ask_all_time(message: Message, state: FSMContext, quiz_id: int, page: int, mode: str, edit: bool = True):
    quiz = db.get_quiz(quiz_id)
    await state.set_state(QTime.value)
    await state.update_data(quiz_id=quiz_id, question_id=None, page=page, mode=mode)
    rows = time_buttons(f"qt:a{quiz_id}:{page}:{mode}:", quiz["time_limit"])
    if mode == "s":
        rows.append([InlineKeyboardButton(text="⬅️ Ro'yxatga", callback_data=f"qs:{quiz_id}:{page}:{mode}")])
    await show(message, f"⏱ <b>Hamma savollarga qancha vaqt beriladi?</b>\n{TIME_HINT}\n\n"
               "Keyin xohlagan savolning vaqtini alohida o'zgartirishingiz mumkin.",
               InlineKeyboardMarkup(inline_keyboard=rows), edit)


def quiz_of(call: CallbackQuery, quiz_id: int):
    quiz = db.get_quiz(quiz_id)
    return quiz if quiz and quiz["owner_id"] == call.from_user.id else None


@dp.callback_query(F.data == "noop")
async def noop(call: CallbackQuery):
    await call.answer()


@dp.callback_query(F.data.startswith("qs:"))
async def times_list(call: CallbackQuery, state: FSMContext):
    _, quiz_id, page, mode = call.data.split(":")
    if not quiz_of(call, int(quiz_id)):
        await call.answer("Bu test sizniki emas", show_alert=True)
        return
    await call.answer()
    if await state.get_state() == QTime.value.state:
        await state.clear()
    text, kb = times_view(int(quiz_id), int(page), mode)
    await show(call.message, text, kb, True)


@dp.callback_query(F.data.startswith("qa:"))
async def times_all(call: CallbackQuery, state: FSMContext):
    _, quiz_id, page, mode = call.data.split(":")
    if not quiz_of(call, int(quiz_id)):
        await call.answer("Bu test sizniki emas", show_alert=True)
        return
    await call.answer()
    await ask_all_time(call.message, state, int(quiz_id), int(page), mode)


@dp.callback_query(F.data.startswith("qe:"))
async def time_one(call: CallbackQuery, state: FSMContext):
    _, question_id, page, mode = call.data.split(":")
    q = db.get_question(int(question_id))
    quiz = q and quiz_of(call, q["quiz_id"])
    if not quiz:
        await call.answer("Bu savol topilmadi", show_alert=True)
        return
    await call.answer()
    await state.set_state(QTime.value)
    await state.update_data(quiz_id=quiz["id"], question_id=q["id"], page=int(page), mode=mode)
    current = q["time_limit"] or quiz["time_limit"]
    lines = [f"<b>{q['pos'] + 1}-savol:</b> {escape(q['text'][:500])}", ""]
    lines += [("✅ " if i == q["correct"] else "▫️ ") + escape(o[:80]) for i, o in enumerate(q["options"])]
    lines += ["", f"⏱ Hozir: <b>{fmt_limit(current)}</b>. Yangi vaqtni tanlang", TIME_HINT]
    rows = time_buttons(f"qt:{q['id']}:{page}:{mode}:", current)
    rows.append([InlineKeyboardButton(text="⬅️ Ro'yxatga", callback_data=f"qs:{quiz['id']}:{page}:{mode}")])
    await show(call.message, "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows), True)


def apply_time(target: str, seconds: int) -> int:
    """target: «a<quiz_id>» — hamma savollar, aks holda savol id. Test id sini qaytaradi."""
    if target.startswith("a"):
        db.set_all_times(int(target[1:]), seconds)
        return int(target[1:])
    q = db.get_question(int(target))
    db.set_question_time(q["id"], seconds)
    return q["quiz_id"]


@dp.callback_query(F.data.startswith("qt:"))
async def time_set(call: CallbackQuery, state: FSMContext):
    _, target, page, mode, seconds = call.data.split(":")
    quiz_id = int(target[1:]) if target.startswith("a") else (db.get_question(int(target)) or {}).get("quiz_id")
    if not quiz_id or not quiz_of(call, quiz_id):
        await call.answer("Bu test sizniki emas", show_alert=True)
        return
    apply_time(target, int(seconds))
    await call.answer(f"⏱ {fmt_limit(int(seconds))}")
    if await state.get_state() == QTime.value.state:
        await state.clear()
    text, kb = times_view(quiz_id, int(page), mode)
    await show(call.message, text, kb, True)


@dp.message(private, QTime.value, F.text, NOT_MENU)
async def time_typed(message: Message, state: FSMContext):
    seconds = parse_seconds(message.text)
    if seconds is None:
        await message.answer(TIME_ERROR)
        return
    d = await state.get_data()
    quiz = db.get_quiz(d["quiz_id"])
    await state.clear()
    if not quiz or quiz["owner_id"] != message.from_user.id:
        return
    target = f"a{quiz['id']}" if d["question_id"] is None else str(d["question_id"])
    apply_time(target, seconds)
    text, kb = times_view(quiz["id"], d["page"], d["mode"])
    await message.answer(f"✅ Vaqt: {fmt_limit(seconds)}\n\n" + text, reply_markup=kb)


@dp.callback_query(F.data.startswith("fin:"))
async def new_finish(call: CallbackQuery, state: FSMContext, bot: Bot):
    parts = call.data.split(":")
    quiz = quiz_of(call, int(parts[1]))
    if not quiz:
        await call.answer("Bu test sizniki emas", show_alert=True)
        return
    await call.answer()
    if await state.get_state() == QTime.value.state:
        await state.clear()
    if len(parts) == 2:
        qid = quiz["id"]
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔀 Savollar va javoblarni", callback_data=f"fin:{qid}:11")],
            [InlineKeyboardButton(text="Faqat javoblarni", callback_data=f"fin:{qid}:01")],
            [InlineKeyboardButton(text="Aralashtirmaslik", callback_data=f"fin:{qid}:00")],
            [InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"qs:{qid}:0:n")],
        ])
        await show(call.message, "🔀 <b>Har safar aralashtirilsinmi?</b>", kb, True)
        return
    flags = parts[2]
    db.update_quiz(quiz["id"], shuffle_q=int(flags[0]), shuffle_a=int(flags[1]))
    await call.message.edit_text("✅ Test saqlandi!")
    text, kb = await quiz_card(bot, quiz["id"])
    await call.message.answer(text, reply_markup=kb)


# ---------- Testlarim va sozlamalar ----------

def owned(call: CallbackQuery, quiz_id: int):
    quiz = db.get_quiz(quiz_id)
    return quiz if quiz and quiz["owner_id"] == call.from_user.id else None


@dp.message(private, Command("testlarim"))
@dp.message(private, F.text == BTN_MY)
async def cmd_my(message: Message):
    quizzes = db.user_quizzes(message.from_user.id)
    if not quizzes:
        await message.answer("Sizda hali test yo'q. Pastdagi «➕ Yangi test» ni bosing.", reply_markup=MAIN_KB)
        return
    buttons = [
        [InlineKeyboardButton(text=f"📝 {q['title'][:40]} ({q['n']})", callback_data=f"card:{q['id']}")]
        for q in quizzes[:50]
    ]
    await message.answer(f"📚 <b>Testlaringiz</b>: {len(quizzes)} ta", reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@dp.callback_query(F.data.startswith("card:"))
async def show_card(call: CallbackQuery, bot: Bot):
    await call.answer()
    quiz_id = int(call.data.split(":")[1])
    text, kb = await quiz_card(bot, quiz_id, for_group=not owned(call, quiz_id))
    await call.message.answer(text, reply_markup=kb)


def settings_kb(quiz) -> InlineKeyboardMarkup:
    qid = quiz["id"]
    rows = [[InlineKeyboardButton(text="⏱ Savollar va vaqtlari", callback_data=f"qs:{qid}:0:s")]]
    rows.append([InlineKeyboardButton(
        text=("✅" if quiz["shuffle_q"] else "▫️") + " Savollarni aralashtirish", callback_data=f"set:{qid}:q")])
    rows.append([InlineKeyboardButton(
        text=("✅" if quiz["shuffle_a"] else "▫️") + " Javoblarni aralashtirish", callback_data=f"set:{qid}:a")])
    rows.append([InlineKeyboardButton(text="✏️ Nomini o'zgartirish", callback_data=f"set:{qid}:n")])
    rows.append([InlineKeyboardButton(text="⬅️ Orqaga", callback_data=f"card:{qid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class Rename(StatesGroup):
    title = State()


@dp.callback_query(F.data.startswith("set:"))
async def quiz_settings(call: CallbackQuery, state: FSMContext):
    parts = call.data.split(":")
    quiz = owned(call, int(parts[1]))
    if not quiz:
        await call.answer("Bu test sizniki emas", show_alert=True)
        return
    await call.answer()
    if len(parts) > 2:
        if parts[2] == "q":
            db.update_quiz(quiz["id"], shuffle_q=0 if quiz["shuffle_q"] else 1)
        elif parts[2] == "a":
            db.update_quiz(quiz["id"], shuffle_a=0 if quiz["shuffle_a"] else 1)
        elif parts[2] == "n":
            await state.set_state(Rename.title)
            await state.update_data(quiz_id=quiz["id"])
            await call.message.answer("Yangi nomni yozing:", reply_markup=CANCEL_KB)
            return
        quiz = db.get_quiz(quiz["id"])
    await call.message.edit_text(
        f"⚙️ <b>{escape(quiz['title'])}</b> — sozlamalar\n\n⏱ {times_text(quiz_times(quiz)).capitalize()}", reply_markup=settings_kb(quiz)
    )


@dp.message(private, Rename.title, F.text, NOT_MENU)
async def rename_quiz(message: Message, state: FSMContext, bot: Bot):
    quiz_id = (await state.get_data())["quiz_id"]
    await state.clear()
    db.update_quiz(quiz_id, title=" ".join(message.text.split())[:120])
    await message.answer("✅ Nomi o'zgartirildi.", reply_markup=MAIN_KB)
    text, kb = await quiz_card(bot, quiz_id)
    await message.answer(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("del:"))
async def delete_quiz(call: CallbackQuery):
    parts = call.data.split(":")
    quiz = owned(call, int(parts[1]))
    if not quiz:
        await call.answer("Bu test sizniki emas", show_alert=True)
        return
    await call.answer()
    if len(parts) == 2:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Ha, o'chirish", callback_data=f"del:{quiz['id']}:yes"),
            InlineKeyboardButton(text="❌ Yo'q", callback_data=f"card:{quiz['id']}"),
        ]])
        await call.message.edit_text(f"«{escape(quiz['title'])}» testi o'chirilsinmi?", reply_markup=kb)
        return
    db.delete_quiz(quiz["id"])
    await call.message.edit_text(f"🗑 «{escape(quiz['title'])}» o'chirildi.")


# ---------- Testni o'tkazish ----------

@dataclass
class Player:
    name: str
    correct: int = 0
    wrong: int = 0
    seconds: float = 0.0


@dataclass
class Run:
    chat_id: int
    thread_id: int | None
    is_group: bool
    quiz: dict
    questions: list
    starter_id: int
    players: dict = field(default_factory=dict)
    poll_id: str = ""
    correct_option: int = 0
    sent_at: float = 0.0
    answered: set = field(default_factory=set)
    answer_event: asyncio.Event = field(default_factory=asyncio.Event)
    stopped: bool = False
    asked: int = 0
    task: asyncio.Task | None = None


runs: dict = {}       # (chat_id, thread_id) -> Run
poll_runs: dict = {}  # poll_id -> Run


def prepare_questions(quiz) -> list:
    """Har o'yin uchun savollar (va javoblar) yangidan aralashtiriladi."""
    questions = db.quiz_questions(quiz["id"])
    for q in questions:
        q["time_limit"] = q["time_limit"] or quiz["time_limit"]
    if quiz["shuffle_q"]:
        random.shuffle(questions)
    if quiz["shuffle_a"]:
        for q in questions:
            order = list(range(len(q["options"])))
            random.shuffle(order)
            q["correct"] = order.index(q["correct"])
            q["options"] = [q["options"][i] for i in order]
    return questions


async def send_question(bot: Bot, run: Run, i: int):
    q = run.questions[i]
    n = len(run.questions)
    head = f"[{i + 1}/{n}] "
    limit = q["time_limit"]
    explanation = q["explanation"][:200] or None
    if len(head) + len(q["text"]) <= 300 and all(len(o) <= 100 for o in q["options"]):
        question, options = head + q["text"], q["options"]
    else:
        # Telegram cheklovi: savol 300, javob 100 belgidan oshmasin — uzun matn alohida xabarda yuboriladi
        letters = LETTERS
        body = f"<b>{head}</b>{escape(q['text'])}\n\n" + "\n".join(
            f"<b>{letters[j]})</b> {escape(o)}" for j, o in enumerate(q["options"])
        )
        await bot.send_message(run.chat_id, body[:4096], message_thread_id=run.thread_id)
        question, options = f"{head}To'g'ri javobni tanlang", list(letters[:len(q["options"])])
    msg = await bot.send_poll(
        run.chat_id, question, options, type="quiz", correct_option_id=q["correct"], is_anonymous=False,
        open_period=limit, explanation=explanation, message_thread_id=run.thread_id,
    )
    poll_runs.pop(run.poll_id, None)
    run.poll_id, run.correct_option, run.sent_at = msg.poll.id, q["correct"], time.monotonic()
    run.answered = set()
    run.answer_event = asyncio.Event()
    poll_runs[run.poll_id] = run


async def run_quiz(bot: Bot, run: Run):
    key = (run.chat_id, run.thread_id)
    try:
        n = len(run.questions)
        await bot.send_message(
            run.chat_id,
            f"🏁 <b>«{escape(run.quiz['title'])}»</b> testi boshlanmoqda!\n\n"
            f"🖊 {n} ta savol, ⏱ {times_text([q['time_limit'] for q in run.questions])}.\n"
            + ("Kim ko'p to'g'ri va tez javob bersa, o'sha g'olib! " if run.is_group else "")
            + "\nTayyorlaning...",
            message_thread_id=run.thread_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⏹ To'xtatish", callback_data="stop"),
            ]]),
        )
        await asyncio.sleep(COUNTDOWN)
        idle = 0
        for i in range(n):
            if run.stopped:
                break
            await send_question(bot, run, i)
            run.asked = i + 1
            limit = run.questions[i]["time_limit"]
            if run.is_group:
                for _ in range(limit + 1):  # /stop darhol ishlashi uchun soniyalab kutamiz
                    if run.stopped:
                        break
                    await asyncio.sleep(1)
            else:
                try:  # shaxsiy chatda javob berilishi bilan keyingi savol
                    await asyncio.wait_for(run.answer_event.wait(), timeout=limit + 1)
                    await asyncio.sleep(1)
                except asyncio.TimeoutError:
                    pass
            if run.stopped:
                break
            idle = 0 if run.answered else idle + 1
            if idle >= (GROUP_IDLE_STOP if run.is_group else PRIVATE_IDLE_STOP):
                await bot.send_message(
                    run.chat_id,
                    "⏸ Ketma-ket bir necha savolga javob berilmadi, test to'xtatildi.",
                    message_thread_id=run.thread_id,
                )
                break
        await finish(bot, run)
    except Exception:
        log.exception("Testni o'tkazishda xato")
        try:
            await bot.send_message(run.chat_id, "⚠️ Xatolik tufayli test to'xtadi.", message_thread_id=run.thread_id)
        except Exception:
            pass
    finally:
        poll_runs.pop(run.poll_id, None)
        if runs.get(key) is run:
            runs.pop(key)


def again_kb(quiz_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🔄 Qayta ishlash", callback_data=f"run:{quiz_id}"),
    ]])


async def finish(bot: Bot, run: Run):
    quiz, asked = run.quiz, run.asked
    for uid, p in run.players.items():
        db.save_result(quiz["id"], uid, p.name, p.correct, asked, p.seconds, "group" if run.is_group else "private")
    if not run.is_group:
        p = run.players.get(run.starter_id) or Player("")
        skipped = asked - p.correct - p.wrong
        text = (
            f"🏁 <b>«{escape(quiz['title'])}»</b> testi yakunlandi!\n\n"
            f"{asked} ta savol berildi:\n\n"
            f"✅ To'g'ri — {p.correct}\n❌ Xato — {p.wrong}\n⌛️ O'tkazib yuborilgan — {skipped}\n"
            f"⏱ {fmt_secs(p.seconds)}"
        )
        await bot.send_message(run.chat_id, text, reply_markup=again_kb(quiz["id"]))
        return
    ranking = sorted(run.players.values(), key=lambda p: (-p.correct, p.seconds))
    lines = [f"🏁 <b>«{escape(quiz['title'])}»</b> testi yakunlandi!\n", f"{asked} ta savol berildi.\n"]
    if not ranking:
        lines.append("Hech kim javob bermadi.")
    medals = ["🥇", "🥈", "🥉"]
    for i, p in enumerate(ranking[:30]):
        place = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{place} {escape(p.name)} — <b>{p.correct}</b> ({fmt_secs(p.seconds)})")
    if ranking:
        lines.append("\n🏆 G'oliblarni tabriklaymiz!")
    await bot.send_message(run.chat_id, "\n".join(lines), message_thread_id=run.thread_id, reply_markup=again_kb(quiz["id"]))


@dp.callback_query(F.data.startswith("run:"))
async def start_run(call: CallbackQuery, bot: Bot):
    quiz = db.get_quiz(int(call.data.split(":")[1]))
    if not quiz:
        await call.answer("Bu test topilmadi (o'chirilgan bo'lishi mumkin).", show_alert=True)
        return
    msg = call.message
    thread_id = msg.message_thread_id if msg.is_topic_message else None
    key = (msg.chat.id, thread_id)
    if key in runs:
        await call.answer("Bu yerda test allaqachon ketyapti. Avval uni «⏹ To'xtatish» bilan to'xtating.", show_alert=True)
        return
    questions = prepare_questions(quiz)
    if not questions:
        await call.answer("Bu testda savol yo'q.", show_alert=True)
        return
    await call.answer("Boshlandi!")
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    run = Run(
        chat_id=msg.chat.id, thread_id=thread_id, is_group=msg.chat.type != "private",
        quiz=dict(quiz), questions=questions, starter_id=call.from_user.id,
    )
    runs[key] = run
    run.task = asyncio.create_task(run_quiz(bot, run))


@dp.poll_answer()
async def on_answer(answer: PollAnswer):
    run = poll_runs.get(answer.poll_id)
    if not run or not answer.user or not answer.option_ids:
        return
    uid = answer.user.id
    if uid in run.answered:
        return
    run.answered.add(uid)
    p = run.players.setdefault(uid, Player(answer.user.full_name))
    p.seconds += time.monotonic() - run.sent_at
    if answer.option_ids[0] == run.correct_option:
        p.correct += 1
    else:
        p.wrong += 1
    if not run.is_group and uid == run.starter_id:
        run.answer_event.set()


async def try_stop(bot: Bot, chat_id: int, chat_type: str, thread_id, user_id: int) -> str:
    run = runs.get((chat_id, thread_id))
    if not run:
        return "Hozir ketayotgan test yo'q."
    allowed = user_id in (run.starter_id, run.quiz["owner_id"])
    if not allowed and run.is_group:
        member = await bot.get_chat_member(chat_id, user_id)
        allowed = member.status in ("creator", "administrator")
    if not allowed:
        return "Testni faqat uni boshlagan odam yoki guruh admini to'xtata oladi."
    run.stopped = True
    run.answer_event.set()
    return ""


@dp.message(Command("stop"))
async def cmd_stop(message: Message, bot: Bot):
    thread_id = message.message_thread_id if message.is_topic_message else None
    err = await try_stop(bot, message.chat.id, message.chat.type, thread_id, message.from_user.id)
    await message.answer(err or "⏹ Test to'xtatildi. Natijalar chiqarilmoqda...")


@dp.callback_query(F.data == "stop")
async def stop_button(call: CallbackQuery, bot: Bot):
    msg = call.message
    thread_id = msg.message_thread_id if msg.is_topic_message else None
    err = await try_stop(bot, msg.chat.id, msg.chat.type, thread_id, call.from_user.id)
    if err:
        await call.answer(err, show_alert=True)
        return
    await call.answer("To'xtatildi")
    try:
        await msg.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await msg.answer("⏹ Test to'xtatildi. Natijalar chiqarilmoqda...")


# ---------- Ishga tushirish ----------

# Hamma narsa tugmalar orqali: «Menu» da faqat /start qoladi
PRIVATE_COMMANDS = [BotCommand(command="start", description="Bosh menyu")]


async def main():
    if not BOT_TOKEN:
        raise SystemExit(".env faylida BOT_TOKEN yozilmagan")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.set_my_commands(PRIVATE_COMMANDS, scope=BotCommandScopeAllPrivateChats())
        await bot.delete_my_commands(scope=BotCommandScopeAllGroupChats())
    except Exception:
        log.exception("Menyu buyruqlarini o'rnatib bo'lmadi")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
