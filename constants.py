"""
Все настройки PyLab. Переменные окружения не используются —
меняйте значения прямо здесь.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# --- Django -----------------------------------------------------------------
# ОБЯЗАТЕЛЬНО замените перед выкладкой в интернет (любая длинная случайная строка)
SECRET_KEY = "change-me-to-a-long-random-string-pylab-2026"
DEBUG = True
# В локалке "*" удобно (студенты заходят по IP). Онлайн укажите свой домен.
ALLOWED_HOSTS = ["*"]
# Для онлайна с HTTPS: ["https://pylab.example.kz"]
CSRF_TRUSTED_ORIGINS = []

TIME_ZONE = "Asia/Almaty"
LANGUAGE_CODE = "ru"

# --- База данных --------------------------------------------------------------
# "sqlite" — для пар в локалке, "postgres" — для онлайна
DB_ENGINE = "sqlite"
SQLITE_PATH = BASE_DIR / "db.sqlite3"
PG_NAME = "pylab"
PG_USER = "pylab"
PG_PASSWORD = ""
PG_HOST = "127.0.0.1"
PG_PORT = 5432

# --- Песочница (запуск кода студентов) ---------------------------------------
# Интерпретатор, которым исполняется код студентов
SANDBOX_PYTHON = "/usr/bin/python3"
# True — изоляция через bubblewrap (sudo apt install bubblewrap). Обязательно онлайн!
# False — простой запуск без изоляции: только для проверки на своём компьютере.
SANDBOX_USE_BWRAP = True
SANDBOX_BWRAP = "/usr/bin/bwrap"
# Необязательно: отдельный системный пользователь без прав, под которым
# запускается код (см. README). None — запуск под пользователем сервера.
SANDBOX_RUN_AS = None
# Каталог для временных файлов запусков
SANDBOX_TMP_DIR = BASE_DIR / "sandbox_tmp"

# Лимиты по умолчанию (у каждой задачи можно задать свои время и память)
DEFAULT_TIME_LIMIT = 2          # секунды
DEFAULT_MEMORY_LIMIT = 256      # МБ
OUTPUT_LIMIT_KB = 1024          # максимальный объём вывода программы
MAX_PROCESSES = 16              # защита от fork-бомб

# Сколько программ одновременно может выполняться на сервере
JUDGE_WORKERS = 4               # проверка отправок
MAX_PARALLEL_RUNS = 6           # кнопка «Запустить»
RUN_WAIT_SECONDS = 15           # сколько ждать свободного места для «Запустить»
