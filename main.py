import asyncio
import json
import logging
import os
from pathlib import Path
import time
from typing import Dict, Any, Set, List

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==========================
# ⚙️ НАСТРОЙКИ
# ==========================
# Укажи токен бота прямо здесь (в коде)
BOT_TOKEN = "8460442737:AAGCKd60R__tn2W83EdHpQ21Qah4nty1xj4"

# Укажи себя как админа:
# Предпочтительно ADMIN_ID (число). Узнать можно у @userinfobot
# Либо ADMIN_USERNAME как запасной вариант.
ADMIN_ID = 5083696616 # <-- Укажи свой числовой ID (узнать у @userinfobot), 0 = проверка по username  # 0 = не задан
ADMIN_USERNAME = "Ma3stro274"  # <-- Твой @username без @


# Директория и файлы данных
DATA_DIR = Path("data")
USERS_FILE = DATA_DIR / "users.json"
STATE_FILE = DATA_DIR / "user_state.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
STATS_FILE = DATA_DIR / "stats.json"
STATS_FILE = DATA_DIR / "stats.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("proxy-bot")

# В памяти помечаем, что админ ждёт текст для рассылки
AWAITING_BROADCAST: Set[int] = set()

# ==========================
# 🧰 УТИЛИТЫ ДЛЯ ХРАНЕНИЯ
# ==========================

def _read_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Ошибка чтения %s — использую default", path)
    return default


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# users.json: {"users": [id, id, ...]}
def get_all_users() -> List[int]:
    data = _read_json(USERS_FILE, {"users": []})
    return [int(x) for x in data.get("users", [])]


def add_user(user_id: int) -> None:
    users = set(get_all_users())
    if user_id not in users:
        users.add(user_id)
        _write_json(USERS_FILE, {"users": sorted(users)})


# user_state.json: {"<uid>": {"started": bool}}
def has_started(user_id: int) -> bool:
    data = _read_json(STATE_FILE, {})
    return str(user_id) in data and bool(data[str(user_id)].get("started", False))


def set_started(user_id: int, value: bool) -> None:
    data = _read_json(STATE_FILE, {})
    data[str(user_id)] = {"started": value}
    _write_json(STATE_FILE, data)


# settings.json: {"germany_enabled": true}
def get_settings() -> Dict[str, Any]:
    data = _read_json(SETTINGS_FILE, None)
    if not data:
        data = {"germany_enabled": True}
        _write_json(SETTINGS_FILE, data)
    return data


def update_settings(**kwargs) -> Dict[str, Any]:
    data = get_settings()
    data.update(kwargs)
    _write_json(SETTINGS_FILE, data)
    return data


# ==========================\n# 📈 СТАТИСТИКА СООБЩЕНИЙ\n# ==========================\n\n\ndef get_stats() -> Dict[str, Any]:\n    data = _read_json(STATS_FILE, {"messages": []})\n    if "messages" not in data:\n        data["messages"] = []\n    return data\n\n\ndef record_message(user_id: int) -> None:\n    st = get_stats()\n    now = int(time.time())\n    msgs = list(st.get("messages", []))\n    msgs.append(now)\n    cutoff = now - 3 * 86400  # храним последние ~3 суток\n    msgs = [t for t in msgs if t >= cutoff]\n    st["messages"] = msgs\n    _write_json(STATS_FILE, st)\n\n\ndef count_messages_last_24h() -> int:\n    st = get_stats()\n    now = int(time.time())\n    cutoff = now - 86400\n    return sum(1 for t in st.get("messages", []) if t >= cutoff)\n\n\n# ==========================\n# 🔐 ПРОВЕРКА АДМИНА
# ==========================

def is_admin(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    if ADMIN_ID:
        return user.id == ADMIN_ID
    if user.username:
        return user.username.lower() == ADMIN_USERNAME.lower()
    return False


# ==========================
# 🧩 РАЗМЕТКА КНОПОК
# ==========================

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 Магазин прокси", callback_data="shop")],
    ])


def kb_shop_menu(germany_enabled: bool) -> InlineKeyboardMarkup:
    rows = []
    if germany_enabled:
        rows.append([InlineKeyboardButton("🇩🇪 Германия", callback_data="shop_de")])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def kb_germany() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✉️ Написать админу", url=f"https://t.me/{ADMIN_USERNAME}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_shop")],
    ])


def kb_admin_panel(settings: Dict[str, Any]) -> InlineKeyboardMarkup:
    label = "Скрыть категорию 'Германия'" if settings.get("germany_enabled", True) else "Показать категорию 'Германия'"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🇩🇪 {label}", callback_data="admin_toggle_de")],
        [InlineKeyboardButton("📣 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_main")],
    ])


def kb_back_to_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_admin")]])


# ==========================
# 📨 СООБЩЕНИЯ-ЭКРАНЫ
# ==========================

async def send_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False) -> None:
    text = "🧭 Вы в главном меню. Выберите раздел:"
    if edit and update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(text, reply_markup=kb_main_menu())
    else:
        await update.effective_chat.send_message(text, reply_markup=kb_main_menu())


