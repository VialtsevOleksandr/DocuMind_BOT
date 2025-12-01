import os
import asyncio
import logging
import functions_framework
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
# Додаємо імпорти для локального запуску (Polling)
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# --- КОНФІГУРАЦІЯ ---

# 👇 ВСТАВТЕ ВАШ ТОКЕН СЮДИ (всередину лапок)
# Це дозволить запускати бота без налаштування терміналу
TOKEN = ""

try:
    bot = telegram.Bot(token=TOKEN)
except Exception as e:
    print(f"⚠️ Увага: Токен не валідний. {e}")
    bot = None

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- MOCK DATA ---
MOCK_OCR_TEXT = """
РАХУНОК № 12345
Дата: 01.12.2024
Постачальник: ТОВ "Рога та Копита"
Клієнт: Іваненко І.І.

Товари:
1. Розробка ПЗ - 50 000 грн
2. Хостинг серверів - 2 000 грн
3. Технічна підтримка - 5 000 грн

Всього до сплати: 57 000 грн.
Термін оплати: до 10.12.2024.
"""

# --- ДОПОМІЖНІ ФУНКЦІЇ ---

def get_main_keyboard():
    """Повертає головну клавіатуру з діями."""
    keyboard = [
        [InlineKeyboardButton("📝 Стислий зміст", callback_data="summarize")],
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="translate_en"),
            InlineKeyboardButton("🇺🇦 Українська", callback_data="translate_ua")
        ],
        [InlineKeyboardButton("🔑 Ключові моменти", callback_data="keywords")],
        # Додаємо кнопку очищення/нового сканування
        [InlineKeyboardButton("🗑️ Завершити / Нове фото", callback_data="new_scan")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    """Повертає клавіатуру тільки з кнопкою Назад."""
    keyboard = [
        [InlineKeyboardButton("🔙 Назад до меню", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- MOCKS ---
async def mock_vision_api(file_id):
    await asyncio.sleep(1) 
    return MOCK_OCR_TEXT

async def mock_gemini_api(text, command):
    await asyncio.sleep(1)
    # Використовуємо одинарні зірочки для жирного шрифту в Markdown Legacy
    if command == "summarize":
        return "📝 *Стислий зміст:*\nЦе рахунок на оплату IT-послуг (розробка, хостинг, підтримка) на загальну суму 57 000 грн від ТОВ 'Рога та Копита'."
    elif command == "translate_en":
        return "🇬🇧 *Translation:*\nINVOICE # 12345\nDate: 01.12.2024\nSupplier: Horns and Hooves LLC\nTotal due: 57,000 UAH."
    elif command == "translate_ua":
        return "🇺🇦 *Переклад:*\n(Текст вже українською, але тут був би переклад)."
    elif command == "keywords":
        return "🔑 *Ключові моменти:*\n- *Сума:* 57 000 грн\n- *Дата:* 01.12.2024\n- *Дедлайн:* 10.12.2024"
    else:
        return "❓ Невідома команда."

# --- ЛОГІКА ---

async def start_command(update: Update):
    await bot.send_message(
        chat_id=update.effective_chat.id,
        text="👋 Привіт! Я *DocuMind*.\n📸 *Надішли фото*, і я запропоную варіанти обробки.",
        parse_mode='Markdown'
    )

async def process_photo_interactive(update: Update):
    chat_id = update.effective_chat.id
    status_msg = await bot.send_message(chat_id, "👀 Дивлюся на фото...")
    
    raw_text = await mock_vision_api("dummy_file_id")
    
    await bot.delete_message(chat_id, status_msg.message_id)
    
    await bot.send_message(
        chat_id=chat_id,
        text=f"📄 *Я знайшов текст:*\n\n`{raw_text}`\n\nЩо з ним зробити?",
        reply_markup=get_main_keyboard(), # Використовуємо функцію
        parse_mode='Markdown'
    )

async def process_callback(update: Update):
    query = update.callback_query
    command = query.data
    
    await query.answer() # Прибирає годинник завантаження
    
    # 1. Логіка повернення НАЗАД
    if command == "back_to_menu":
        # Відновлюємо оригінальний текст і головне меню
        # У реальному боті тут ми б брали текст з Firestore за message_id
        await query.edit_message_text(
            text=f"📄 *Оригінальний текст:*\n\n`{MOCK_OCR_TEXT}`\n\nЩо з ним зробити?",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
        return

    # 2. Логіка "НОВЕ СКАНУВАННЯ" (Видалення)
    if command == "new_scan":
        await query.delete_message()
        await bot.send_message(
            chat_id=query.message.chat_id, 
            # Використовуємо одинарні зірочки для жирного
            text="🗑️ Чат очищено. Надішліть *нове фото* для обробки!",
            parse_mode='Markdown'
        )
        return

    # 3. Логіка ОБРОБКИ (AI)
    await query.edit_message_text(text="🧠 *Аналізую...*", parse_mode='Markdown')
    result_text = await mock_gemini_api(MOCK_OCR_TEXT, command)
    
    # Додаємо кнопку "Назад" до результату
    await query.edit_message_text(
        text=result_text, 
        reply_markup=get_back_keyboard(), # Додаємо кнопку повернення
        parse_mode='Markdown'
    )

async def main_logic(update: Update):
    if update.message:
        # Перевірка на команди
        if update.message.text and update.message.text.startswith('/start'):
            await start_command(update)
            return # Важливо вийти з функції
        
        # Перевірка на наявність фото
        if update.message.photo:
            if update.message.caption:
                # Тут поки що заглушка для direct mode, можна додати пізніше або використати стару
                await bot.send_message(update.effective_chat.id, "⚡ Швидка команда прийнята (Mock Mode).")
            else:
                await process_photo_interactive(update)
            return # Важливо вийти з функції

        # Якщо ми тут, значить це не команда /start і не фото.
        # Відправляємо повідомлення про помилку.
        await bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ *Я розумію тільки фотографії!*\n\nБудь ласка, надішліть мені зображення документа (стиснуте, не як файл), щоб я міг його прочитати.",
            parse_mode='Markdown'
        )

    elif update.callback_query:
        await process_callback(update)

@functions_framework.http
def telegram_webhook(request):
    if request.method != "POST": return "OK", 200
    try:
        update = Update.de_json(request.get_json(force=True), bot)
        asyncio.run(main_logic(update))
        return "OK", 200
    except: return "Error", 500

if __name__ == "__main__":
    if TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("❌ Вставте токен!")
        exit(1)
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    async def local_handler(update, context): await main_logic(update)
    application.add_handler(MessageHandler(filters.ALL, local_handler))
    application.add_handler(CallbackQueryHandler(local_handler))
    
    print("🚀 Бот запущено!")
    application.run_polling()