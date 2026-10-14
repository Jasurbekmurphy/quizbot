import asyncio
import logging
import random
import time
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
    Message,
    PollAnswer,
    ReplyKeyboardMarkup,
)

import db
from config import ADMIN_IDS, BOT_TOKEN
from importer import make_docx_template, make_xlsx_template, parse_file

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("quiz")

dp = Dispatcher()
private = F.chat.type == "private"

TIME_CHOICES = [10, 15, 20, 30, 45, 60, 120, 180, 300]
COUNTDOWN = 3          # boshlashdan oldin kutish (soniya)
PRIVATE_IDLE_STOP = 2  # shaxsiy chatda ketma-ket shuncha savolga javob berilmasa, test to'xtaydi
GROUP_IDLE_STOP = 3    # guruhda ketma-ket shuncha savolga hech kim javob bermasa, test to'xtaydi

BTN_NEW = "➕ Yangi test"
BTN_MY = "📚 Testlarim"
BTN_TEMPLATES = "📄 Shablonlar"
BTN_HELP = "❓ Yordam"
BTN_CANCEL = "❌ Bekor qilish"
MAIN_KB = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=BTN_NEW), KeyboardButton(text=BTN_MY)],
        [KeyboardButton(text=BTN_TEMPLATES), KeyboardButton(text=BTN_HELP)],
    ],
    resize_keyboard=True,
)
CANCEL_KB = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=BTN_CANCEL)]], resize_keyboard=True)

HELP = """<b>Test boti</b>

Savollarni bittalab kiritish shart emas: hamma savollarni bitta Excel yoki Word faylda yuborasiz.

<b>Test yaratish</b>
1. Pastdagi «➕ Yangi test» tugmasini bosing va test nomini yozing.
2. Savollar faylini yuboring (Excel, Word yoki .txt). Namunasi: «📄 Shablonlar» tugmasida.
3. Har savolga vaqtni va aralashtirishni tanlang. Tayyor!

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
    return f"{seconds} soniya" if seconds < 60 else f"{seconds // 60} daqiqa"


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
        f"🖊 {n} ta savol\n⏱ Har savolga {fmt_limit(quiz['time_limit'])}\n🔀 {shuffle_text(quiz)}"
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
    timing = State()


@dp.message(private, Command("yangi"))
@dp.message(private, F.text == BTN_NEW)
async def new_quiz(message: Message, state: FSMContext):
    if not can_create(message.from_user.id):
        await message.answer("Test yaratish faqat adminlarga ruxsat etilgan.")
        return
    await state.clear()
    await state.set_state(NewQuiz.title)
    await message.answer("📝 Test nomini yozing, masalan: <code>Matematika, 9-sinf, 1-chorak</code>", reply_markup=CANCEL_KB)


@dp.message(private, NewQuiz.title, F.text, ~F.text.startswith("/"))
async def new_title(message: Message, state: FSMContext):
    await state.update_data(title=" ".join(message.text.split())[:120])
    await state.set_state(NewQuiz.file)
    await message.answer(
        "📎 Endi savollar faylini yuboring: Excel (.xlsx), Word (.docx) yoki .txt.\n"
        "Namuna fayllar: «📄 Shablonlar» tugmasida."
    )


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
    await state.set_state(NewQuiz.timing)
    await state.update_data(title=title, questions=questions)
    lines = [f"✅ <b>{len(questions)} ta savol</b> o'qildi."]
    if errors:
        lines.append(f"\n⚠️ {len(errors)} ta savol qo'shilmadi:")
        lines += [escape(e) for e in errors[:15]]
        if len(errors) > 15:
            lines.append(f"... va yana {len(errors) - 15} ta")
    q = questions[0]
    lines.append("\n<b>1-savol:</b> " + escape(q["text"][:200]))
    lines += [("✅ " if i == q["correct"] else "▫️ ") + escape(o[:80]) for i, o in enumerate(q["options"])]
    lines.append("\n⏱ <b>Har bir savolga qancha vaqt beriladi?</b>")
    buttons = [InlineKeyboardButton(text=fmt_limit(t), callback_data=f"new:t:{t}") for t in TIME_CHOICES]
    kb = InlineKeyboardMarkup(inline_keyboard=[buttons[i:i + 3] for i in range(0, len(buttons), 3)])
    await message.answer("\n".join(lines), reply_markup=kb)


@dp.callback_query(NewQuiz.timing, F.data.startswith("new:t:"))
async def new_time(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.update_data(time_limit=int(call.data.split(":")[2]))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔀 Savollar va javoblarni", callback_data="new:s:11")],
        [InlineKeyboardButton(text="Faqat javoblarni", callback_data="new:s:01")],
        [InlineKeyboardButton(text="Aralashtirmaslik", callback_data="new:s:00")],
    ])
    await call.message.edit_reply_markup(reply_markup=None)
    await call.message.answer("🔀 <b>Har safar aralashtirilsinmi?</b>", reply_markup=kb)


@dp.callback_query(NewQuiz.timing, F.data.startswith("new:s:"))
async def new_shuffle(call: CallbackQuery, state: FSMContext, bot: Bot):
    await call.answer()
    d = await state.get_data()
    await state.clear()
    flags = call.data.split(":")[2]
    quiz_id = db.create_quiz(call.from_user.id, d["title"], d["questions"], d.get("time_limit", 30))
    db.update_quiz(quiz_id, shuffle_q=int(flags[0]), shuffle_a=int(flags[1]))
    await call.message.edit_text("✅ Test saqlandi!")
    await call.message.answer("Tayyor.", reply_markup=MAIN_KB)
    text, kb = await quiz_card(bot, quiz_id)
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
    times = [
        InlineKeyboardButton(text=("✅ " if t == quiz["time_limit"] else "") + fmt_limit(t), callback_data=f"set:{qid}:t:{t}")
        for t in TIME_CHOICES
    ]
    rows = [times[i:i + 3] for i in range(0, len(times), 3)]
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
        if parts[2] == "t":
            db.update_quiz(quiz["id"], time_limit=int(parts[3]))
        elif parts[2] == "q":
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
        f"⚙️ <b>{escape(quiz['title'])}</b> — sozlamalar\n\n⏱ Har savolga vaqt:", reply_markup=settings_kb(quiz)
    )


@dp.message(private, Rename.title, F.text, ~F.text.startswith("/"))
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
    limit = run.quiz["time_limit"]
    explanation = q["explanation"][:200] or None
    if len(head) + len(q["text"]) <= 300 and all(len(o) <= 100 for o in q["options"]):
        question, options = head + q["text"], q["options"]
    else:
        # Telegram cheklovi: savol 300, javob 100 belgidan oshmasin — uzun matn alohida xabarda yuboriladi
        letters = "ABCDEFGHIJ"
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
            f"🖊 {n} ta savol, har biriga ⏱ {fmt_limit(run.quiz['time_limit'])}.\n"
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
            limit = run.quiz["time_limit"]
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
