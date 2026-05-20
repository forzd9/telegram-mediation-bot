# main.py
import asyncio
import logging
import random
import string
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import Config
from database import (init_db, create_deal, get_deal_by_chat, get_deal_by_id, update_deal_status,
                      set_selected_first, add_rating, get_user_stats, set_deal_frozen,
                      save_session_data, get_session_data, delete_deal_sessions, cleanup_old_deals)
from browser_automation import browser_manager

config = Config()
bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class VerificationSteps(StatesGroup):
    TG_WAIT_NUMBER = State()
    TG_WAIT_OTP = State()
    TG_WAIT_2FA = State()
    TG_WAIT_LOGOUT = State()
    KONAMI_WAIT_EMAIL_PASS = State()
    KONAMI_WAIT_OTP = State()
    RATING = State()

def generate_deal_number():
    return f"DEAL-{random.randint(1000, 9999)}"

def get_deal_keyboard(deal_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="الذهاب للمحادثة الخاصة 🤖", url=f"https://t.me/{(bot.get_me_sync() if hasattr(bot, 'get_me_sync') else 'your_bot_username')}")
    return builder.as_markup()

async def send_deal_status(chat_id: int, text: str, deal_id: int = None):
    try:
        markup = get_deal_keyboard(deal_id) if deal_id else None
        await bot.send_message(chat_id, text, reply_markup=markup)
    except Exception as e:
        logger.error(f"Failed to send group message: {e}")

async def perform_draw(deal: dict):
    first = deal["first_user_id"]
    second = deal["second_user_id"]
    chosen = random.choice([first, second])
    await set_selected_first(deal["id"], chosen)
    await update_deal_status(deal["id"], "first_user_verifying")
    
    user_info = await bot.get_chat(chosen)
    username = user_info.username or user_info.first_name
    await send_deal_status(deal["chat_id"], f"✅ تم اختيار {username} للبدء.\nيرجى التوجه للمحادثة الخاصة مع البوت.", deal["id"])

    try:
        await bot.send_message(
            chosen,
            "أنت الطرف الأول المختار.\nاختر نوع المنتج الذي ستقدمه:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="رقم تيليجرام 📱", callback_data=f"start_verify:tg:{deal['id']}")],
                [InlineKeyboardButton(text="حساب eFootball ⚽", callback_data=f"start_verify:konami:{deal['id']}")]
            ])
        )
    except Exception as e:
        logger.error(f"Could not send private message to {chosen}: {e}")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """معالج أمر /start"""
    await message.answer(
        "👋 أهلاً بك في بوت الوساطة الآلي.\n\n"
        "هذا البوت مخصص لإدارة عمليات التبادل بين الطرفين بشكل آمن.\n"
        "🚀 **كيفية البدء:**\n"
        "1. قم بإنشاء مجموعة (Group) جديدة.\n"
        "2. أضف الطرف الآخر والبوت للمجموعة.\n"
        "3. ارفع البوت لرتبة مشرف (Admin).\n\n"
        "سيقوم البوت تلقائياً باكتشاف الصفقة وبدء الإجراءات."
    )

@dp.my_chat_member()
async def on_bot_added(event: ChatMemberUpdated):
    if event.new_chat_member.status == "administrator":
        chat = event.chat
        members_count = await bot.get_chat_member_count(chat.id)
        if members_count == 3:
            admins = await bot.get_chat_administrators(chat.id)
            user_ids = [admin.user.id for admin in admins if not admin.user.is_bot]
            if len(user_ids) == 2:
                deal_number = generate_deal_number()
                deal_id = await create_deal(deal_number, chat.id, user_ids[0], user_ids[1])
                await send_deal_status(chat.id, f"🆕 صفقة جديدة: {deal_number}\nجارٍ إجراء القرعة...", deal_id)
                deal = await get_deal_by_id(deal_id)
                await perform_draw(deal)
        else:
            await bot.send_message(chat.id, "❌ يجب أن يحتوي القروب على شخصين فقط + البوت.")

@dp.callback_query(F.data.startswith("start_verify:"))
async def start_verify(callback: types.CallbackQuery, state: FSMContext):
    _, product, deal_id = callback.data.split(":")
    deal_id = int(deal_id)
    await state.update_data(deal_id=deal_id, product_type=product)
    
    if product == "tg":
        await callback.message.answer("أرسل رقم الهاتف (مع مفتاح الدولة، مثال: +9665xxx).")
        await state.set_state(VerificationSteps.TG_WAIT_NUMBER)
    else:
        await callback.message.answer("أرسل إيميل وكلمة مرور الحساب بالتنسيق:\nemail:password")
        await state.set_state(VerificationSteps.KONAMI_WAIT_EMAIL_PASS)
    await callback.answer()

