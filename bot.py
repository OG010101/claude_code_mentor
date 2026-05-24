import asyncio
import base64
import logging
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import anthropic
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

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
MAX_HISTORY = 20
MAX_TOKENS = 4096

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
executor = ThreadPoolExecutor(max_workers=8)

history: dict[int, list] = defaultdict(list)
user_profiles: dict[int, dict] = defaultdict(dict)
setup_state: dict[int, str] = {}  # user_id -> "awaiting_goal"


def get_profile(user_id: int) -> dict:
    return dict(user_profiles[user_id])


def update_profile(user_id: int, **kwargs) -> None:
    user_profiles[user_id].update(kwargs)


def is_setup_done(user_id: int) -> bool:
    return user_profiles[user_id].get("setup_done", False)


def make_kb(*buttons: tuple[str, str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d)] for t, d in buttons]
    )


def trim_history(user_id: int) -> None:
    if len(history[user_id]) > MAX_HISTORY:
        history[user_id] = history[user_id][-MAX_HISTORY:]


def _run_claude_loop(user_id: int, user_content, system_prompt: str) -> str:
    history[user_id].append({"role": "user", "content": user_content})
    trim_history(user_id)

    messages = list(history[user_id])
    response = None

    for _ in range(10):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            history[user_id].append({"role": "assistant", "content": response.content})
            return _extract_text(response)

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"Tool: {block.name} | {block.input}")
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    if response is not None:
        history[user_id].append({"role": "assistant", "content": response.content})
        return _extract_text(response)
    return "Не удалось получить ответ."


def ask_claude_sync(user_id: int, text: str) -> str:
    return _run_claude_loop(user_id, text, get_system_prompt(get_profile(user_id)))


def ask_claude_with_image_sync(user_id: int, image_bytes: bytes, caption: str) -> str:
    text = caption.strip() if caption else "Посмотри на этот скриншот. Что здесь происходит? Если видишь ошибку — объясни что это и как исправить."
    content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(image_bytes).decode(),
            },
        },
        {"type": "text", "text": text},
    ]
    return _run_claude_loop(user_id, content, get_system_prompt(get_profile(user_id)))


def transcribe_voice_sync(audio_bytes: bytes) -> str:
    import io
    from groq import Groq
    groq_client = Groq(api_key=GROQ_API_KEY)
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "voice.ogg"
    transcription = groq_client.audio.transcriptions.create(
        file=audio_file,
        model="whisper-large-v3-turbo",
        language="ru",
    )
    return transcription.text


def _extract_text(response) -> str:
    parts = [block.text for block in response.content if hasattr(block, "text")]
    return "\n".join(parts) if parts else "Не получил ответ от Claude."


def split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        parts.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return parts


def needs_yn_buttons(text: str) -> bool:
    lower = text.lower()
    triggers = ["хочешь попробу", "попробуем?", "начнём?", "создадим?", "подключим?", "настроим?", "попробуем создать", "хочешь начнём", "попробуем настроить"]
    return any(t in lower for t in triggers)


