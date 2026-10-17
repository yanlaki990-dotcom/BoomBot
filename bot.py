"""BoomDLC Bot"""
import asyncio, logging, re
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, CallbackQuery, ReplyKeyboardMarkup
import config, db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("boomdlc-bot")
router = Router()

# ============ BAN MIDDLEWARE ============
class BanMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = event.from_user.id
        if user_id in config.ADMIN_IDS:
            return await handler(event, data)
        try:
            async with db.pool.acquire() as con:
                row = await con.fetchrow("SELECT tg_id FROM banned_users WHERE tg_id=$1", user_id)
                if row is not None:
                    return
        except Exception as e:
            log.error(f"ban check {user_id}: {e}")
        return await handler(event, data)

# ============ SUBSCRIPTION MIDDLEWARE ============
class SubscriptionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        bot: Bot = data["bot"]
        user_id = event.from_user.id
        if user_id in config.ADMIN_IDS:
            return await handler(event, data)
        is_sub = False
        try:
            m = await bot.get_chat_member(chat_id=config.CHANNEL_ID, user_id=user_id)
            if m.status in ['creator', 'administrator', 'member']:
                is_sub = True
        except Exception as e:
            log.error(f"sub {user_id}: {e}")
        if is_sub:
            return await handler(event, data)
        if isinstance(event, CallbackQuery) and event.data == "check_sub":
            await event.answer("❌ Ты ещё не подписан!", show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться", url=config.CHANNEL_URL)],
            [InlineKeyboardButton(text="✅ Я подписался", callback_data="check_sub")]
        ])
        if isinstance(event, Message):
            await event.answer("⚠️ <b>Подпишись на канал!</b>", reply_markup=kb)
        elif isinstance(event, CallbackQuery):
            await event.message.answer("⚠️ <b>Подпишись на канал!</b>", reply_markup=kb)
            await event.answer()
        return

# ============ KEYBOARDS ============
def main_menu():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="📥 Скачать клиент")],
        [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🛒 Купить")],
        [KeyboardButton(text="📢 Наш канал"), KeyboardButton(text="🟢 Поддержка")],
    ], resize_keyboard=True)

def loader_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Скачать клиент", url=config.LOADER_URL)]
    ])

def support_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Написать в поддержку", url=config.SUPPORT_URL)]
    ])

def channel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться", url=config.CHANNEL_URL)]
    ])

# ============ HELPERS ============
def fmt_dt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

def is_admin(tg_id: int) -> bool:
    return tg_id in config.ADMIN_IDS

def _mask_db_url(url: str) -> str:
    if not url:
        return "(пусто)"
    try:
        if "://" in url and "@" in url:
            proto, rest = url.split("://", 1)
            userpass, hostpart = rest.split("@", 1)
            if ":" in userpass:
                user, _ = userpass.split(":", 1)
                return f"{proto}://{user}:***@{hostpart}"
            return f"{proto}://{userpass}@{hostpart}"
        return url
    except Exception:
        return url

async def is_banned(tg_id: int) -> bool:
    try:
        async with db.pool.acquire() as con:
            row = await con.fetchrow("SELECT tg_id FROM banned_users WHERE tg_id=$1", tg_id)
            return row is not None
    except Exception:
        return False

async def sub_status_text(tg_id: int) -> str:
    sub = await db.get_active_subscription(tg_id)
    if sub is None:
        return "❌ Нет активной подписки"
    if sub["expires_at"] is None:
        return "♾ Подписка: <b>Навсегда</b>"
    left = sub["expires_at"] - datetime.now(timezone.utc)
    return f"✅ Активна до <b>{fmt_dt(sub['expires_at'])}</b>\n⏳ Осталось: <b>{left.days} д. {left.seconds//3600} ч.</b>"

