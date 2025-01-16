import logging
import sqlite3
import asyncio
import time
import random
from fuzzywuzzy import process

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    ConversationHandler,
    filters
)
from googlesearch import search

from Config import TOKEN


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

REQUEST_DELAY_BASE = 2.0
REQUEST_DELAY_FACTOR = 1.5
MAX_RETRIES = 5
RETRY_DELAY_BASE = 5
SEARCH_CACHE = {}

(
    WAITING_FOR_NAME,
    WAITING_FOR_GENRE,
    WAITING_FOR_STEAM,
    WAITING_FOR_GOG,
    WAITING_FOR_EPIC,
) = map(chr, range(5))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отправляет приветственное сообщение и список жанров."""
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("Начать заново")], [KeyboardButton("Добавить игру")]], resize_keyboard=True
    )
    await update.message.reply_text(
        "Привет! Выбери жанр, чтобы посмотреть игры, или напиши название игры:", reply_markup=keyboard
    )
    await update.message.reply_text("Доступные жанры:", reply_markup=await get_genre_keyboard(0))


async def get_genre_keyboard(page: int) -> InlineKeyboardMarkup:
    """Получает список уникальных жанров из базы данных и формирует клавиатуру."""
    conn = sqlite3.connect('games.db')
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT genre FROM games")
    genres = cursor.fetchall()
    conn.close()

    genres_per_page = 4
    start_index = page * genres_per_page
    end_index = start_index + genres_per_page
    current_genres = genres[start_index:end_index]

    keyboard = []
    for genre in current_genres:
        keyboard.append([InlineKeyboardButton(genre[0], callback_data=f"genre_{genre[0]}_page_0")])

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"genres_page_{page - 1}"))
    if end_index < len(genres):
        buttons.append(InlineKeyboardButton("➡️ Вперед", callback_data=f"genres_page_{page + 1}"))
    if buttons:
        keyboard.append(buttons)

    return InlineKeyboardMarkup(keyboard)


async def show_genres_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает перелистывание страниц жанров"""
    query = update.callback_query
    await query.answer()

    page = int(query.data.split("_")[-1])
    await query.message.edit_reply_markup(reply_markup=await get_genre_keyboard(page))


