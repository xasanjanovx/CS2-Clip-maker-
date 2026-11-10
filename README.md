# CS2 Clip Maker (`cs2-clip-maker`)

Автономный **Agent Skill** и CLI-инструмент на Python для автоматического создания хайлайт-видео (вертикальных **9:16** для Shorts/Reels/TikTok, **4:5**, **9:16-clean** или **16:9**) из любых демозаписей **Counter-Strike 2** (`.dem`, `.dem.gz`, `.dem.zst`, `.zip`).

Self-contained **Agent Skill** & CLI that turns any CS2 demo into ready-to-post highlight MP4s with enlarged top killfeed, bottom info HUD cards, decoded in-game Opus team voice chat, and optional background music.

---

## Возможности / Features

- **Умный разбор любых демок (`demoparser2`):**
  - Поддерживает `.dem`, `.dem.gz` (FACEIT), `.dem.zst` (Valve MM/Premier) и `.zip`-архивы с несколькими картами сразу.
  - Автоматически исключает **разминку** и **ножевые раунды** (кроме режима `--preset knife_zeus --include-knife-rounds`).
  - Детектирует реальные **ван-тапы (1-taps)** через анализ `player_hurt` + `headshot`, клатчи **1vX** (посекундный трекинг живых игроков), эйсы **5K**, серии **4K/3K**, фраги с **Deagle** и пистолетов, нож и Zeus x27.
  - **Tight-Trimming:** автоматически вырезает долгие пустые паузы между фрагами в раунде, оставляя плотный экшен и честные подписи на карточках.
- **Пресеты выбора моментов (`--preset`):**
  - `all` — сбалансированная подборка лучших моментов (эйсы, клатчи 1vX, 4K/3K, Deagle, быстрые 1-тапы, спецфраги).
  - `aces`, `clutches`, `deagle`, `pistols`, `1taps`, `knife_zeus`.
- **Качественная запись 1080p 60fps (Windows):**
  - Группирует все моменты по каждой демке в одну сессию `CS2 + HLAE + server.dll` (`-insecure`).
  - Гарантированно откатывает `gameinfo.gi` в `finally` (безопасно для VAC).
- **Войс-чат из демки (`PyAV` + Opus):**
  - Декодирует голосовой чат тиммейтов прямо из `.dem` без записи микрофона вручную (при отсутствии войса в демке мягко создаёт тихую дорожку без сбоев).
- **Готовый монтаж (`Pillow` + `FFmpeg`):**
  - **Вертикальный 9:16:** размытый фон + чёткий центр 16:9 + **увеличенный киллчат сверху** + стильная **инфо-плашка снизу** (тип момента, оружие, игрок, карта и раунд, RU/EN) + микс игры, войса и локальной музыки `--music`.

---

## Установка / Installation