# ============ CREATE VENDOR ACCOUNT ============
async def create_vendor_account(message: Message, username: str, password: str, plan: str, source: str, grant_sub: bool = True):
    tg = message.from_user.id
    log.info(f"[save] START tg={tg} user={username} pass_len={len(password)}")

    if await is_banned(tg):
        log.warning(f"[save] BANNED tg={tg} — отклонено")
        return False

    try:
        async with db.pool.acquire() as con:
            result = await con.execute(
                "INSERT INTO public.users (tg_id, username, vendor_username, password) "
                "VALUES ($1, NULL, $2, $3) "
                "ON CONFLICT (tg_id) DO UPDATE SET "
                "vendor_username = EXCLUDED.vendor_username, "
                "password = EXCLUDED.password",
                tg, username, password
            )
            log.info(f"[save] SQL result={result}")
            row = await con.fetchrow(
                "SELECT tg_id, vendor_username, password FROM public.users WHERE tg_id=$1",
                tg
            )
            log.info(f"[save] AFTER row={dict(row) if row else None}")
    except Exception as e:
        log.error(f"[save] FAILED tg={tg}: {e}")
        await message.answer(f"❌ Ошибка сохранения: {e}")
        return False

    if grant_sub:
        try:
            await db.grant_subscription(tg, plan, source=source)
            log.info(f"[save] subscription granted for tg={tg}")
        except Exception as e:
            log.error(f"[save] sub failed tg={tg}: {e}")

    await message.answer(
        f"✅ Аккаунт создан!\n\n"
        f"👤 Логин: <code>{username}</code>\n"
        f"🔑 Пароль: <code>{password}</code>\n\n"
        f"📥 Скачать клиент: {config.LOADER_URL}\n\n"
        f"Сохрани логин и пароль.",
        reply_markup=main_menu()
    )
    return True