async def show_games_by_genre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выводит список игр выбранного жанра."""
    query = update.callback_query
    await query.answer()

    data = query.data.split("_")
    genre = data[1]
    page = int(data[-1]) if len(data) > 3 and data[-2] == "page" else 0

    keyboard = await get_games_keyboard(genre, page)
    if keyboard:
        await query.message.reply_text(f"Выбери игру жанра <b>{genre}</b>:", reply_markup=keyboard, parse_mode="HTML")
    else:
        await query.message.reply_text("Игры в этом жанре не найдены.")


async def get_games_keyboard(genre: str, page: int) -> InlineKeyboardMarkup:
    """Получает список игр из базы данных и формирует клавиатуру."""
    conn = sqlite3.connect('games.db')
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM games WHERE genre=?", (genre,))
    games = cursor.fetchall()
    conn.close()

    games_per_page = 5
    start_index = page * games_per_page
    end_index = start_index + games_per_page
    current_games = games[start_index:end_index]

    keyboard = []
    for game_id, game_name in current_games:
        keyboard.append([InlineKeyboardButton(game_name, callback_data=f"game_{game_id}")])

    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"genre_{genre}_page_{page - 1}"))
    if end_index < len(games):
        buttons.append(InlineKeyboardButton("➡️ Вперед", callback_data=f"genre_{genre}_page_{page + 1}"))
    if buttons:
        keyboard.append(buttons)

    return InlineKeyboardMarkup(keyboard)


async def show_game_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выводит ссылки на маркетплейсы для выбранной игры."""
    query = update.callback_query
    await query.answer()

    game_id = query.data.split("_")[1]

    conn = sqlite3.connect('games.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name, steam_link, gog_link, epic_link FROM games WHERE id=?", (game_id,))
    game = cursor.fetchone()
    conn.close()

    if game:
        name, steam_link, gog_link, epic_link = game
        message = f"Ссылки на покупку игры <b>{name}</b>:\n\n"
        stores = {
            'steam': [],
            'gog': [],
            'epic': []
        }

        if steam_link and 'steam' in steam_link.lower():
            stores['steam'].append(steam_link)
        if gog_link and 'gog' in gog_link.lower():
            stores['gog'].append(gog_link)
        if epic_link and ('epicgames' in epic_link.lower() or 'epic' in epic_link.lower()):
            stores['epic'].append(epic_link)

        if stores['steam']:
            message += "\n<b>Steam:</b>\n"
            for link in stores['steam']:
                message += f"<a href='{link}'>Открыть в Steam</a>\n"
        if stores['gog']:
            message += "\n<b>GOG:</b>\n"
            for link in stores['gog']:
                message += f"<a href='{link}'>Открыть в GOG</a>\n"
        if stores['epic']:
            message += "\n<b>Epic Games Store:</b>\n"
            for link in stores['epic']:
                message += f"<a href='{link}'>Открыть в Epic Games Store</a>\n"

        if not any(stores.values()):
            message = f"Ссылки на игру <b>{name}</b> не найдены.\n"

        await query.message.reply_text(message, parse_mode="HTML")
    else:
        await query.message.reply_text("Игра не найдена.")


async def handle_start_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает нажатие кнопки "Начать заново"."""
    await start(update, context)


async def add_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Позволяет администратору добавить игру в базу данных."""
    if update.message.from_user.id != 210705050:  # Замените на ID доверенных пользователей
        await update.message.reply_text("У вас нет прав для выполнения этого действия.")
        return ConversationHandler.END  # Stop the conversation

    await update.message.reply_text("Введите название игры:")
    return WAITING_FOR_NAME


async def add_game_handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает название игры, полученное от администратора."""
    game_name = update.message.text
    context.user_data['game_name'] = game_name
    await update.message.reply_text("Введите жанр игры:")
    return WAITING_FOR_GENRE


async def add_game_handle_genre(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает жанр игры, полученный от администратора."""
    game_genre = update.message.text
    context.user_data['game_genre'] = game_genre
    await update.message.reply_text("Введите ссылку на Steam (если есть, иначе введите - ):")
    return WAITING_FOR_STEAM


async def add_game_handle_steam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ссылку на Steam, полученную от администратора."""
    steam_link = update.message.text
    if steam_link == "-":
        steam_link = None
    context.user_data['steam_link'] = steam_link
    await update.message.reply_text("Введите ссылку на GOG (если есть, иначе введите - ):")
    return WAITING_FOR_GOG


async def add_game_handle_gog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ссылку на GOG, полученную от администратора."""
    gog_link = update.message.text
    if gog_link == "-":
        gog_link = None
    context.user_data['gog_link'] = gog_link
    await update.message.reply_text("Введите ссылку на Epic Games Store (если есть, иначе введите - ):")
    return WAITING_FOR_EPIC


async def add_game_handle_epic(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает ссылку на Epic Games Store, полученную от администратора."""
    epic_link = update.message.text
    if epic_link == "-":
        epic_link = None
    context.user_data['epic_link'] = epic_link

    conn = sqlite3.connect('games.db')
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO games (name, genre, steam_link, gog_link, epic_link) VALUES (?, ?, ?, ?, ?)",
                       (
                           context.user_data['game_name'], context.user_data['game_genre'],
                           context.user_data['steam_link'],
                           context.user_data['gog_link'], context.user_data['epic_link']))
        conn.commit()
        await update.message.reply_text("Игра добавлена в базу данных.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка добавления игры: {e}")
    finally:
        conn.close()
        context.user_data.clear()
    return ConversationHandler.END


async def search_page(query, page_num):
    """
    Implements pagination for your search.
    Crucially, you MUST adapt this function to the output format of your `google_search` library.
    """
    logging.info(f"Поисковый запрос: {query}, страница: {page_num}")
    try:
        results_generator = search(query, num_results=5)
        results_from_page = list(results_generator)
        if results_from_page:
            logging.info(f"Результаты поиска (страница {page_num}):")
            for i, result in enumerate(results_from_page):
                if isinstance(result, str):
                    logging.info(f"   {i}: str: {result}")
                else:
                    if hasattr(result, 'link') and hasattr(result, 'name'):
                        logging.info(f"   {i}: link: {result.link}, name: {result.name}")
                    else:
                        logging.info(f"  {i}: unknown: {result}")
        else:
            logging.info(f"Результаты поиска (страница {page_num}): нет результатов")
        return results_from_page

    except Exception as e:
        logging.error(f"Ошибка при пагинации: {e}")
        return []


async def search_page_multiple_words(query_words, page_num):
    """
    Implements pagination for your search, now with multiple words.
    """
    query = " ".join(query_words)
    logging.info(f"Поисковый запрос (несколько слов): {query}, страница: {page_num}")
    try:
        results_generator = search(query, num_results=5)
        results_from_page = list(results_generator)
        if results_from_page:
            logging.info(f"Результаты поиска (страница {page_num}):")
            for i, result in enumerate(results_from_page):
                if isinstance(result, str):
                    logging.info(f"   {i}: str: {result}")
                else:
                    if hasattr(result, 'link') and hasattr(result, 'name'):
                        logging.info(f"   {i}: link: {result.link}, name: {result.name}")
                    else:
                        logging.info(f"  {i}: unknown: {result}")
        else:
            logging.info(f"Результаты поиска (страница {page_num}): нет результатов")
        return results_from_page

    except Exception as e:
        logging.error(f"Ошибка при пагинации (несколько слов): {e}")
        return []


async def search_game(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ищет игру в интернете и выводит ссылки на магазины."""
    user_query = update.message.text
    if user_query == "Начать заново":
        await start(update, context)
        return

    if user_query in SEARCH_CACHE:
        await update.message.reply_text(SEARCH_CACHE[user_query], parse_mode='HTML')
        return

    current_delay = REQUEST_DELAY_BASE
    retries_left = MAX_RETRIES

    conn = sqlite3.connect('games.db')
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM games")
    game_names = [row[0] for row in cursor.fetchall()]
    conn.close()

    best_match, score = process.extractOne(user_query, game_names)
    if score < 50:  # Adjust threshold as needed
        await update.message.reply_text(
            f"Не найдено точных совпадений для '{user_query}'. Попробуйте ввести точное название.")
        return

    if best_match != user_query:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Искать '{user_query}'", callback_data=f"search_original_{user_query}"),
             InlineKeyboardButton(f"Искать '{best_match}'", callback_data=f"search_best_{best_match}")],
        ])
        await update.message.reply_text(
            f"Вы ввели '{user_query}', возможно вы имели ввиду '{best_match}'. Какой вариант использовать для поиска?",
            reply_markup=keyboard)
        return  # Stop processing here and wait for a callback
    else:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Любой", callback_data=f"store_filter_any_{user_query}"),
             InlineKeyboardButton("Steam", callback_data=f"store_filter_steam_{user_query}"),
             InlineKeyboardButton("GOG", callback_data=f"store_filter_gog_{user_query}"),
             InlineKeyboardButton("Epic", callback_data=f"store_filter_epic_{user_query}")],
        ])
        await update.message.reply_text(f"Вы ввели '{user_query}'. Выберите магазин или оставьте любой:",
                                        reply_markup=keyboard)
        return  # Stop processing here and wait for a callback


