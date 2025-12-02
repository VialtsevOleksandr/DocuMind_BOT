import os
import io
import asyncio
import logging
import functions_framework
import telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# --- GOOGLE CLOUD IMPORTS ---
from google.cloud import vision
from google.cloud import firestore
from google.cloud import secretmanager
import google.generativeai as genai

# --- 1. КОНФІГУРАЦІЯ ТА КОНСТАНТИ ---
PROJECT_ID = os.environ.get("GCP_PROJECT", "documind-478420")
REGION_ID = "europe-central2" 
MODEL_NAME = "gemini-2.5-flash"

MAX_MESSAGE_LENGTH = 3000

# Налаштування логування
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- 2. ПРОМПТИ (СИСТЕМНІ ІНСТРУКЦІЇ) ---
SYSTEM_PROMPTS = {
    "summarize": """
    Ти — елітний бізнес-асистент. Твоя мета — заощадити час користувача, надавши миттєве розуміння суті документу.
    
    СУВОРІ ПРАВИЛА ФОРМАТУВАННЯ:
    - Використовуй ТІЛЬКИ одинарні зірочки (*) для жирного шрифту.
    - НІКОЛИ не використовуй подвійні зірочки (**) або нижнє підкреслення (_).
    
    АЛГОРИТМ РОБОТИ:
    1. **Ідентифікація:** Визначи тип документу (Рахунок, Договір, Офіційний лист, Стаття, Технічна інструкція, Рукописна нотатка).
    2. **Адаптивний аналіз:**
       - Якщо це *Фінанси* (чек/рахунок): Вкажи кому платити, скільки, за що і дедлайн.
       - Якщо це *Договір*: Вкажи сторони, предмет договору, суму та ключові ризики/терміни.
       - Якщо це *Лист/Стаття*: Сформулюй "Executive Summary" (суть у 2-3 реченнях).
    3. **Action Items:** Якщо документ вимагає дій (оплатити, підписати, відповісти), виділи це окремим блоком.
    
    СТРУКТУРА ВІДПОВІДІ:
    👋 *Тип документу:* [Назва]
    
    💡 *Головне:*
    [Стислий опис суті своїми словами без води]
    
    🔍 *Деталі:*
    - [Пункт 1]
    - [Пункт 2]
    - [Пункт 3]
    
    ⚡ *Що треба зробити:* [Тільки якщо є явна дія]
    """,

    "translate_en": """
    Ти — сертифікований перекладач рівня Native Speaker. Переклади текст англійською мовою.
    
    СУВОРІ ПРАВИЛА:
    - Використовуй ТІЛЬКИ одинарні зірочки (*) для жирного шрифту.
    - Стиль: Business English (для документів) або Neutral (для загальних текстів).
    
    ІНСТРУКЦІЇ:
    1. Збережи оригінальну структуру абзаців та списків.
    2. Власні назви (імена, назви компаній) транслітеруй, але якщо є усталений переклад — використовуй його.
    3. Адаптуй формати дат та валют (наприклад, 01.12.2024 -> December 1, 2024).
    4. Виправляй очевидні помилки OCR в оригіналі перед перекладом.
    """,

    "translate_ua": """
    Ти — професійний перекладач і редактор української мови.
    
    СУВОРІ ПРАВИЛА:
    - Використовуй ТІЛЬКИ одинарні зірочки (*) для жирного шрифту.
    - Уникай канцеляризмів та кальок з російської чи англійської.
    
    ІНСТРУКЦІЇ:
    1. Текст має звучати природно, як написаний носієм мови.
    2. Для офіційних документів дотримуйся офіційно-ділового стилю.
    3. Збережи структуру документу.
    4. Терміни перекладай відповідно до чинних стандартів України.
    """,

    "keywords": """
    Ти — інтелектуальний аналітик даних. Твоя мета — структурувати хаос.
    
    СУВОРІ ПРАВИЛА:
    1. ТІЛЬКИ одинарні зірочки (*) для жирного.
    2. Ніякої води, тільки факти.
    3. **ГРУПУВАННЯ:** Якщо кілька фактів стосуються одного об'єкта (наприклад, характеристики однієї людини або деталі одного товару) — ГРУПУЙ їх. Не повторюй назву об'єкта в кожному рядку.
    
    ПРИКЛАД ПОГАНОГО ФОРМАТУ:
    - Характеристика Івана: Добрий
    - Характеристика Івана: Розумний
    
    ПРИКЛАД ХОРОШОГО ФОРМАТУ:
    👤 *Іван:*
      - Добрий
      - Розумний
    
    АЛГОРИТМ:
    1. Визначи головні сутності тексту (Люди, Компанії, Товари, Психотипи тощо).
    2. Створи ієрархічний список.
    3. Для фінансів: окремо виділи суми та дати.
    
    ФОРМАТ ВІДПОВІДІ:
    📂 *Категорія:* [Тип тексту]
    
    📊 *Структуровані дані:*
    
    *🔹 [Головна сутність 1]:*
       - [Характеристика/Факт]
       - [Характеристика/Факт]
    
    *🔹 [Головна сутність 2]:*
       - [Характеристика/Факт]
    """
}

