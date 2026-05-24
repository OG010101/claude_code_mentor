import os
import json
import urllib.request
import urllib.parse

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")


def search_web(query: str) -> str:
    """Поиск актуальной информации через Tavily."""
    if not TAVILY_API_KEY:
        return "Поиск недоступен: нет TAVILY_API_KEY"

    url = "https://api.tavily.com/search"
    payload = json.dumps({
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
    }).encode()

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        parts = []
        if data.get("answer"):
            parts.append(f"**Краткий ответ:** {data['answer']}\n")

        for r in data.get("results", [])[:4]:
            title = r.get("title", "")
            url_r = r.get("url", "")
            content = r.get("content", "")[:400]
            parts.append(f"**{title}**\n{content}\nИсточник: {url_r}")

        return "\n\n".join(parts) if parts else "Ничего не найдено"

    except Exception as e:
        return f"Ошибка поиска: {e}"


def search_github(query: str, search_type: str = "repositories") -> str:
    """Поиск на GitHub: repositories, code, или topics."""
    base = "https://api.github.com/search"

    if search_type == "code":
        endpoint = f"{base}/code"
    elif search_type == "topics":
        endpoint = f"{base}/topics"
    else:
        endpoint = f"{base}/repositories"

    params = urllib.parse.urlencode({"q": query, "per_page": 5, "sort": "stars"})
    url = f"{endpoint}?{params}"

    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())

        items = data.get("items", [])
        if not items:
            return "Ничего не найдено на GitHub"

        parts = []
        for item in items[:5]:
            if search_type == "repositories":
                name = item.get("full_name", "")
                desc = item.get("description", "Нет описания")
                stars = item.get("stargazers_count", 0)
                url_r = item.get("html_url", "")
                lang = item.get("language", "")
                parts.append(f"**{name}** ⭐{stars} [{lang}]\n{desc}\n{url_r}")
            elif search_type == "code":
                repo = item.get("repository", {}).get("full_name", "")
                path = item.get("path", "")
                url_r = item.get("html_url", "")
                parts.append(f"**{repo}** → `{path}`\n{url_r}")

        return "\n\n".join(parts)

    except Exception as e:
        return f"Ошибка поиска GitHub: {e}"


# Описания инструментов для Claude API (tool use)
TOOLS = [
    {
        "name": "search_web",
        "description": (
            "Поиск актуальной информации в интернете. "
            "Используй для проверки последних обновлений Claude Code, "
            "новых функций, актуальной документации."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос на русском или английском",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_github",
        "description": (
            "Поиск репозиториев, скиллов, примеров кода на GitHub. "
            "Используй для поиска готовых Claude Code skills, плагинов, примеров."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Поисковый запрос (на английском лучше)",
                },
                "search_type": {
                    "type": "string",
                    "enum": ["repositories", "code"],
                    "description": "repositories — поиск репо, code — поиск в коде",
                    "default": "repositories",
                },
            },
            "required": ["query"],
        },
    },
]


def execute_tool(tool_name: str, tool_input: dict) -> str:
    if tool_name == "search_web":
        return search_web(tool_input["query"])
    elif tool_name == "search_github":
        return search_github(
            tool_input["query"],
            tool_input.get("search_type", "repositories"),
        )
    return f"Неизвестный инструмент: {tool_name}"
