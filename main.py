import asyncio
import logging
import os
import sys
import json
import html
import uuid
import random
import traceback
import re
from urllib.parse import quote, urlparse
from dotenv import load_dotenv
from functools import wraps

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, PreCheckoutQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiohttp import web
import asyncpg
import aiohttp

# ==========================================
# ⚙️ НАСТРОЙКИ
# ==========================================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

# 🔥 ГЛАВНОЕ ДЛЯ RENDER: Порт берется из системы
WEB_SERVER_PORT = int(os.getenv("PORT", 8080))

# Исправление ссылки (asyncpg не любит postgresql://, но Render дает именно её)
# Мы оставляем как есть, asyncpg умный, но если будут проблемы - раскомментируй строку ниже
CPA_CONFIG = {
    'aliexpress.ru': 'https://rzekl.com/g/YOUR_ALI_CODE/?ulp=',
    'aliexpress.com': 'https://rzekl.com/g/YOUR_ALI_CODE/?ulp=',
}
URL_REGEX = r'(https?://[^\s]+)'

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = PROJECT_ROOT
# Для Render используем временную папку, если вдруг что-то надо сохранить
UPLOAD_DIR = os.path.join('/tmp', 'images') 

if not BOT_TOKEN: sys.exit("❌ Ошибка: Нет BOT_TOKEN")
if not DATABASE_URL: sys.exit("❌ Ошибка: Нет DATABASE_URL")

os.makedirs(UPLOAD_DIR, exist_ok=True)
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, GET, OPTIONS, DELETE",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400"
}

# --- Вспомогательные функции ---
def process_wishlist_links(text):
    if not text: return ""
    def replace_match(match):
        original_url = match.group(0)
        return f'[{original_url}]({original_url})'
    return re.sub(URL_REGEX, replace_match, text)

async def get_user_display_name(bot_instance: Bot, user_id: int):
    try:
        chat = await bot_instance.get_chat(user_id)
        if chat.username: return f"@{chat.username}"
        elif chat.first_name: return chat.first_name
        else: return f"ID: {user_id}"
    except: return f"Участник (ID: {user_id})"

# ==========================================
# 🗄️ БАЗА ДАННЫХ
# ==========================================
INIT_SQL = """
CREATE TABLE IF NOT EXISTS public.known_group_chats (
    chat_id BIGINT PRIMARY KEY, title TEXT, last_active TIMESTAMP
);
CREATE TABLE IF NOT EXISTS public.collections (
    id SERIAL PRIMARY KEY, creator_id BIGINT, target_chat_id BIGINT, goal TEXT, description TEXT, image_url TEXT, amount INT, current_amount INT DEFAULT 0, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS public.contributions (
    id SERIAL PRIMARY KEY, collection_id INT NOT NULL REFERENCES public.collections(id), user_id BIGINT NOT NULL, amount INT NOT NULL, currency TEXT NOT NULL, telegram_payment_charge_id TEXT, created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS public.santa_games (
    id SERIAL PRIMARY KEY,
    creator_id BIGINT NOT NULL,
    title TEXT,
    status TEXT DEFAULT 'recruiting',
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS public.santa_participants (
    id SERIAL PRIMARY KEY,
    game_id INT NOT NULL REFERENCES public.santa_games(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    wishlist TEXT,
    target_user_id BIGINT,
    UNIQUE(game_id, user_id)
);
"""

async def create_db_pool():
    try:
        # Стандартное подключение для Render/CockroachDB
        pool = await asyncpg.create_pool(
            dsn=DATABASE_URL,
            min_size=1, 
            max_size=4, 
            server_settings={'multiple_active_portals_enabled': 'true'}
        )
        async with pool.acquire() as conn:
            await conn.execute(INIT_SQL)
            try: await conn.execute("ALTER TABLE public.collections ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'active';")
            except: pass
        logging.info("✅ Database pool created.")
        return pool
    except Exception as e:
        logging.critical(f"❌ DB Error: {e}")
        sys.exit(1)

# ==========================================
# 🧠 БИЗНЕС-ЛОГИКА (ВСЕ ФУНКЦИИ ВЕРНУЛ)
# ==========================================
async def get_common_chats(pool, bot_instance, user_id):
    async with pool.acquire() as conn:
        records = await conn.fetch("SELECT chat_id, title FROM public.known_group_chats")
    valid_chats = []
    for r in records:
        try:
            m = await bot_instance.get_chat_member(r['chat_id'], user_id)
            if m.status not in ['left', 'kicked', 'restricted']:
                valid_chats.append({"chat_id": str(r['chat_id']), "title": r['title']})
        except: continue
    return valid_chats