async def _send_response(message: Message, text: str) -> None:
    parts = split_message(text)
    for i, part in enumerate(parts):
        kb = None
        if i == len(parts) - 1 and needs_yn_buttons(part):
            kb = make_kb(
                ("Да, попробуем 👍", "yn_yes"),
                ("Нет, давай дальше ➡️", "yn_no"),
            )
        try:
            await message.answer(part, parse_mode="Markdown", reply_markup=kb)
        except Exception:
            await message.answer(part, reply_markup=kb)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user_id = message.from_user.id
    history[user_id].clear()
    user_profiles[user_id] = {}
    setup_state.pop(user_id, None)
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
async def cb_setup_level(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    level = "beginner" if call.data == "setup_level_beginner" else "experienced"
    update_profile(user_id, level=level)
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
async def cb_setup_os(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    os_map = {"setup_os_mac": "Mac", "setup_os_windows": "Windows", "setup_os_linux": "Linux"}
    update_profile(user_id, os=os_map[call.data])
    setup_state[user_id] = "awaiting_goal"
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()
    await call.message.answer(
        "Почти готово! *Последний вопрос:*\n\n"
        "Чего хочешь достичь с Claude Code? Напиши в двух словах.\n\n"
        "_Например: «хочу автоматизировать рутину» или «учусь программировать с нуля»_",
        parse_mode="Markdown",
    )


@dp.callback_query(F.data.in_({"yn_yes", "yn_no"}))
async def cb_yn(call: CallbackQuery) -> None:
    user_id = call.from_user.id
    text = "Да, хочу попробовать!" if call.data == "yn_yes" else "Нет, давай дальше"
    await call.message.edit_reply_markup(reply_markup=None)
    await call.answer()
    await bot.send_chat_action(call.message.chat.id, "typing")
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(executor, ask_claude_sync, user_id, text)
        await _send_response(call.message, response)
    except Exception as e:
        logger.error(f"YN callback error {user_id}: {e}")
        await call.message.answer(f"❌ Ошибка: {e}")


@dp.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    history[message.from_user.id].clear()
    await message.answer("✅ История сброшена. Начнём с чистого листа!")


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🎓 *Что я умею:*\n\n"
        "💬 *Текст* — задавай любые вопросы\n"
        "🎤 *Голос* — говори, я пойму\n"
        "📸 *Фото/скриншот* — покажи ошибку, объясню\n\n"
        "*Темы:*\n"
        "• Claude Code — скиллы, хуки, MCP, CLAUDE.md\n"
        "• GitHub — git, PR, gh CLI\n"
        "• Вайб-кодинг — как работать с AI как профи\n\n"
        "/clear — сбросить историю\n"
        "/start — начать заново и обновить профиль",
        parse_mode="Markdown",
    )


@dp.message(F.text)
async def handle_text(message: Message) -> None:
    user_id = message.from_user.id
    text = message.text.strip()
    if not text:
        return

    if setup_state.get(user_id) == "awaiting_goal":
        setup_state.pop(user_id)
        update_profile(user_id, goal=text, setup_done=True)
        profile = get_profile(user_id)
        level_label = "новичок" if profile.get("level") == "beginner" else "есть опыт"
        await bot.send_chat_action(message.chat.id, "typing")
        try:
            await message.answer(
                f"*Профиль сохранён* ✅\n\n"
                f"Уровень: {level_label} | ОС: {profile.get('os')} | Цель: {text}\n\n"
                f"Поехали! 🚀",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                executor, ask_claude_sync, user_id,
                "Мой профиль заполнен. С чего начнём?"
            )
            await _send_response(message, response)
        except Exception as e:
            logger.error(f"Setup start error {user_id}: {e}")
        return

    await bot.send_chat_action(message.chat.id, "typing")
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(executor, ask_claude_sync, user_id, text)
        await _send_response(message, response)
    except Exception as e:
        logger.error(f"Text error {user_id}: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.photo)
async def handle_photo(message: Message) -> None:
    user_id = message.from_user.id
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        file = await bot.get_file(message.photo[-1].file_id)
        bio = await bot.download_file(file.file_path)
        image_bytes = bio.read()
        caption = message.caption or ""
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            executor, ask_claude_with_image_sync, user_id, image_bytes, caption
        )
        await _send_response(message, response)
    except Exception as e:
        logger.error(f"Photo error {user_id}: {e}")
        await message.answer("❌ Не смог обработать фото. Попробуй ещё раз.")


@dp.message(F.voice)
async def handle_voice(message: Message) -> None:
    user_id = message.from_user.id
    if not GROQ_API_KEY:
        await message.answer("🎤 Голосовые сообщения не настроены. Добавь GROQ_API_KEY в Railway.")
        return
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        file = await bot.get_file(message.voice.file_id)
        bio = await bot.download_file(file.file_path)
        audio_bytes = bio.read()
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(executor, transcribe_voice_sync, audio_bytes)
        if not text:
            await message.answer("Не смог разобрать речь. Попробуй ещё раз.")
            return
        await message.answer(f"🎤 _{text}_", parse_mode="Markdown")
        response = await loop.run_in_executor(executor, ask_claude_sync, user_id, text)
        await _send_response(message, response)
    except Exception as e:
        logger.error(f"Voice error {user_id}: {e}")
        await message.answer("❌ Не смог обработать голосовое. Попробуй ещё раз.")


async def main() -> None:
    logger.info("Starting Claude Code Mentor bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