async def send_shop(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False) -> None:
    settings = get_settings()
    germany_enabled = settings.get("germany_enabled", True)
    text = "🌍 Выберите локацию прокси:" if germany_enabled else "🚫 Нет доступных proxy."
    if edit and update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(text, reply_markup=kb_shop_menu(germany_enabled))
    else:
        await update.effective_chat.send_message(text, reply_markup=kb_shop_menu(germany_enabled))


async def send_germany(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False) -> None:
    text = (
        "🇩🇪 Германия\n\n"
        "Чтобы получить proxy — напишите админу 💬."
    )
    if edit and update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(text, reply_markup=kb_germany(), disable_web_page_preview=True)
    else:
        await update.effective_chat.send_message(text, reply_markup=kb_germany(), disable_web_page_preview=True)


async def send_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False) -> None:
    settings = get_settings()
    users = get_all_users()
    total = len(users)
    state = _read_json(STATE_FILE, {})
    started = sum(1 for uid in users if state.get(str(uid), {}).get("started", False))
    not_started = max(total - started, 0)
    msgs24 = count_messages_last_24h()
    de_status = "включена ✅" if settings.get("germany_enabled", True) else "выключена ❌"

    text = (
        "🛠️ Админ-панель\n\n"
        "📊 Статистика:\n"
        f"• Пользователи: {total}\n"
        f"• Нажали Start: {started}\n"
        f"• Не нажали: {not_started}\n"
        f"• Сообщений за 24ч: {msgs24}\n"
        f"• Германия: {de_status}"
    )
    markup = kb_admin_panel(settings)
    if edit and update.callback_query and update.callback_query.message:
        await update.callback_query.message.edit_text(text, reply_markup=markup)
    else:
        await update.effective_chat.send_message(text, reply_markup=markup)


# ==========================
# 🧠 ХЕНДЛЕРЫ КОМАНД/СООБЩЕНИЙ
# ==========================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    add_user(user.id)
    # учитываем /start как сообщение в статистике
    record_message(user.id)

    if has_started(user.id):
        await send_main_menu(update, context)
        return

    # Первый визит: используем нативную синюю кнопку Telegram (управляется самим Telegram и появляется до первого сообщения).
    # Здесь просто отмечаем запуск и показываем меню.
    set_started(user.id, True)
    await update.effective_chat.send_message("👋 Добро пожаловать! Бот запущен.")
    await send_main_menu(update, context)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not update.message:
        return

    add_user(user.id)

    # Режим рассылки для админа — отправляем текст как есть всем пользователям
    if user.id in AWAITING_BROADCAST and is_admin(update):
        text = update.message.text or ""
        AWAITING_BROADCAST.discard(user.id)
        await update.message.reply_text("📨 Отправляю рассылку…", reply_markup=kb_admin_panel(get_settings()))
        sent, errors = 0, 0
        for uid in get_all_users():
            try:
                if uid == user.id:
                    # Чтобы админ тоже видел итоговое сообщение
                    await context.bot.send_message(chat_id=uid, text=text)
                else:
                    await context.bot.send_message(chat_id=uid, text=text)
                sent += 1
            except Exception as e:
                logger.warning("Не удалось отправить пользователю %s: %s", uid, e)
                errors += 1
        await update.effective_chat.send_message(f"✅ Готово. Доставлено: {sent}, ошибок: {errors}.")
        return

    # Первый клик по кнопке Start
    msg = (update.message.text or "").strip()
    if msg == START_BUTTON_TEXT and not has_started(user.id):
        set_started(user.id, True)
        await update.message.reply_text("✅ Бот запущен", reply_markup=ReplyKeyboardRemove())
        await send_main_menu(update, context)
        return

    # Любые другие сообщения
    await update.message.reply_text("👉 Выберите действие из меню ниже.")


async def cmd_adminpanel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update):
        await update.effective_chat.send_message("⛔ Доступ запрещён.")
        return
    await send_admin_panel(update, context)


# ==========================
# 🔘 CALLBACKS (ИНЛАЙН-КНОПКИ)
# ==========================

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    data = query.data or ""

    if data == "shop":
        await send_shop(update, context, edit=True)
        return

    if data == "shop_de":
        await send_germany(update, context, edit=True)
        return

    if data == "back_main":
        await send_main_menu(update, context, edit=True)
        return

    if data == "back_shop":
        await send_shop(update, context, edit=True)
        return

    if data == "back_admin":
        if is_admin(update):
            await send_admin_panel(update, context, edit=True)
        else:
            await send_main_menu(update, context, edit=True)
        return

    if data == "admin_toggle_de":
        if not is_admin(update):
            await query.edit_message_text(
            "📝 Введите текст рассылки одним сообщением.\nБез лишней воды — будет отправлено как есть.",
            reply_markup=kb_back_to_admin(),
        )
        return


# ==========================
# 🚀 ЗАПУСК
# ==========================

async def main() -> None:
    if not BOT_TOKEN or BOT_TOKEN == "PASTE_YOUR_TOKEN_HERE":
        raise RuntimeError("Укажи токен в константе BOT_TOKEN.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("adminpanel", cmd_adminpanel))

    app.add_handler(CallbackQueryHandler(on_callback))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("Бот запущен. Нажми Ctrl+C для остановки.")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.run(main())