async def create_collection(pool, creator_id, target_chat_id, goal, amount):
    default_img = "https://cdn-icons-png.flaticon.com/512/9466/9466245.png"
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""INSERT INTO public.collections (creator_id, target_chat_id, goal, amount, image_url, description, status) VALUES ($1, $2, $3, $4, $5, $6, 'active') RETURNING id""", int(creator_id), int(target_chat_id), goal, int(amount), default_img, "Описание отсутствует")
    return str(row['id'])

async def update_collection_details(pool, coll_id, user_id, desc, img):
    async with pool.acquire() as conn:
        res = await conn.execute("""UPDATE public.collections SET description = $1, image_url = $2 WHERE id = $3 AND creator_id = $4""", desc, img, int(str(coll_id).strip()), int(str(user_id).strip()))
    return "UPDATE 1" in res

async def get_collection_by_id(pool, coll_id):
    try: c_id = int(str(coll_id).strip())
    except: return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM public.collections WHERE id = $1", c_id)
    if not row: return None
    percent = int((row['current_amount'] / row['amount']) * 100) if row['amount'] > 0 else 0
    return {"id": str(row['id']), "creator_id": str(row['creator_id']), "goal": row['goal'], "description": row.get('description', ''), "image_url": row.get('image_url', ''), "amount": row['amount'], "current": row['current_amount'], "status": row.get('status', 'active'), "percent": percent}

async def get_user_collections(pool, user_id):
    u_id = int(str(user_id).strip())
    def format_row(r):
        percent = int((r['current_amount'] / r['amount']) * 100) if r['amount'] > 0 else 0
        return {"id": str(r['id']), "goal": r['goal'], "amount": r['amount'], "current": r['current_amount'], "status": r.get('status', 'active'), "percent": percent}
    async with pool.acquire() as conn:
        created_records = await conn.fetch("SELECT * FROM public.collections WHERE creator_id = $1 ORDER BY created_at DESC", u_id)
        participated_records = await conn.fetch("""SELECT DISTINCT c.* FROM public.collections c JOIN public.contributions cb ON c.id = cb.collection_id WHERE cb.user_id = $1 AND c.creator_id != $1 ORDER BY c.created_at DESC""", u_id)
    return {"created": [format_row(r) for r in created_records], "participated": [format_row(r) for r in participated_records]}

async def delete_collection_safely(pool, coll_id, user_id):
    c_id = int(str(coll_id).strip()); u_id = int(str(user_id).strip())
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT creator_id, current_amount FROM public.collections WHERE id = $1", c_id)
        if not row: return "Not found"
        if str(row['creator_id']) != str(u_id): return "Not creator"
        if row['current_amount'] > 0: return "Cannot delete: money collected"
        await conn.execute("DELETE FROM public.contributions WHERE collection_id = $1", c_id)
        await conn.execute("DELETE FROM public.collections WHERE id = $1", c_id)
    return "OK"

# --- ЛОГИКА ТАЙНОГО САНТЫ ---
async def create_santa_game(pool, creator_id, title):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("INSERT INTO public.santa_games (creator_id, title, status) VALUES ($1, $2, 'recruiting') RETURNING id", int(creator_id), title)
        game_id = row['id']
        await conn.execute("INSERT INTO public.santa_participants (game_id, user_id) VALUES ($1, $2)", game_id, int(creator_id))
    return str(game_id)

async def join_santa_game(pool, game_id, user_id, wishlist):
    processed_wishlist = process_wishlist_links(wishlist)
    async with pool.acquire() as conn:
        game = await conn.fetchrow("SELECT status FROM public.santa_games WHERE id = $1", int(game_id))
        if not game or game['status'] != 'recruiting': return "Game not recruiting"
        await conn.execute("""INSERT INTO public.santa_participants (game_id, user_id, wishlist) VALUES ($1, $2, $3) ON CONFLICT (game_id, user_id) DO UPDATE SET wishlist = EXCLUDED.wishlist""", int(game_id), int(user_id), processed_wishlist)
    return "OK"

async def get_user_santa_state(pool, bot_instance, user_id):
    u_id = int(user_id)
    async with pool.acquire() as conn:
        rows = await conn.fetch("""SELECT p.*, g.title, g.status as game_status, g.creator_id FROM public.santa_participants p JOIN public.santa_games g ON p.game_id = g.id WHERE p.user_id = $1 AND g.status IN ('recruiting', 'active') ORDER BY g.created_at DESC""", u_id)
        bot_username = getattr(bot_instance, 'username', 'GiftFlowBot')
        games_list = []
        for row in rows:
            game_id = row['game_id']; is_creator = str(row['creator_id']) == str(user_id)
            game_data = {"game_id": str(game_id), "game_title": row['title'] or "Тайный Санта", "game_status": row['game_status'], "is_creator": is_creator, "my_wishlist": row['wishlist'] or "", "invite_link": f"https://t.me/{bot_username}/app?startapp=santa_{game_id}" if (is_creator and row['game_status'] == 'recruiting') else None}
            if row['game_status'] == 'recruiting':
                participants = await conn.fetch("SELECT user_id FROM public.santa_participants WHERE game_id = $1", game_id)
                game_data['participants_count'] = len(participants)
            if row['game_status'] == 'active' and row['target_user_id']:
                target_id = row['target_user_id']
                target_row = await conn.fetchrow("SELECT wishlist FROM public.santa_participants WHERE game_id = $1 AND user_id = $2", game_id, target_id)
                if target_row:
                    game_data["target_user_name"] = await get_user_display_name(bot_instance, target_id)
                    game_data["target_wishlist"] = target_row['wishlist'] or "Вишлист пуст"
            games_list.append(game_data)
        state_to_return = games_list[0] if games_list else None
    return state_to_return

async def start_santa_game_shuffle(pool, bot_instance: Bot, game_id, creator_id):
    pairs_to_notify = []; g_id = int(game_id); c_id = int(creator_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            game = await conn.fetchrow("SELECT creator_id, status, title FROM public.santa_games WHERE id = $1", g_id)
            if not game or game['status'] != 'recruiting': return "Игра не в статусе набора"
            if str(game['creator_id']) != str(c_id): return "Нет прав"
            participants = await conn.fetch("SELECT user_id FROM public.santa_participants WHERE game_id = $1", g_id)
            if len(participants) < 2: return "Слишком мало участников"
            user_ids = [p['user_id'] for p in participants]
            givers = user_ids[:]; receivers = user_ids[:]
            attempts = 0
            while attempts < 20:
                random.shuffle(receivers)
                if not any(g == r for g, r in zip(givers, receivers)): break
                attempts += 1
            if any(g == r for g, r in zip(givers, receivers)): return "Ошибка жеребьевки"
            for giver_id, receiver_id in zip(givers, receivers):
                await conn.execute("UPDATE public.santa_participants SET target_user_id = $1 WHERE game_id = $2 AND user_id = $3", receiver_id, g_id, giver_id)
                pairs_to_notify.append((giver_id, receiver_id))
            await conn.execute("UPDATE public.santa_games SET status = 'active' WHERE id = $1", g_id)
    
    game_title = game['title'] or "Тайный Санта"
    for giver_id, receiver_id in pairs_to_notify:
        try:
            receiver_name = await get_user_display_name(bot_instance, receiver_id)
            await bot_instance.send_message(giver_id, f"🎅 <b>Жеребьевка в игре «{html.escape(game_title)}» завершена!</b>\n\nТвой подопечный: <b>{receiver_name}</b> 🎁\n\nЗайди в приложение, чтобы увидеть его вишлист!")
            await asyncio.sleep(0.05)
        except: pass
    return "OK"

# ==========================================
# 🤖 AIOGRAM ХЕНДЛЕРЫ
# ==========================================
@dp.pre_checkout_query()
async def process_pre_checkout_query(q: PreCheckoutQuery): await bot.answer_pre_checkout_query(q.id, ok=True)

@dp.message(F.successful_payment)
async def process_successful_payment(msg: types.Message):
    pmnt = msg.successful_payment
    try:
        prefix, c_id_str = pmnt.invoice_payload.split('_'); collection_id = int(c_id_str)
        if prefix != 'collection': return
        async with bot.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("INSERT INTO public.contributions (collection_id, user_id, amount, currency, telegram_payment_charge_id) VALUES ($1, $2, $3, $4, $5)", collection_id, msg.from_user.id, pmnt.total_amount, pmnt.currency, pmnt.telegram_payment_charge_id)
                res = await conn.fetchrow("UPDATE public.collections SET current_amount = current_amount + $1 WHERE id = $2 RETURNING current_amount, amount, goal, target_chat_id, status", pmnt.total_amount, collection_id)
                if res['current_amount'] >= res['amount'] and res['status'] == 'active':
                    await conn.execute("UPDATE public.collections SET status = 'finished' WHERE id = $1", collection_id)
                    try: await bot.send_message(res['target_chat_id'], f"🎉 <b>СБОР ЗАВЕРШЕН!</b>\nЦель «{html.escape(res['goal'])}» достигнута! Собрано {res['current_amount']} ⭐", parse_mode="HTML")
                    except: pass
    except: pass

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def track_group_activity(msg: types.Message):
    if msg.chat and msg.chat.id and msg.chat.title and hasattr(bot, 'db_pool'):
        try:
            async with bot.db_pool.acquire() as conn:
                await conn.execute("""INSERT INTO public.known_group_chats (chat_id, title, last_active) VALUES ($1, $2, NOW()) ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title, last_active = NOW()""", msg.chat.id, msg.chat.title)
        except: pass

# ==========================================
# 🌐 ВЕБ-СЕРВЕР (API)
# ==========================================
def api_handler_wrapper(handler):
    @wraps(handler)
    async def wrapped(request):
        try:
            return await handler(request)
        except Exception as e:
            logging.error(f"API Error: {e}")
            return web.json_response({"status": "error", "error": str(e)}, status=500, headers=CORS_HEADERS)
    return wrapped

async def handle_options(request): return web.Response(headers=CORS_HEADERS)

async def parse_body(request):
    data = await request.json()
    return data, data.get('chat_id')

@api_handler_wrapper
async def api_get_chats(request):
    _, uid = await parse_body(request)
    chats = await get_common_chats(bot.db_pool, bot, uid)
    return web.json_response({"status": "ok", "chats": chats}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_get_my_collections(request):
    _, uid = await parse_body(request)
    data = await get_user_collections(bot.db_pool, uid)
    return web.json_response({"status": "ok", "data": data}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_get_collection_info(request):
    data, _ = await parse_body(request)
    c = await get_collection_by_id(bot.db_pool, data.get('collection_id'))
    return web.json_response({"status": "ok", "data": c}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_update_collection(request):
    data, uid = await parse_body(request)
    await update_collection_details(bot.db_pool, data.get('collection_id'), uid, data.get('description'), data.get('image_url'))
    return web.json_response({"status": "ok"}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_create_collection(request):
    data, uid = await parse_body(request)
    cid = await create_collection(bot.db_pool, uid, data.get('target_chat_id'), data.get('goal'), int(data.get('amount', 0)))
    if cid:
        try:
            me = await bot.get_me()
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💸 Внести вклад", url=f"https://t.me/{me.username}/app?startapp=donate_{cid}")]])
            await bot.send_message(data.get('target_chat_id'), f"🚀 <b>НОВЫЙ СБОР</b>\nЦель: {html.escape(data.get('goal'))}\nНужно: {int(data.get('amount', 0)):,} ⭐", reply_markup=kb, parse_mode="HTML")
        except: pass
        return web.json_response({"status": "ok", "collection_id": cid}, headers=CORS_HEADERS)
    return web.json_response({"error": "Failed"}, status=500, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_delete_collection(request):
    data, uid = await parse_body(request)
    res = await delete_collection_safely(bot.db_pool, data.get('collection_id'), uid)
    return web.json_response({"status": "ok"} if res == "OK" else {"error": res}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_create_invoice(request):
    data, _ = await parse_body(request)
    c = await get_collection_by_id(bot.db_pool, data.get('collection_id'))
    inv = await bot.create_invoice_link(title="Вклад", description=c['goal'], payload=f"collection_{c['id']}", currency="XTR", prices=[LabeledPrice(label="Вклад", amount=int(data.get('amount')))])
    return web.json_response({"status": "ok", "invoice_url": inv}, headers=CORS_HEADERS)

# --- API САНТА (ВЕРНУЛ ОБРАТНО!) ---
@api_handler_wrapper
async def api_santa_get_state(request):
    _, uid = await parse_body(request)
    state = await get_user_santa_state(bot.db_pool, bot, uid)
    return web.json_response({"status": "ok", "state": state}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_santa_create(request):
    data, uid = await parse_body(request)
    gid = await create_santa_game(bot.db_pool, uid, data.get('title') or "Тайный Санта")
    return web.json_response({"status": "ok", "game_id": gid}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_santa_join(request):
    data, uid = await parse_body(request)
    await join_santa_game(bot.db_pool, data.get('game_id'), uid, data.get('wishlist'))
    return web.json_response({"status": "ok"}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_santa_start(request):
    data, uid = await parse_body(request)
    await start_santa_game_shuffle(bot.db_pool, bot, data.get('game_id'), uid)
    return web.json_response({"status": "ok"}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_santa_mark_sent(request):
    data, uid = await parse_body(request)
    await api_santa_mark_sent_logic(bot, bot.db_pool, uid, int(data.get('game_id')))
    return web.json_response({"status": "ok"}, headers=CORS_HEADERS)

@api_handler_wrapper
async def api_santa_mark_received(request):
    data, uid = await parse_body(request)
    await api_santa_mark_received_logic(bot, bot.db_pool, uid, int(data.get('game_id')))
    return web.json_response({"status": "ok"}, headers=CORS_HEADERS)

async def api_santa_mark_sent_logic(bot, pool, uid, gid):
    async with pool.acquire() as conn:
        p = await conn.fetchrow("SELECT target_user_id FROM public.santa_participants WHERE game_id=$1 AND user_id=$2", gid, uid)
        if p and p['target_user_id']:
            try: await bot.send_message(p['target_user_id'], "🎁 Санта отправил подарок!")
            except: pass

async def api_santa_mark_received_logic(bot, pool, uid, gid):
    async with pool.acquire() as conn:
        s = await conn.fetchrow("SELECT user_id FROM public.santa_participants WHERE game_id=$1 AND target_user_id=$2", gid, uid)
        if s:
            try: await bot.send_message(s['user_id'], "🎉 Подарок получен!")
            except: pass

# --- ЗАГРУЗКА (IMGBB) ---
@api_handler_wrapper
async def handle_upload(request):
    # 👇👇👇 ВСТАВЬ СВОЙ КЛЮЧ 👇👇👇
    IMGBB_KEY = "7c11778e00b562e2dfd3a7ec7efe0d3e"
    # 👆👆👆👆👆👆👆👆👆👆👆👆👆
    
    reader = await request.multipart()
    field = await reader.next()
    if field.name == 'image':
        data = await field.read()
        async with aiohttp.ClientSession() as sess:
            form = aiohttp.FormData()
            form.add_field('key', IMGBB_KEY)
            form.add_field('image', data)
            async with sess.post('https://api.imgbb.com/1/upload', data=form) as resp:
                res = await resp.json()
                if res.get('success'): return web.json_response({"status": "ok", "url": res['data']['url']}, headers=CORS_HEADERS)
    return web.json_response({"error": "Upload failed"}, status=500, headers=CORS_HEADERS)

async def serve_index(request): return web.FileResponse(os.path.join(BASE_DIR, 'index.html'), headers=CORS_HEADERS)
async def serve_script(request): return web.FileResponse(os.path.join(BASE_DIR, 'script.js'), headers=CORS_HEADERS)
async def serve_style(request): return web.FileResponse(os.path.join(BASE_DIR, 'style.css'), headers=CORS_HEADERS)

# --- ЗАПУСК ---
async def on_startup(app):
    global bot
    bot.db_pool = await create_db_pool()
    bot_info = await bot.get_me()
    bot.username = bot_info.username
    logging.info(f"🤖 Bot started: @{bot.username}")
    asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    logging.info(f"🚀 Web Server started on port {WEB_SERVER_PORT}")

async def on_shutdown(app):
    if bot:
        await bot.db_pool.close()
        await bot.session.close()

def main():
    app = web.Application()
    app.router.add_route('OPTIONS', '/api/{tail:.*}', handle_options)
    app.router.add_post('/api/chats', api_get_chats)
    app.router.add_post('/api/collections/my', api_get_my_collections)
    app.router.add_post('/api/collections/info', api_get_collection_info)
    app.router.add_post('/api/collections/update', api_update_collection)
    app.router.add_post('/api/collections/create', api_create_collection)
    app.router.add_post('/api/collections/delete', api_delete_collection)
    app.router.add_post('/api/collections/invoice', api_create_invoice)
    # САНТА РОУТЫ
    app.router.add_post('/api/santa/state', api_santa_get_state)
    app.router.add_post('/api/santa/create', api_santa_create)
    app.router.add_post('/api/santa/join', api_santa_join)
    app.router.add_post('/api/santa/start', api_santa_start)
    app.router.add_post('/api/santa/sent', api_santa_mark_sent)
    app.router.add_post('/api/santa/received', api_santa_mark_received)
    
    app.router.add_post('/api/upload', handle_upload)
    app.router.add_get('/', serve_index)
    app.router.add_get('/script.js', serve_script)
    app.router.add_get('/style.css', serve_style)
    
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, port=WEB_SERVER_PORT)

if __name__ == "__main__":
    main()