### Требования
- [`uv`](https://docs.astral.sh/uv/) (менеджер Python-окружений и зависимостей).
- Windows + установленный **CS2** (Steam) — для этапа захвата кадров (`record` / `run`). Этапы `analyze`, `extract-voice` и `render` работают кроссплатформенно.
- **FFmpeg** и **HLAE** скачиваются автоматически при первом запуске или командой `update-tools`.

### Как подключить как Agent Skill (Antigravity / Codex / Claude Code)

Клонируйте репозиторий прямо в каталог глобальных скиллов (например, `%USERPROFILE%\.gemini\config\skills\cs2-clip-maker` для Antigravity 2.0):

```powershell
git clone https://github.com/xasanjanovx/CS2-Clip-maker-.git "$HOME\.gemini\config\skills\cs2-clip-maker"
cd "$HOME\.gemini\config\skills\cs2-clip-maker"
uv sync
uv run scripts/cs2_clip_maker.py doctor --output doctor.json
```

---

## Быстрый старт / Quick Start

### 1. Проверка окружения (`doctor`)

```powershell
uv run scripts/cs2_clip_maker.py doctor --output outputs/doctor.json
```

### 2. Полный цикл одной командой (`run`)

```powershell
uv run scripts/cs2_clip_maker.py run `
  --demos "C:\Demos\match_1.dem" "C:\Demos\match_pack.zip" `
  --preset all `
  --limit 20 `
  --aspect 9:16 `
  --tight-trim `
  --lang ru `
  --music "C:\Music\background.mp3" `
  --output "outputs\cs2_highlights_9x16.mp4"
```

> Аргумент `--limit` обязателен, чтобы выборка никогда не обрезалась скрытым образом (`--limit 0` снимает ограничение и берёт все подходящие моменты).

### 3. Пошаговый запуск (для ручной проверки списка моментов)

```powershell
# Шаг 1: Анализ и отбор моментов -> manifest.json
uv run scripts/cs2_clip_maker.py analyze `
  --demos "C:\Demos\match.dem" `
  --preset all `
  --limit 0 `
  --tight-trim `
  --exclude-round "Dust 2:10" `
  --output outputs/manifest.json

# Шаг 2: Запись POV через CS2 + HLAE (закройте CS2 или добавьте --kill-cs2)
uv run scripts/cs2_clip_maker.py record `
  --manifest outputs/manifest.json `
  --kill-cs2 `
  --output outputs/recorded.json

# Шаг 3: Извлечение Opus войс-чата из демок
uv run scripts/cs2_clip_maker.py extract-voice `
  --manifest outputs/recorded.json `
  --output outputs/voiced.json

# Шаг 4: Финальный монтаж 9:16 с киллчатом сверху, инфо-карточками и музыкой
uv run scripts/cs2_clip_maker.py render `
  --manifest outputs/voiced.json `
  --aspect 9:16 `
  --killfeed `
  --info-cards `
  --music "C:\Music\track.mp3" `
  --output outputs/final_supercut.mp4
```

---

## Структура проекта

```text
CS2-Clip-maker-/
├── SKILL.md                          # Инструкция скилла для AI-агента
├── README.md                         # Документация проекта
├── LICENSE                           # MIT License + атрибуции
├── pyproject.toml                    # Конфигурация пакета и зависимостей (uv)
├── scripts/
│   └── cs2_clip_maker.py             # Единая CLI-точка входа (argparse)
├── src/cs2_clip_maker/
│   ├── cli.py                        # Команды doctor, update-tools, analyze, record, extract-voice, render, run
│   ├── presets.py                    # Пресеты, фильтр разминки/ножевых раундов, tight-trim, RU/EN HUD-карточки
│   ├── voice.py                      # Декодер Opus-войса из .dem через demoparser2 + PyAV
│   ├── composer.py                   # Генерация Pillow HUD-оверлеев и рендер 9:16 / 4:5 / 16:9 в FFmpeg
│   └── highlights/                   # Движок парсинга, скоринга, клатчей и HLAE/CSDM-записи
├── vendor/cs2-plugin/                # Плагин CS2 (akiver/cs-demo-manager, MIT)
├── references/
│   ├── presets_and_layouts.md        # Спецификация пресетов, tight-trim и координат композинга
│   └── troubleshooting.md            # Диагностика HLAE, VAC и Store Python
└── tests/                            # Юнит-тесты
```

---

## Атрибуция и лицензия / License & Attribution

Код проекта распространяется под лицензией **MIT** (см. [`LICENSE`](LICENSE)).
- Механика автоматической записи и базового анализа адаптирована из [`Rovniy/cs2-highlights-maker`](https://github.com/Rovniy/cs2-highlights-maker) (MIT).
- Серверный плагин `vendor/cs2-plugin/server*.dll` взят из [`akiver/cs-demo-manager`](https://github.com/akiver/cs-demo-manager) (MIT, подробности в [`vendor/cs2-plugin/NOTICE`](vendor/cs2-plugin/NOTICE)).
- Личные демки (`.dem`, `.zip`), видеоклипы (`.mp4`) и музыкальные треки (`.mp3`, `.wav`) исключены из репозитория через `.gitignore`.
