import asyncio
import logging
import os
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

if not BOT_TOKEN:
    raise SystemExit("Missing BOT_TOKEN")
if not ANTHROPIC_API_KEY:
    raise SystemExit("Missing ANTHROPIC_API_KEY")
MODEL = "claude-sonnet-4-6"  # Sonnet: отличное качество + разумная цена. Заменить на claude-opus-4-7 для максимума.
MAX_HISTORY = 30  # максимум сообщений в истории на пользователя
MAX_TOKENS = 4096

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
executor = ThreadPoolExecutor(max_workers=8)

# История диалогов: {user_id: [{"role": ..., "content": ...}]}
history: dict[int, list] = defaultdict(list)


def trim_history(user_id: int) -> None:
    """Оставляем только последние MAX_HISTORY сообщений."""
    if len(history[user_id]) > MAX_HISTORY:
        history[user_id] = history[user_id][-MAX_HISTORY:]


def ask_claude_sync(user_id: int, user_text: str) -> str:
    """Синхронный запрос в Claude с историей и инструментами (для ThreadPoolExecutor)."""
    history[user_id].append({"role": "user", "content": user_text})
    trim_history(user_id)

    messages = list(history[user_id])
    response = None

    # Агентный цикл: Claude может вызывать инструменты несколько раз
    for _ in range(10):  # максимум 10 итераций tool use
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        # Если Claude закончил — вернуть ответ
        if response.stop_reason == "end_turn":
            history[user_id].append({"role": "assistant", "content": response.content})
            return _extract_text(response)

        # Claude хочет вызвать инструменты
        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    logger.info(f"Tool call: {block.name} | input: {block.input}")
                    result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})
        else:
            break

    # Вернуть последний доступный ответ
    if response is not None:
        history[user_id].append({"role": "assistant", "content": response.content})
        return _extract_text(response)
    return "Не удалось получить ответ."


def _extract_text(response) -> str:
    """Извлечь текстовый контент из ответа Claude."""
    parts = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts) if parts else "Не получил ответ от Claude."


def split_message(text: str, max_len: int = 4000) -> list[str]:
    """Разбить длинный текст на части для Telegram."""
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


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(
        "👋 Привет! Я Ментор — твой личный инструктор по Claude Code и GitHub.\n\n"
        "Я помогу тебе:\n"
        "• 🔧 Разобраться с Claude Code CLI\n"
        "• ⚡ Создавать скиллы, хуки, MCP-серверы\n"
        "• 🐙 Работать с GitHub и репозиториями\n"
        "• 🔍 Находить готовые решения и примеры\n\n"
        "Просто задай любой вопрос!\n\n"
        "Команды:\n"
        "/clear — сбросить историю диалога\n"
        "/help — что я умею"
    )


@dp.message(Command("clear"))
async def cmd_clear(message: Message) -> None:
    history[message.from_user.id].clear()
    await message.answer("✅ История диалога сброшена. Начнём с чистого листа!")


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "🎓 <b>Что я умею:</b>\n\n"
        "<b>Claude Code:</b>\n"
        "• Все slash-команды и горячие клавиши\n"
        "• Как создать скилл (/skill)\n"
        "• Хуки — автоматизация действий\n"
        "• MCP-серверы — подключение внешних сервисов\n"
        "• CLAUDE.md — память проекта\n"
        "• settings.json — настройки\n"
        "• Субагенты и параллельная работа\n\n"
        "<b>GitHub:</b>\n"
        "• Основы git (commit, branch, merge)\n"
        "• Pull Requests и ревью\n"
        "• GitHub Actions (CI/CD)\n"
        "• gh CLI — управление из терминала\n"
        "• Поиск готовых решений\n\n"
        "<b>Особенности:</b>\n"
        "• Ищу актуальную информацию в интернете\n"
        "• Нахожу примеры кода на GitHub\n"
        "• Помню историю нашего разговора\n\n"
        "/clear — сбросить историю",
        parse_mode="HTML"
    )


@dp.message(F.text)
async def handle_message(message: Message) -> None:
    user_id = message.from_user.id
    user_text = message.text.strip()

    if not user_text:
        return

    # Показать статус "печатает"
    await bot.send_chat_action(message.chat.id, "typing")

    try:
        loop = asyncio.get_event_loop()
        response_text = await loop.run_in_executor(
            executor, ask_claude_sync, user_id, user_text
        )
    except Exception as e:
        logger.error(f"Error for user {user_id}: {e}")
        await message.answer(f"❌ Произошла ошибка: {e}")
        return

    # Отправить ответ (с разбивкой если длинный)
    for part in split_message(response_text):
        try:
            await message.answer(part, parse_mode="Markdown")
        except Exception:
            # Если Markdown не работает — отправить как обычный текст
            await message.answer(part)


async def main() -> None:
    logger.info("Starting Claude Code Mentor bot...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