# --- 3. ОТРИМАННЯ СЕКРЕТІВ ---
def get_secret(secret_id, version_id="latest"):
    try:
        if os.environ.get(secret_id):
            return os.environ.get(secret_id)

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{PROJECT_ID}/secrets/{secret_id}/versions/{version_id}"
        response = client.access_secret_version(request={"name": name})
        return response.payload.data.decode("UTF-8")
    except Exception as e:
        logger.error(f"Помилка отримання секрету {secret_id}: {e}")
        return None

# --- 4. ІНІЦІАЛІЗАЦІЯ КЛІЄНТІВ ---
try:
    TELEGRAM_TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
    GEMINI_KEY = get_secret("GEMINI_API_KEY")
    
    bot = telegram.Bot(token=TELEGRAM_TOKEN)
    vision_client = vision.ImageAnnotatorClient()
    db = firestore.Client(project=PROJECT_ID)
    
    genai.configure(api_key=GEMINI_KEY)
    gemini_model = genai.GenerativeModel(MODEL_NAME)
    logger.info(f"🚀 Система ініціалізована. Модель: {MODEL_NAME}")
    
except Exception as e:
    logger.critical(f"Critical Error: {e}")
    bot = None

# --- 5. UI: КЛАВІАТУРИ ---

def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📝 Стислий зміст", callback_data="summarize")],
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="translate_en"),
            InlineKeyboardButton("🇺🇦 Українська", callback_data="translate_ua")
        ],
        [InlineKeyboardButton("🔑 Ключові моменти", callback_data="keywords")],
        [InlineKeyboardButton("🗑️ Нове фото (Очистити)", callback_data="new_scan")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_back_keyboard():
    keyboard = [[InlineKeyboardButton("🔙 Назад до меню", callback_data="back_to_menu")]]
    return InlineKeyboardMarkup(keyboard)

def get_direct_response_keyboard():
    """Клавіатура для відповіді на пряме запитання (фото + текст)."""
    keyboard = [
        [InlineKeyboardButton("📂 Всі дії з цим документом", callback_data="back_to_menu")],
        [InlineKeyboardButton("📸 Нове фото (Очистити)", callback_data="new_scan")]
    ]
    return InlineKeyboardMarkup(keyboard)

# --- 6. CORE LOGIC ---

async def real_vision_api(image_bytes):
    try:
        image = vision.Image(content=image_bytes)
        response = vision_client.document_text_detection(image=image)
        if response.error.message: raise Exception(response.error.message)
        return response.full_text_annotation.text
    except Exception as e:
        logger.error(f"Vision API Failed: {e}")
        return None

async def real_gemini_api(text, command):
    try:
        if command in SYSTEM_PROMPTS:
            system_instruction = SYSTEM_PROMPTS[command]
        else:
            system_instruction = f"Ти корисний асистент. Проаналізуй документ згідно з запитом користувача: '{command}'. \nВАЖЛИВО: Використовуй тільки одинарні зірочки (*) для жирного шрифту."

        full_prompt = f"{system_instruction}\n\n=== ТЕКСТ ДОКУМЕНТА ===\n{text}\n======================="
        response = gemini_model.generate_content(full_prompt)
        
        clean_text = response.text.replace("**", "*") 
        return clean_text
    except Exception as e:
        logger.error(f"Gemini API Failed: {e}")
        return "⚠️ Помилка AI."

def save_to_cache(chat_id, message_id, text):
    try:
        doc_ref = db.collection("ocr_cache").document(f"{chat_id}_{message_id}")
        doc_ref.set({"text": text, "created_at": firestore.SERVER_TIMESTAMP})
    except Exception as e:
        logger.error(f"Firestore Save Error: {e}")

def get_from_cache(chat_id, message_id):
    try:
        doc_ref = db.collection("ocr_cache").document(f"{chat_id}_{message_id}")
        doc = doc_ref.get()
        if doc.exists:
            return doc.to_dict().get("text")
        return None
    except Exception as e:
        logger.error(f"Firestore Get Error: {e}")
        return None

# --- 7. HELPER: SAFE SENDING ---

async def safe_edit_message(query, text, reply_markup):
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    except telegram.error.BadRequest as e:
        if "Can't parse entities" in str(e):
            logger.warning(f"Markdown Error: {e}")
            await query.edit_message_text(
                text=text + "\n\n_(⚠️ Форматування вимкнено через помилку в символах)_",
                reply_markup=reply_markup,
                parse_mode=None 
            )
        else:
            raise e

async def send_smart_response(chat_id, text, reply_markup=None, caption_msg=None):
    if len(text) > MAX_MESSAGE_LENGTH:
        file_obj = io.BytesIO(text.encode('utf-8'))
        file_obj.name = "documind_text.txt"
        
        await bot.send_document(
            chat_id=chat_id, 
            document=file_obj, 
            caption="📂 *Текст великий, тому я зберіг його у файл.*",
            parse_mode='Markdown'
        )
        msg_text = caption_msg if caption_msg else "✅ *Готово.* Оберіть дію:"
        return await bot.send_message(chat_id, msg_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        msg_text = caption_msg + f"\n\n`{text}`" if caption_msg else f"📄 *Текст:*\n\n`{text}`"
        try:
            return await bot.send_message(
                chat_id=chat_id,
                text=msg_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
        except telegram.error.BadRequest:
             return await bot.send_message(
                chat_id=chat_id,
                text=msg_text.replace('`', '').replace('*', ''),
                reply_markup=reply_markup,
                parse_mode=None
            )

# --- 8. BOT HANDLERS ---

async def start_command(update: Update):
    welcome_text = (
        "👋 *Вітаю! Я — DocuMind AI.*\n\n"
        "📸 *Режим 1: Меню*\n"
        "Надішліть просто фото, щоб отримати меню дій (підсумок, переклад, дані).\n\n"
        "💬 *Режим 2: Пряма команда*\n"
        "Надішліть фото і *додайте підпис* (наприклад: _'Що тут сказано про податки?'_), і я одразу відповім на ваше питання."
    )
    await bot.send_message(update.effective_chat.id, welcome_text, parse_mode='Markdown')

async def clear_command(update: Update):
    """Очищення чату (візуальне) для нового сеансу."""
    await bot.send_message(
        chat_id=update.effective_chat.id,
        text="🗑️ *Історію сесії очищено.* Я готовий до нового фото!",
        parse_mode='Markdown'
    )

async def process_photo_interactive(update: Update):
    """Сценарій Б: Фото БЕЗ підпису -> Меню кнопок"""
    chat_id = update.effective_chat.id
    status_msg = await bot.send_message(chat_id, "⏳ *Аналізую зображення...*", parse_mode='Markdown')
    
    try:
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        raw_text = await real_vision_api(bytes(image_bytes))
        await bot.delete_message(chat_id, status_msg.message_id)
        
        if not raw_text:
            await bot.send_message(chat_id, "⚠️ *Текст не виявлено.*", parse_mode='Markdown')
            return

        sent_msg = await send_smart_response(
            chat_id, 
            raw_text, 
            reply_markup=get_main_keyboard(), 
            caption_msg="✅ *Текст розпізнано!* Оберіть дію:"
        )
        save_to_cache(chat_id, sent_msg.message_id, raw_text)

    except Exception as e:
        logger.error(f"Error: {e}")
        await bot.send_message(chat_id, "❌ *Помилка.*", parse_mode='Markdown')

async def process_photo_direct(update: Update):
    """Сценарій А: Фото З підписом -> Пряма відповідь"""
    chat_id = update.effective_chat.id
    user_prompt = update.message.caption
    
    status_msg = await bot.send_message(chat_id, f"🧠 *Виконую запит:* _{user_prompt}_...", parse_mode='Markdown')
    
    try:
        # 1. OCR
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytearray()
        raw_text = await real_vision_api(bytes(image_bytes))
        
        if not raw_text:
            await bot.delete_message(chat_id, status_msg.message_id)
            await bot.send_message(chat_id, "⚠️ *Текст не виявлено.*", parse_mode='Markdown')
            return

        # 2. AI з кастомним промптом
        result_text = await real_gemini_api(raw_text, user_prompt)
        await bot.delete_message(chat_id, status_msg.message_id)
        
        # 3. Надсилаємо результат + КЛАВІАТУРУ ДІЙ
        sent_msg = await send_smart_response(
            chat_id,
            result_text,
            reply_markup=get_direct_response_keyboard(), # Додано кнопки "Меню" і "Нове фото"
            caption_msg="✅ *Відповідь на ваш запит:*"
        )
        
        # 4. Кешуємо текст, щоб кнопка "Всі дії" спрацювала
        save_to_cache(chat_id, sent_msg.message_id, raw_text)
        
    except Exception as e:
        logger.error(f"Direct Mode Error: {e}")
        await bot.send_message(chat_id, "❌ *Помилка при обробці запиту.*", parse_mode='Markdown')

async def process_callback(update: Update):
    query = update.callback_query
    command = query.data
    chat_id = query.message.chat_id
    message_id = query.message.message_id
    
    await query.answer()
    
    if command == "new_scan":
        await query.delete_message()
        await bot.send_message(chat_id, "🗑️ *Очищено.* Чекаю нове фото!", parse_mode='Markdown')
        return

    original_text = get_from_cache(chat_id, message_id)
    if not original_text:
        await query.edit_message_text("⚠️ *Сесія застаріла.* Надішліть фото знову.", parse_mode='Markdown')
        return

    # Логіка повернення до меню (працює і для "back_to_menu", і для "Всі дії")
    if command == "back_to_menu":
        if len(original_text) > MAX_MESSAGE_LENGTH:
            await query.edit_message_text(
                "📄 *Оригінальний текст (у файлі вище)*\n\nОберіть дію:", 
                reply_markup=get_main_keyboard(), 
                parse_mode='Markdown'
            )
        else:
            await safe_edit_message(
                query,
                f"📄 *Оригінальний текст:*\n\n`{original_text}`", 
                get_main_keyboard()
            )
        return

    await query.edit_message_text(f"🧠 *Gemini працює...*", parse_mode='Markdown')
    result_text = await real_gemini_api(original_text, command)
    
    if len(result_text) > MAX_MESSAGE_LENGTH:
        file_obj = io.BytesIO(result_text.encode('utf-8'))
        file_obj.name = f"{command}_result.txt"
        await bot.send_document(chat_id, file_obj, caption="🧠 *Результат (у файлі):*", parse_mode='Markdown')
        
        await query.edit_message_text(
            "✅ *Готово!* Результат у файлі.\nЩе дії?", 
            reply_markup=get_back_keyboard(),
            parse_mode='Markdown'
        )
    else:
        await safe_edit_message(
            query,
            result_text,
            get_back_keyboard()
        )

async def main_logic(update: Update):
    if update.message:
        text = update.message.text
        if text and text.startswith('/start'):
            await start_command(update)
        elif text and text.startswith('/clear'):
            await clear_command(update)
        elif update.message.photo:
            if update.message.caption:
                await process_photo_direct(update)
            else:
                await process_photo_interactive(update)
        else:
            await bot.send_message(update.effective_chat.id, "⚠️ Надішліть фото.", parse_mode='Markdown')
    elif update.callback_query:
        await process_callback(update)

# --- ENTRY POINT ---
@functions_framework.http
def telegram_webhook(request):
    if request.method != "POST": return "OK", 200
    try:
        if bot is None: return "Bot Error", 500
        update = Update.de_json(request.get_json(force=True), bot)
        asyncio.run(main_logic(update))
        return "OK", 200
    except Exception: return "Error", 500

# --- LOCAL RUN ---
if __name__ == "__main__":
    if not TELEGRAM_TOKEN: exit(1)
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    async def h(u, c): await main_logic(u)
    app.add_handler(MessageHandler(filters.ALL, h))
    app.add_handler(CallbackQueryHandler(h))
    app.run_polling()