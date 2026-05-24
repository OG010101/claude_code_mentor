import asyncio
import base64
import json
import logging
import os
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import anthropic
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from system_prompt import SYSTEM_PROMPT
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


def trim_history(user_id: int) -> None:
    if len(history[user_id]) > MAX_HISTORY:
        history[user_id] = history[user_id][-MAX_HISTORY:]


def _run_claude_loop(user_id: int, user_content) -> str:
    """Агентный цикл Claude — принимает текст или список блоков контента (для изображений)."""
    history[user_id].append({"role": "user", "content": user_content})
    trim_history(user_id)

    messages = list(history[user_id])
    response = None

    for _ in range(10):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
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
    return _run_claude_loop(user_id, text)


def ask_claude_with_image_sync(user_id: int, image_bytes: bytes, caption: str) -> str:
    text = caption.strip() if caption else "Посмотри на этот скриншот. Что здесь происходит? Если видишь ошибку — объясни простыми словами что это и как исправить."
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
    return _run_claude_loop(user_id, content)


def transcribe_voice_sync(audio_bytes: bytes) -> str:
    """Транскрибирует голосовое сообщение через Groq Whisper API."""
    import http.client, ssl

    boundary = "Boundary7MA4YWxkTrZu0gW"

    def field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode()

    def file_field(name: str, filename: str, data: bytes, ctype: str) -> bytes:
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n"
        ).encode()
        return header + data + b"\r\n"

    body = (
        field("model", "whisper-large-v3-turbo")
        + field("language", "ru")
        + file_field("file", "voice.ogg", audio_bytes, "audio/ogg")
        + f"--{boundary}--\r\n".encode()
    )

    ctx = ssl.create_default_context()
    conn = http.client.HTTPSConnection("api.groq.com", context=ctx, timeout=30)
    conn.request(
        "POST",
        "/openai/v1/audio/transcriptions",
        body=body,
        headers={
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    resp = conn.getresponse()
    resp_body = resp.read()
    if resp.status != 200:
        logger.error(f"Groq error {resp.status}: {resp_body.decode()}")
        return ""
    return json.loads(resp_body).get("text", "")


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


async def _send_response(message: Message, text: str) -> None:
    for part in split_message(text):
        try:
            await message.answer(part, parse_mode="Markdown")
        except Exception:
            await message.answer(part)


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Я Ментор — твой личный инструктор по Claude Code и GitHub.\n\n"
        "Я помогу тебе:\n"
        "• 🔧 Разобраться с Claude Code CLI\n"
        "• ⚡ Создавать скиллы, хуки, MCP-серверы\n"
        "• 🐙 Работать с GitHub и репозиториями\n"
        "• 🔍 Находить готовые решения и примеры\n\n"
        "Можешь писать текстом, голосом или присылать скриншоты!\n\n"
        "/clear — сбросить историю диалога\n"
        "/help — что я умею"
    )


@dp.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    history[message.from_user.id].clear()
    await message.answer("✅ История сброшена. Начнём с чистого листа!")


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🎓 <b>Что я умею:</b>\n\n"
        "💬 <b>Текст</b> — задавай любые вопросы\n"
        "🎤 <b>Голос</b> — говори, я пойму\n"
        "📸 <b>Фото/скриншот</b> — покажи ошибку, объясню\n\n"
        "<b>Темы:</b>\n"
        "• Claude Code — все команды, скиллы, хуки, MCP\n"
        "• GitHub — git, PR, Actions, gh CLI\n"
        "• Вайб-кодинг — как работать с AI как профи\n\n"
        "/clear — сбросить историю",
        parse_mode="HTML"
    )


@dp.message(F.text)
async def handle_text(message: Message) -> None:
    user_id = message.from_user.id
    text = message.text.strip()
    if not text:
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