async def perform_search(update: Update, context: ContextTypes.DEFAULT_TYPE, game_name, store_filter=None) -> None:
    """Выполняет поиск игры в интернете."""
    current_delay = REQUEST_DELAY_BASE
    retries_left = MAX_RETRIES
    while retries_left > 0:
        try:
            await asyncio.sleep(current_delay)
            search_results = []
            displayed_links = 0
            stores = {'steam': [], 'gog': [], 'epic': []}

            query_words = game_name.split()  # Split the game name into words

            for page_num in range(0, 10):
                if displayed_links >= 5:
                    break
                page_results = await search_page_multiple_words(query_words, page_num)  # Use the multiple words search
                if page_results:
                    for result in page_results:
                        if displayed_links >= 5:
                            break
                        if isinstance(result, str):
                            if store_filter:
                                if store_filter == "steam" and 'steam' not in result.lower():
                                    continue
                                elif store_filter == "gog" and 'gog' not in result.lower():
                                    continue
                                elif store_filter == "epic" and (
                                        'epicgames' not in result.lower() and 'epic' not in result.lower()):
                                    continue

                            search_results.append(result)
                            link = result.lower()
                            if 'steam' in link and len(stores['steam']) < 5:
                                stores['steam'].append(result)
                            elif 'gog' in link and len(stores['gog']) < 5:
                                stores['gog'].append(result)
                            elif ('epicgames' in link or 'epic' in link) and len(stores['epic']) < 5:
                                stores['epic'].append(result)
                            displayed_links += 1
                else:
                    break

            if search_results:
                message = f"Ссылки на покупку игры <b>{game_name}</b>:\n\n"
                for i, result in enumerate(search_results):
                    if i >= 5:
                        break
                    if isinstance(result, str):
                        message += f"<a href='{result}'> {result}</a>\n"

                store_messages = {}
                if stores['steam']:
                    store_messages['steam'] = [f"<a href='{r}'> {r}</a>\n" for r in stores['steam'][:5]]
                if stores['gog']:
                    store_messages['gog'] = [f"<a href='{r}'> {r}</a>\n" for r in stores['gog'][:5]]
                if stores['epic']:
                    store_messages['epic'] = [f"<a href='{r}'> {r}</a>\n" for r in stores['epic'][:5]]

                for store, links in store_messages.items():
                    if links:
                        message += f"\n<b>{store}:</b>\n"
                        message += "".join(links)
                if message:
                    await update.callback_query.message.reply_text(message,
                                                                   parse_mode='HTML') if update.callback_query else await update.message.reply_text(
                        message, parse_mode='HTML')
                    return
            else:
                await update.callback_query.message.reply_text(
                    f"Не удалось найти ссылки на игру '{game_name}'.") if update.callback_query else await update.message.reply_text(
                    f"Не удалось найти ссылки на игру '{game_name}'.")
                return
        except Exception as e:
            if "429" in str(e):
                logging.warning(f"Ошибка 429. Увеличиваем задержку...")
                current_delay *= REQUEST_DELAY_FACTOR
                retries_left -= 1
                await asyncio.sleep(random.uniform(0, RETRY_DELAY_BASE))
            else:
                await update.callback_query.message.reply_text(
                    f"Произошла ошибка при поиске: {e}") if update.callback_query else await update.message.reply_text(
                    f"Произошла ошибка при поиске: {e}")
                return
    await update.callback_query.message.reply_text(
        f"Не удалось выполнить поиск после {MAX_RETRIES} попыток.") if update.callback_query else await update.message.reply_text(
        f"Не удалось выполнить поиск после {MAX_RETRIES} попыток.")


