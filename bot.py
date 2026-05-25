import asyncio
import base64
import logging
import os
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import anthropic
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import db
from knowledge_updater import run_update_sync
from system_prompt import get_system_prompt
from tools import TOOLS, execute_tool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

if not BOT_TOKEN:
    raise SystemExit("Missing BOT_TOKEN")
if not ANTHROPIC_API_KEY:
    raise SystemExit("Missing ANTHROPIC_API_KEY")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 4096

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
executor = ThreadPoolExecutor(max_workers=8)

setup_state: dict[int, str] = {}
pending_parts: dict[int, list[str]] = defaultdict(list)

TOPIC_RE = re.compile(r"#TOPIC:(\S+?)#")


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_kb(*buttons: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t, d in buttons]
    )


def split_smart(text: str, max_len: int = 4000) -> list[str]:
    """Split at newlines without cutting inside a code block."""
    if len(text) <= max_len:
        return [text]
    parts = []
    while len(text) > max_len:
        chunk = text[:max_len]
        if chunk.count("```") % 2 == 1:
            last_fence = chunk.rfind("```")
            split_at = chunk.rfind("\n", 0, last_fence)
            if split_at <= 0:
                split_at = last_fence
        else:
            split_at = chunk.rfind("\n")
            if split_at <= 0:
                split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        parts.append(text)
    return parts


def needs_yn(text: str) -> bool:
    lower = text.lower()
    return any(t in lower for t in [
        "хочешь попробу", "попробуем?", "начнём?", "создадим?",
        "подключим?", "настроим?", "попробуем создать", "попробуем настроить",
    ])


def action_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔁 Объясни иначе", callback_data="action_again"),
            InlineKeyboardButton(text="✅ Понял, дальше", callback_data="action_next"),
        ]
    ])


def last_part_kb(text: str) -> InlineKeyboardMarkup | None:
    if needs_yn(text):
        return make_kb(("Да, попробуем 👍", "yn_yes"), ("Нет, давай дальше ➡️", "yn_no"))
    return action_kb()


async def _send_paged(target: Message, text: str) -> None:
    user_id = target.from_user.id
    parts = split_smart(text)

    if len(parts) == 1:
        kb = last_part_kb(parts[0])
        try:
            await target.answer(parts[0], parse_mode="Markdown", reply_markup=kb)
        except Exception:
            await target.answer(parts[0], reply_markup=kb)
        return

    pending_parts[user_id] = parts[1:]
    try:
        await target.answer(parts[0], parse_mode="Markdown", reply_markup=make_kb(("Дальше ➡️", "next_part")))
    except Exception:
        await target.answer(parts[0], reply_markup=make_kb(("Дальше ➡️", "next_part")))


# ── Claude loop ───────────────────────────────────────────────────────────────

def _run_claude(user_id: int, user_content, on_tool_use=None) -> str:
    db.append_message(user_id, "user", user_content)
    messages = db.load_history(user_id)
    profile = db.load_profile(user_id)
    system_prompt = get_system_prompt(profile)
    response = None
    tool_notified = False

    for _ in range(10):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            db.append_message(user_id, "assistant", response.content)
            text = _extract(response)
            topic_match = TOPIC_RE.search(text)
            if topic_match:
                db.add_topic(user_id, topic_match.group(1))
                text = TOPIC_RE.sub("", text).strip()
            return text

        if response.stop_reason == "tool_use":
            if not tool_notified and on_tool_use:
                on_tool_use()
                tool_notified = True
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"Tool: {block.name} | {block.input}")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": execute_tool(block.name, block.input),
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    if response:
        db.append_message(user_id, "assistant", response.content)
        return _extract(response)
    return "Не удалось получить ответ."


def _extract(response) -> str:
    parts = [b.text for b in response.content if hasattr(b, "text")]
    return "\n".join(parts) if parts else "Не получил ответ от Claude."


def ask_sync(user_id: int, text: str, on_tool_use=None) -> str:
    return _run_claude(user_id, text, on_tool_use)


def ask_image_sync(user_id: int, image_bytes: bytes, caption: str, on_tool_use=None) -> str:
    text = caption.strip() if caption else "Посмотри на этот скриншот. Что здесь происходит? Если видишь ошибку — объясни что это и как исправить."
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                      "data": base64.b64encode(image_bytes).decode()}},
        {"type": "text", "text": text},
    ]
    return _run_claude(user_id, content, on_tool_use)


async def _run_with_status(target: Message, fn) -> str:
    """Запускает fn(on_tool_use) в executor, показывает 🔍 при поиске."""
    loop = asyncio.get_event_loop()
    status_ref: list = [None]

    def on_tool_use():
        future = asyncio.run_coroutine_threadsafe(
            target.answer("🔍 Ищу актуальную информацию..."),
            loop,
        )
        try:
            status_ref[0] = future.result(timeout=5)
        except Exception:
            pass

    resp = await loop.run_in_executor(executor, fn, on_tool_use)

    if status_ref[0]:
        try:
            await status_ref[0].delete()
        except Exception:
            pass

    return resp


