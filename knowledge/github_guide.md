# GitHub — Полное руководство для разработчика

## ОСНОВНЫЕ ПОНЯТИЯ

**Git** — система контроля версий (локальная)
**GitHub** — облачная платформа для хранения git-репозиториев
**Репозиторий (repo)** — папка проекта с историей изменений
**Commit** — "снимок" изменений с описанием
**Branch** — параллельная ветка разработки
**Pull Request (PR)** — запрос на слияние ветки
**Fork** — копия чужого репозитория на вашем аккаунте
**Clone** — скачать репозиторий на компьютер
**Push** — отправить изменения на GitHub
**Pull** — получить изменения с GitHub
**Merge** — слить ветки вместе

---

## ПЕРВОНАЧАЛЬНАЯ НАСТРОЙКА

```bash
# Установить git (Mac)
brew install git

# Настроить имя и email (обязательно!)
git config --global user.name "Ваше Имя"
git config --global user.email "ваш@email.com"

# Проверить настройки
git config --list

# Настроить редактор (VS Code)
git config --global core.editor "code --wait"

# SSH-ключ для GitHub (безопасное подключение)
ssh-keygen -t ed25519 -C "ваш@email.com"
# Нажать Enter на все вопросы
cat ~/.ssh/id_ed25519.pub
# Скопировать и добавить в GitHub → Settings → SSH Keys
```

---

## СОЗДАНИЕ РЕПОЗИТОРИЯ

### Вариант 1: Новый проект
```bash
# Создать папку
mkdir my-project
cd my-project

# Инициализировать git
git init

# Создать первый файл
echo "# My Project" > README.md

# Добавить файлы
git add README.md

# Первый коммит
git commit -m "Initial commit"

# Подключить к GitHub (создать repo на сайте сначала!)
git remote add origin git@github.com:USERNAME/my-project.git

# Отправить на GitHub
git push -u origin main
```

### Вариант 2: Клонировать существующий
```bash
# Клонировать по SSH (нужен SSH-ключ)
git clone git@github.com:USERNAME/repo-name.git

# Клонировать по HTTPS
git clone https://github.com/USERNAME/repo-name.git

# Клонировать в конкретную папку
git clone https://github.com/USERNAME/repo-name.git my-folder
```

---

## БАЗОВЫЕ КОМАНДЫ

```bash
# Статус — что изменилось?
git status

# Разница — что конкретно изменилось?
git diff

# Добавить все изменения
git add .

# Добавить конкретный файл
git add src/app.py

# Добавить часть файла (интерактивно)
git add -p src/app.py

# Коммит с сообщением
git commit -m "Описание изменений"

# Добавить + закоммитить отслеживаемые файлы
git commit -am "Быстрый коммит"

# История коммитов
git log
git log --oneline
git log --oneline --graph

# Отменить последний коммит (сохранить изменения)
git reset --soft HEAD~1

# Посмотреть конкретный коммит
git show abc123
```

---

## ВЕТКИ (BRANCHES)

```bash
# Список веток
git branch

# Создать ветку
git branch feature/my-feature

# Переключиться на ветку
git checkout feature/my-feature

# Создать и переключиться (одна команда)
git checkout -b feature/my-feature
# Современный вариант:
git switch -c feature/my-feature

# Слить ветку в текущую
git merge feature/my-feature

# Удалить ветку
git branch -d feature/my-feature

# Переименовать ветку
git branch -m старое-имя новое-имя

# Посмотреть все ветки (включая remote)
git branch -a
```

---

## РАБОТА С GITHUB (REMOTE)

```bash
# Отправить изменения
git push

# Отправить новую ветку
git push -u origin feature/my-feature

# Получить изменения
git pull

# Получить без слияния (посмотреть)
git fetch

# Посмотреть удалённые репозитории
git remote -v

# Добавить remote
git remote add upstream https://github.com/ORIGINAL/repo.git

# Обновиться из upstream (для forked repos)
git fetch upstream
git merge upstream/main
```

---

## PULL REQUESTS (PR)

### Создать PR через GitHub CLI (gh)
```bash
# Установить gh
brew install gh

# Авторизоваться
gh auth login

# Создать PR
gh pr create --title "Название" --body "Описание"

# Создать Draft PR
gh pr create --draft --title "В процессе"

# Просмотреть список PR
gh pr list

# Ревью PR
gh pr review 123 --approve
gh pr review 123 --comment -b "Комментарий"

# Слить PR
gh pr merge 123

# Checkout PR для тестирования
gh pr checkout 123
```

