import logging
import time

import anthropic

import db
from tools import search_web, search_github

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "new AI coding tools vibe coding 2026",
    "Claude Code tips tricks new features site:reddit.com OR site:github.com",
    "best tools Telegram bot Python development 2026",
    "AI developer productivity workflow tools new",
    "vibe coding новые инструменты ИИ разработка",
    "cursor windsurf bolt lovable AI coding tools comparison",
    "MCP servers useful Claude tools new",
    "Python AI agent tools libraries 2026",
]

_PROMPT = """\
Ты анализируешь поисковую выдачу о новых инструментах и приёмах для разработки с AI.

Твоя аудитория — вайб-кодеры: люди которые создают Telegram-ботов, агентов, веб-приложения с помощью Claude Code, не являясь профессиональными программистами.

Из результатов поиска извлеки ровно 2-3 совета или инструмента. Только то что реально полезно и конкретно.

Строгий формат каждого пункта (одна строка):
• НазваниеИлиТема: одно предложение — что это и зачем нужно вайб-кодеру

Примеры хорошего формата:
• Warp Terminal: AI-терминал который сам объясняет ошибки и предлагает команды — работает как второй мозг в командной строке
• bolt.new: создаёт рабочий прототип приложения из текстового описания за 2 минуты — идеально для быстрой проверки идей

Плохой формат (не делай так):
• Общий совет без конкретики
• Очень длинное описание на несколько предложений

Если в результатах ничего полезного нет — ответь только словом "пусто".

Результаты поиска:
{results}"""


def _extract_tips(client: anthropic.Anthropic, query: str) -> list[str]:
    results = search_web(query)
    if not results or "Ошибка" in results or "недоступен" in results:
        return []
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": _PROMPT.format(results=results[:3000]),
            }],
        )
        text = resp.content[0].text.strip()
        if text.lower() == "пусто":
            return []
        return [l.strip() for l in text.splitlines() if l.strip().startswith("•")]
    except Exception as e:
        logger.error(f"Extraction error for '{query}': {e}")
        return []


def run_update_sync(client: anthropic.Anthropic) -> int:
    """Полный цикл обновления. Вызывать через run_in_executor."""
    logger.info("Knowledge update started")
    count = 0

    for query in SEARCH_QUERIES:
        tips = _extract_tips(client, query)
        for tip in tips:
            if tip and len(tip) > 10:
                db.add_discovery(tip, source=query)
                count += 1
        time.sleep(0.5)  # не спамить Tavily

    db.trim_discoveries(keep=40)
    logger.info(f"Knowledge update complete: {count} new tips")
    return count