def transcribe_sync(audio_bytes: bytes) -> str:
    import io
    from groq import Groq
    client_groq = Groq(api_key=GROQ_API_KEY)
    f = io.BytesIO(audio_bytes)
    f.name = "voice.ogg"
    return client_groq.audio.transcriptions.create(
        file=f, model="whisper-large-v3-turbo", language="ru"
    ).text


# ── Handlers: setup ───────────────────────────────────────────────────────────

@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    uid = message.from_user.id
    db.clear_history(uid)
    db.clear_profile(uid)
    setup_state.pop(uid, None)
    await message.answer(
        "👋 Привет! Я Ментор — твой личный инструктор по Claude Code.\n\n"
        "Пара вопросов чтобы объяснять именно так как нужно тебе.\n\n"
        "*Ты уже знаком с Claude Code?*",
        parse_mode="Markdown",
        reply_markup=make_kb(
            ("🌱 Полный новичок", "setup_level_beginner"),
            ("⚡ Кое-что пробовал", "setup_level_experienced"),
        ),
    )


@dp.callback_query(F.data.startswith("setup_level_"))
async def cb_level(call: CallbackQuery) -> None:
    level = "beginner" if call.data == "setup_level_beginner" else "experienced"
    db.save_profile(call.from_user.id, level=level)
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()
    await call.message.answer(
        "Понял! *Какая у тебя операционная система?*",
        parse_mode="Markdown",
        reply_markup=make_kb(
            ("🍎 Mac", "setup_os_mac"),
            ("🪟 Windows", "setup_os_windows"),
            ("🐧 Linux", "setup_os_linux"),
        ),
    )


@dp.callback_query(F.data.startswith("setup_os_"))
async def cb_os(call: CallbackQuery) -> None:
    os_map = {"setup_os_mac": "Mac", "setup_os_windows": "Windows", "setup_os_linux": "Linux"}
    db.save_profile(call.from_user.id, os=os_map[call.data])
    setup_state[call.from_user.id] = "awaiting_name"
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()
    await call.message.answer("Как тебя зовут?")


# ── Handlers: buttons ─────────────────────────────────────────────────────────

@dp.callback_query(F.data.in_({"yn_yes", "yn_no"}))
async def cb_yn(call: CallbackQuery) -> None:
    uid = call.from_user.id
    text = "Да, хочу попробовать!" if call.data == "yn_yes" else "Нет, давай дальше"
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()
    await bot.send_chat_action(call.message.chat.id, "typing")
    try:
        resp = await _run_with_status(call.message, lambda cb: ask_sync(uid, text, cb))
        await _send_paged(call.message, resp)
    except Exception as e:
        logger.error(f"YN error {uid}: {e}")
        await call.message.answer(f"❌ Ошибка: {e}")


@dp.callback_query(F.data.in_({"action_again", "action_next"}))
async def cb_action(call: CallbackQuery) -> None:
    uid = call.from_user.id
    text = (
        "Объясни это иначе — другими словами или через другой пример из жизни"
        if call.data == "action_again"
        else "Понял. Что изучаем дальше?"
    )
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()
    await bot.send_chat_action(call.message.chat.id, "typing")
    try:
        resp = await _run_with_status(call.message, lambda cb: ask_sync(uid, text, cb))
        await _send_paged(call.message, resp)
    except Exception as e:
        logger.error(f"Action error {uid}: {e}")
        await call.message.answer(f"❌ Ошибка: {e}")


@dp.callback_query(F.data == "next_part")
async def cb_next(call: CallbackQuery) -> None:
    uid = call.from_user.id
    parts = pending_parts.get(uid, [])
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()

    if not parts:
        return

    part = parts.pop(0)
    is_last = len(parts) == 0

    if not is_last:
        kb = make_kb(("Дальше ➡️", "next_part"))
    else:
        kb = last_part_kb(part)

    try:
        await call.message.answer(part, parse_mode="Markdown", reply_markup=kb)
    except Exception:
        await call.message.answer(part, reply_markup=kb)


# ── Handlers: commands ────────────────────────────────────────────────────────

@dp.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    db.clear_history(message.from_user.id)
    await message.answer("✅ История сброшена. Начнём с чистого листа!")


@dp.message(Command("progress"))
async def cmd_progress(message: Message) -> None:
    uid = message.from_user.id
    profile = db.load_profile(uid)
    if not profile.get("setup_done"):
        await message.answer("Сначала пройди настройку — напиши /start")
        return

    done = set(profile.get("topics", []))
    lines = ["*Твой прогресс по курсу:*\n"]
    next_found = False
    for key, label in db.LEARNING_PATH:
        if key in done:
            lines.append(f"✅ {label}")
        elif not next_found:
            lines.append(f"▶️ {label}  ← сейчас здесь")
            next_found = True
        else:
            lines.append(f"⬜ {label}")

    completed = len(done)
    total = len(db.LEARNING_PATH)
    lines.append(f"\n_{completed}/{total} тем пройдено_")

    try:
        await message.answer("\n".join(lines), parse_mode="Markdown")
    except Exception:
        await message.answer("\n".join(lines))