# ============ ADMIN: BAN / UNBAN / BANNED ============
@router.message(Command("ban"))
async def cmd_ban(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("Пример: <code>/ban 123456789 Причина</code>")
        return
    target_id = int(parts[1])
    reason = parts[2] if len(parts) > 2 else "(без причины)"
    try:
        async with db.pool.acquire() as con:
            await con.execute(
                "INSERT INTO banned_users (tg_id, reason) VALUES ($1, $2) "
                "ON CONFLICT (tg_id) DO UPDATE SET reason = EXCLUDED.reason, banned_at = NOW()",
                target_id, reason
            )
            await con.execute("DELETE FROM subscriptions WHERE tg_id=$1", target_id)
            await con.execute("DELETE FROM pending_vendor WHERE tg_id=$1", target_id)
            await con.execute("DELETE FROM public.users WHERE tg_id=$1", target_id)
        await message.answer(f"✅ Забанен <code>{target_id}</code>\n📝 {reason}")
    except Exception as e:
        log.error(f"ban failed: {e}")
        await message.answer(f"❌ Ошибка: <code>{e}</code>")

@router.message(Command("unban"))
async def cmd_unban(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Пример: <code>/unban 123456789</code>")
        return
    target_id = int(parts[1])
    try:
        async with db.pool.acquire() as con:
            result = await con.execute("DELETE FROM banned_users WHERE tg_id=$1", target_id)
        await message.answer(f"✅ Разбанен <code>{target_id}</code> ({result})")
    except Exception as e:
        log.error(f"unban failed: {e}")
        await message.answer(f"❌ Ошибка: <code>{e}</code>")

@router.message(Command("banned"))
async def cmd_banned(message: Message):
    if not is_admin(message.from_user.id):
        return
    try:
        async with db.pool.acquire() as con:
            rows = await con.fetch(
                "SELECT tg_id, reason, banned_at FROM banned_users ORDER BY banned_at DESC LIMIT 30"
            )
        if not rows:
            await message.answer("📭 Чёрный список пуст")
            return
        lines = [f"<b>🚫 Чёрный список ({len(rows)}):</b>", ""]
        for r in rows:
            ts = r["banned_at"].strftime("%d.%m %H:%M") if r["banned_at"] else "?"
            lines.append(f"<code>{r['tg_id']}</code> — {r['reason']} <i>({ts})</i>")
        await message.answer("\n".join(lines))
    except Exception as e:
        log.error(f"banned list failed: {e}")
        await message.answer(f"❌ Ошибка: <code>{e}</code>")

@router.message(Command("dbcheck"))
async def cmd_dbcheck(message: Message):
    if not is_admin(message.from_user.id):
        return
    try:
        masked = _mask_db_url(config.DATABASE_URL)
        async with db.pool.acquire() as con:
            total = await con.fetchval("SELECT count(*) FROM public.users")
            rows = await con.fetch(
                "SELECT tg_id, username, vendor_username, password, first_seen "
                "FROM public.users ORDER BY first_seen DESC LIMIT 5"
            )
        lines = [
            f"<b>DB URL:</b> <code>{masked}</code>",
            f"<b>Users count:</b> {total}",
            "",
            "<b>Last 5:</b>",
        ]
        for i, r in enumerate(rows, 1):
            ts = r["first_seen"].strftime("%d.%m %H:%M") if r["first_seen"] else "?"
            lines.append(
                f"{i}. tg_id=<code>{r['tg_id']}</code>, "
                f"user=<code>{r['username']}</code>, "
                f"vuser=<code>{r['vendor_username']}</code>, "
                f"pass=<code>{r['password']}</code>, "
                f"seen={ts}"
            )
        await message.answer("\n".join(lines))
    except Exception as e:
        log.error(f"dbcheck failed: {e}")
        await message.answer(f"❌ dbcheck error: <code>{e}</code>")

# ============ USER HANDLERS ============
@router.callback_query(F.data == "check_sub")
async def check_sub_callback(call: CallbackQuery, state: FSMContext):
    try:
        await call.message.delete()
    except Exception as e:
        log.warning(f"Не удалось удалить сообщение: {e}")
    await call.message.answer("✅ Спасибо! Нажми /start")
    await call.answer()

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await db.upsert_user(message.from_user.id, message.from_user.username)
    user = await db.get_user(message.from_user.id)
    if user and user["first_seen"] and (datetime.now(timezone.utc) - user["first_seen"]).total_seconds() > 60:
        text = "С возвращением! Вы вошли в аккаунт."
    else:
        text = "👋 Добро пожаловать в <b>BoomDLC</b>!"
    await message.answer(text, reply_markup=main_menu())
    await message.answer("📥 <b>Скачать клиент:</b>", reply_markup=loader_keyboard())

@router.message(F.text == "👤 Профиль")
async def profile(message: Message):
    await db.upsert_user(message.from_user.id, message.from_user.username)
    user = await db.get_user(message.from_user.id)
    status = await sub_status_text(message.from_user.id)
    await message.answer(
        f"👤 <b>Профиль</b>\n\n"
        f"🆔 <code>{message.from_user.id}</code>\n"
        f"📅 {fmt_dt(user['first_seen'])}\n\n"
        f"{status}",
        reply_markup=main_menu()
    )

@router.message(F.text == "📥 Скачать клиент")
async def download_client(message: Message):
    await message.answer(
        "📥 <b>Скачать клиент BoomDLC:</b>\n\n"
        "Нажми кнопку ниже, чтобы скачать последнюю версию.",
        reply_markup=loader_keyboard()
    )

@router.message(F.text == "🛒 Купить")
async def buy(message: Message):
    await message.answer(
        "🛒 Для покупки подписки обратитесь в поддержку:",
        reply_markup=support_keyboard()
    )

@router.message(F.text == "📢 Наш канал")
async def channel(message: Message):
    await message.answer("📢 Канал:", reply_markup=channel_keyboard())

@router.message(F.text == "🟢 Поддержка")
async def support(message: Message):
    await message.answer("Вопросы:", reply_markup=support_keyboard())

# ============ ADMIN: GIVE / STATS / POST ============
@router.message(Command("give"))
async def give_sub(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[2] not in config.PLANS:
        await message.answer("Пример: <code>/give 123 30d</code>")
        return
    target_id = int(parts[1])
    plan = parts[2]
    await db.upsert_user(target_id, None)
    await db.grant_subscription(target_id, plan, source="manual")

    user = await db.get_user(target_id)
    vendor_username = user["vendor_username"] if user else None
    if not vendor_username:
        await db.set_pending(target_id, plan)
        try:
            await message.bot.send_message(
                target_id,
                f"🎁 Вам выдана подписка <b>{config.PLANS[plan][0]}</b>!\n\n"
                f"Отправь логин и пароль через пробел, чтобы создать аккаунт.\n"
                f"Пример: <code>mylogin mypassword</code>"
            )
        except Exception as e:
            log.warning(f"give notify failed: {e}")

    await message.answer(f"✅ Выдано <code>{target_id}</code> ({plan})")

@router.message(Command("stats"))
async def cmd_stats(message: Message):
    if not is_admin(message.from_user.id):
        return
    u, a, kf, ku = await db.stats()
    await message.answer(f"📊 Юзеров: {u}\nАктивных: {a}\nСвободно: {kf}\nЮзано: {ku}")

@router.message(Command("post"))
async def cmd_post(message: Message):
    if not is_admin(message.from_user.id):
        return
    text = message.text.replace("/post", "", 1).strip()
    if not text and message.reply_to_message:
        text = message.reply_to_message.html_text or message.reply_to_message.text or ""
    if not text:
        await message.answer("Пример: <code>/post текст</code>")
        return
    ids = await db.get_all_tg_ids()
    await message.answer(f"📤 {len(ids)}...")
    ok = fail = 0
    for uid in ids:
        try:
            await message.bot.send_message(uid, text)
            ok += 1
        except Exception:
            fail += 1
    await message.answer(f"✅ {ok} ок, {fail} ошибок")

# ============ PENDING VENDOR ============
@router.message(F.text)
async def process_pending_vendor(message: Message):
    if message.text.startswith("/") or message.text in (
        "👤 Профиль", "📥 Скачать клиент", "🛒 Купить", "📢 Наш канал", "🟢 Поддержка"
    ):
        return
    pend = await db.get_pending(message.from_user.id)
    if pend is None:
        return
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) != 2:
        await message.answer("❌ Введи логин и пароль через пробел.\nПример: <code>mylogin mypassword</code>")
        return
    username, password = parts[0].replace(" ", ""), parts[1].replace(" ", "")
    if not re.fullmatch(r"[A-Za-z0-9_.@+-]{3,64}", username):
        await message.answer("❌ Логин не подходит.")
        return
    if len(password) < 4:
        await message.answer("❌ Пароль мин. 4.")
        return
    ok = await create_vendor_account(message, username, password, pend["plan"], "manual", grant_sub=False)
    if ok:
        await db.clear_pending(message.from_user.id)

# ============ STARTUP ============
async def on_startup():
    await db.init()
    try:
        async with db.pool.acquire() as con:
            await con.execute("""
                CREATE TABLE IF NOT EXISTS banned_users (
                    tg_id BIGINT PRIMARY KEY,
                    reason TEXT,
                    banned_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
        log.info("banned_users table OK")
    except Exception as e:
        log.error(f"banned_users create failed: {e}")
    log.info("DB ok")

async def main():
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.message.middleware(BanMiddleware())
    dp.callback_query.middleware(BanMiddleware())
    dp.message.middleware(SubscriptionMiddleware())
    dp.callback_query.middleware(SubscriptionMiddleware())
    dp.include_router(router)
    dp.startup.register(on_startup)
    log.info("Bot starting...")
    await dp.start_polling(bot, drop_pending_updates=True)

if __name__ == "__main__":
    asyncio.run(main())