### Создать PR вручную:
1. Запушить ветку: `git push -u origin feature/my-feature`
2. Открыть github.com/YOUR/repo
3. Нажать "Compare & pull request"
4. Заполнить название и описание
5. Нажать "Create pull request"

---

## .GITIGNORE

Файл `.gitignore` — список файлов/папок которые git НЕ отслеживает.

```gitignore
# Зависимости
node_modules/
venv/
.venv/

# Переменные окружения (ВСЕГДА игнорируй!)
.env
.env.local
*.env

# Системные файлы
.DS_Store
Thumbs.db

# Сборка
dist/
build/
__pycache__/
*.pyc

# IDE
.idea/
.vscode/settings.json

# Логи
*.log
logs/

# Временные файлы
*.tmp
*.cache
```

Сгенерировать .gitignore: https://gitignore.io

---

## ФОРК И УЧАСТИЕ В OPEN SOURCE

```bash
# 1. Форкнуть репозиторий на GitHub (кнопка Fork)

# 2. Клонировать свой форк
git clone git@github.com:ВАШ_USERNAME/repo.git

# 3. Добавить оригинал как upstream
git remote add upstream https://github.com/ORIGINAL/repo.git

# 4. Создать ветку для изменений
git checkout -b fix/my-fix

# 5. Сделать изменения, добавить, закоммитить
git add .
git commit -m "Исправить баг в авторизации"

# 6. Отправить в свой форк
git push origin fix/my-fix

# 7. Создать PR из форка в оригинал на GitHub

# Обновить форк из оригинала:
git fetch upstream
git checkout main
git merge upstream/main
git push origin main
```

---

## РАЗРЕШЕНИЕ КОНФЛИКТОВ

```bash
# При merge конфликте:
git merge feature/branch
# CONFLICT: merge conflict in src/app.py

# Открыть файл, найти маркеры:
# <<<<<<< HEAD
# Ваши изменения
# =======
# Изменения из ветки
# >>>>>>> feature/branch

# Отредактировать файл вручную (убрать маркеры)
# Добавить и закоммитить:
git add src/app.py
git commit -m "Разрешить конфликт слияния"

# Отменить слияние если что-то пошло не так:
git merge --abort
```

---

## ТЕГИ И РЕЛИЗЫ

```bash
# Создать тег
git tag v1.0.0

# Тег с описанием
git tag -a v1.0.0 -m "Первый релиз"

# Отправить теги на GitHub
git push --tags

# Создать релиз через gh
gh release create v1.0.0 --title "v1.0.0" --notes "Что нового"
```

---

## GITHUB ACTIONS (CI/CD)

Файлы в `.github/workflows/` автоматически выполняются при событиях.

```yaml
# .github/workflows/test.yml
name: Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: pytest
```

---

## ПОЛЕЗНЫЕ КОМАНДЫ GH CLI

```bash
# Issues
gh issue list
gh issue create --title "Баг" --body "Описание"
gh issue view 123

# Repos
gh repo create my-repo --public
gh repo clone username/repo
gh repo view --web

# Actions
gh workflow list
gh run list
gh run view 123

# Gists
gh gist create file.py --public
```

---

## КОНВЕНЦИИ КОММИТОВ (Conventional Commits)

```
feat: добавить новую функцию
fix: исправить баг
docs: обновить документацию
style: форматирование (без логических изменений)
refactor: рефакторинг кода
test: добавить/исправить тесты
chore: обновить зависимости, конфиги

Примеры:
feat: добавить авторизацию через Google
fix: исправить утечку памяти в обработчике сокетов
docs: обновить README с инструкцией по установке
```

---

## ВЕТВЛЕНИЕ — СТРАТЕГИИ

### Git Flow (классика):
- `main` — продакшн
- `develop` — разработка
- `feature/*` — новые фичи
- `hotfix/*` — срочные фиксы

### GitHub Flow (проще):
- `main` — всегда деплоится
- `feature/*` — ветка → PR → merge в main

### Trunk-based (продвинутый):
- `main` — единственная ветка
- Feature flags вместо долгих веток

---

## ПОИСК НА GITHUB

```
# В строке поиска GitHub:
repo:username/reponame    # В конкретном репо
language:python           # По языку
stars:>100               # Популярные
in:readme "claude code"   # В README

# Через gh:
gh search repos "claude code skills" --language python
gh search code "def my_function" --repo username/repo
gh search issues "memory leak" --repo username/repo
```