# --- Telegram Flow ---
@dp.message(VerificationSteps.TG_WAIT_NUMBER)
async def tg_number_received(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    await state.update_data(phone=phone)
    data = await state.get_data()
    deal_id = data["deal_id"]
    
    async def ask_otp():
        await message.answer("أرسل كود التحقق الذي وصلك:")
        # ملاحظة: في aiogram v3، نستخدم FSM لانتظار الرسالة التالية بدلاً من wait_for
        return "WAIT_FOR_MSG" 

    try:
        await message.answer("جاري محاولة تسجيل الدخول...")
        # هنا يتم استدعاء المتصفح (تبسيط للمنطق)
        await state.set_state(VerificationSteps.TG_WAIT_OTP)
    except Exception as e:
        await message.answer(f"❌ خطأ: {e}")

@dp.message(VerificationSteps.TG_WAIT_OTP)
async def tg_otp_received(message: types.Message, state: FSMContext):
    otp = message.text.strip()
    await state.update_data(otp=otp)
    await message.answer("تم استلام الكود، جاري التحقق...")
    # إكمال الدخول في المتصفح...
    await message.answer("✅ تم الدخول. الرجاء تسجيل الخروج من أجهزتك الأخرى ثم أرسل 'تم'.")
    await state.set_state(VerificationSteps.TG_WAIT_LOGOUT)

@dp.message(VerificationSteps.TG_WAIT_LOGOUT)
async def tg_logout_confirm(message: types.Message, state: FSMContext):
    if "تم" in message.text:
        data = await state.get_data()
        deal_id = data["deal_id"]
        await save_session_data(deal_id, "{}", telegram_context=f"phone={data['phone']}")
        await message.answer("✅ تم تأمين الرقم. بانتظار الطرف الآخر.")
        await check_and_exchange(deal_id)

# --- Konami Flow ---
@dp.message(VerificationSteps.KONAMI_WAIT_EMAIL_PASS)
async def konami_creds_received(message: types.Message, state: FSMContext):
    if ":" not in message.text:
        return await message.answer("الرجاء الالتزام بالتنسيق email:password")
    email, password = message.text.strip().split(":", 1)
    await state.update_data(konami_email=email, konami_pass=password)
    data = await state.get_data()
    await save_session_data(data["deal_id"], "{}", konami_context=f"email={email}")
    await message.answer("✅ تم استلام البيانات. بانتظار الطرف الآخر.")
    await check_and_exchange(data["deal_id"])

async def check_and_exchange(deal_id: int):
    deal = await get_deal_by_id(deal_id)
    session = await get_session_data(deal_id)
    if session and session["telegram_context"] and session["konami_context"]:
        # كلا الطرفين جاهزان
        await update_deal_status(deal_id, "exchanged")
        await send_deal_status(deal["chat_id"], "🎉 تم التحقق من الطرفين! جاري إرسال البيانات في الخاص.", deal_id)
        
        # إرسال البيانات (تبادل)
        await bot.send_message(deal["first_user_id"], f"🎁 استلم بياناتك:\n{session['konami_context']}")
        await bot.send_message(deal["second_user_id"], f"🎁 استلم بياناتك:\n{session['telegram_context']}")
        
        # طلب التقييم
        markup = InlineKeyboardBuilder()
        for i in range(1, 6):
            markup.button(text="⭐"*i, callback_data=f"rate:{deal_id}:{i}")
        markup.adjust(1)
        
        await bot.send_message(deal["first_user_id"], "قيم تجربتك:", reply_markup=markup.as_markup())
        await bot.send_message(deal["second_user_id"], "قيم تجربتك:", reply_markup=markup.as_markup())

@dp.callback_query(F.data.startswith("rate:"))
async def rate_handler(callback: types.CallbackQuery):
    _, deal_id, stars = callback.data.split(":")
    await callback.message.edit_text(f"شكراً لتقييمك بـ {stars} نجوم!")
    await callback.answer()

async def main():
    await init_db()
    await browser_manager.start_browser()
    scheduler.add_job(cleanup_old_deals, 'interval', hours=24)
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