@dp.message(Command("update"))
async def cmd_update(message: Message) -> None:
    await message.answer("🔍 Ищу свежие инструменты и лайфхаки...")
    try:
        loop = asyncio.get_event_loop()
        count = await loop.run_in_executor(executor, run_update_sync, client)
        if count == 0:
            await message.answer("Ничего нового не нашёл. Попробуй позже.")
            return
        fresh = db.get_recent_discoveries(limit=count)
        items = "\n".join(fresh)
        await message.answer(
            f"✅ *Нашёл {count} новых советов:*\n\n{items}\n\n"
            f"_Всего в базе: {db.discoveries_count()}_",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.error(f"Manual update error: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🎓 *Что я умею:*\n\n"
        "💬 *Текст* — задавай любые вопросы\n"
        "🎤 *Голос* — говори, я пойму\n"
        "📸 *Фото/скриншот* — покажи ошибку, объясню\n\n"
        "*Команды:*\n"
        "/progress — твой прогресс по курсу\n"
        "/update — найти свежие инструменты и лайфхаки\n"
        "/clear — сбросить историю диалога\n"
        "/start — начать заново и обновить профиль",
        parse_mode="Markdown",
    )


# ── Handlers: messages ────────────────────────────────────────────────────────

@dp.message(F.text)
async def handle_text(message: Message) -> None:
    uid = message.from_user.id
    text = message.text.strip()
    if not text:
        return

    if setup_state.get(uid) == "awaiting_name":
        setup_state[uid] = "awaiting_goal"
        db.save_profile(uid, name=text)
        await message.answer(
            f"Приятно познакомиться, *{text}*! 👋\n\n"
            "И последнее — чего хочешь достичь с Claude Code? Пару слов.\n\n"
            "_Например: «автоматизировать рутину» или «учусь программировать с нуля»_",
            parse_mode="Markdown",
        )
        return

    if setup_state.get(uid) == "awaiting_goal":
        setup_state.pop(uid)
        db.save_profile(uid, goal=text, setup_done=True)
        profile = db.load_profile(uid)
        level_label = "новичок" if profile.get("level") == "beginner" else "есть опыт"
        name = profile.get("name", "")
        greeting = f"Всё готово, {name}! 🚀" if name else "Всё готово! 🚀"
        try:
            await message.answer(
                f"*Профиль сохранён* ✅\n\n"
                f"Уровень: {level_label} | ОС: {profile.get('os')} | Цель: {text}\n\n"
                f"{greeting}",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        await bot.send_chat_action(message.chat.id, "typing")
        try:
            resp = await _run_with_status(
                message, lambda cb: ask_sync(uid, "Мой профиль заполнен. С чего начнём?", cb)
            )
            await _send_paged(message, resp)
        except Exception as e:
            logger.error(f"Setup start error {uid}: {e}")
        return

    await bot.send_chat_action(message.chat.id, "typing")
    try:
        resp = await _run_with_status(message, lambda cb: ask_sync(uid, text, cb))
        await _send_paged(message, resp)
    except Exception as e:
        logger.error(f"Text error {uid}: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.photo)
async def handle_photo(message: Message) -> None:
    uid = message.from_user.id
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        file = await bot.get_file(message.photo[-1].file_id)
        bio = await bot.download_file(file.file_path)
        image_bytes = bio.read()
        caption = message.caption or ""
        resp = await _run_with_status(
            message, lambda cb: ask_image_sync(uid, image_bytes, caption, cb)
        )
        await _send_paged(message, resp)
    except Exception as e:
        logger.error(f"Photo error {uid}: {e}")
        await message.answer("❌ Не смог обработать фото. Попробуй ещё раз.")


@dp.message(F.voice)
async def handle_voice(message: Message) -> None:
    uid = message.from_user.id
    if not GROQ_API_KEY:
        await message.answer("🎤 Голосовые сообщения не настроены. Добавь GROQ_API_KEY в Railway.")
        return
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        file = await bot.get_file(message.voice.file_id)
        bio = await bot.download_file(file.file_path)
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(executor, transcribe_sync, bio.read())
        if not text:
            await message.answer("Не смог разобрать речь. Попробуй ещё раз.")
            return
        await message.answer(f"🎤 _{text}_", parse_mode="Markdown")
        resp = await _run_with_status(message, lambda cb: ask_sync(uid, text, cb))
        await _send_paged(message, resp)
    except Exception as e:
        logger.error(f"Voice error {uid}: {e}")
        await message.answer("❌ Не смог обработать голосовое. Попробуй ещё раз.")


async def _knowledge_update_loop() -> None:
    """Auto-updates knowledge base every 24 hours."""
    await asyncio.sleep(30)  # дать боту запуститься
    while True:
        try:
            loop = asyncio.get_event_loop()
            count = await loop.run_in_executor(executor, run_update_sync, client)
            logger.info(f"Auto knowledge update: {count} new tips")
        except Exception as e:
            logger.error(f"Auto update error: {e}")
        await asyncio.sleep(24 * 60 * 60)


async def main() -> None:
    db.init_db()
    asyncio.create_task(_knowledge_update_loop())
    logger.info("Starting Claude Code Mentor bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