async def handle_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает callback запросы с выбором поискового запроса"""
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    search_type = data[1]
    game_name = "_".join(data[2:])

    if search_type == "original":
        await perform_search(update, context, game_name)
    elif search_type == "best":
        await perform_search(update, context, game_name)


async def handle_store_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает callback запросы с выбором магазина"""
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    store_filter = data[2] if data[2] != "any" else None
    game_name = "_".join(data[3:])
    await perform_search(update, context, game_name, store_filter)


def main() -> None:
    """Запуск бота."""
    application = Application.builder().token(TOKEN).build()

    # Create the conversation handler for adding games
    add_game_conv_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Text("Добавить игру"), add_game)],
        states={
            WAITING_FOR_NAME: [MessageHandler(filters.TEXT & (~filters.COMMAND), add_game_handle_name)],
            WAITING_FOR_GENRE: [MessageHandler(filters.TEXT & (~filters.COMMAND), add_game_handle_genre)],
            WAITING_FOR_STEAM: [MessageHandler(filters.TEXT & (~filters.COMMAND), add_game_handle_steam)],
            WAITING_FOR_GOG: [MessageHandler(filters.TEXT & (~filters.COMMAND), add_game_handle_gog)],
            WAITING_FOR_EPIC: [MessageHandler(filters.TEXT & (~filters.COMMAND), add_game_handle_epic)],
        },
        fallbacks=[],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(show_genres_page, pattern="^genres_page_"))
    application.add_handler(CallbackQueryHandler(show_games_by_genre, pattern="^genre_"))
    application.add_handler(CallbackQueryHandler(show_game_links, pattern="^game_"))
    application.add_handler(MessageHandler(filters.Text("Начать заново"), handle_start_button))
    application.add_handler(add_game_conv_handler)
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), search_game))
    application.add_handler(CallbackQueryHandler(handle_search_callback, pattern="^search_"))
    application.add_handler(CallbackQueryHandler(handle_store_filter_callback, pattern="^store_filter_"))

    application.run_polling()


if __name__ == "__main__":
    main()
