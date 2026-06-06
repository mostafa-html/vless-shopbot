import logging
import uuid
import qrcode
import aiohttp
import re
import hashlib
import json
import base64
import asyncio
from io import BytesIO
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps

from yookassa import Payment
from aiosend import CryptoPay, TESTNET
from pytonconnect import TonConnect
from pytonconnect.exceptions import UserRejectsError
from aiogram import Bot, Router, F, types, html
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.types import BufferedInputFile
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatMemberStatus
from aiogram.utils.keyboard import InlineKeyboardBuilder

from shop_bot.bot import keyboards
from shop_bot.modules import xui_api
from shop_bot.data_manager.database import (
    get_user, add_new_key, get_user_keys, update_user_stats,
    register_user_if_not_exists, get_next_key_number, get_key_by_id,
    update_key_info, set_trial_used, set_terms_agreed, get_setting, get_all_hosts,
    get_plans_for_host, get_plan_by_id, log_transaction, get_referral_count,
    add_to_referral_balance, create_pending_transaction, get_all_users,
    set_referral_balance, set_referral_balance_all, mark_transaction_receipt,
    approve_manual_transaction
)
from shop_bot.config import (
    get_profile_text, get_vpn_active_text, VPN_INACTIVE_TEXT, VPN_NO_DATA_TEXT,
    get_key_info_text, CHOOSE_PAYMENT_METHOD_MESSAGE, get_purchase_success_text
)

TELEGRAM_BOT_USERNAME = None
PAYMENT_METHODS = {'card_to_card': True, 'cryptobot': True, 'tonconnect': True, 'yookassa': False, 'heleket': False}
ADMIN_ID = None
CRYPTO_BOT_TOKEN = get_setting('cryptobot_token')

logger = logging.getLogger(__name__)
admin_router = Router()
user_router = Router()

class KeyPurchase(StatesGroup):
    waiting_for_host_selection = State()
    waiting_for_plan_selection = State()

class Onboarding(StatesGroup):
    waiting_for_subscription_and_agreement = State()

class PaymentProcess(StatesGroup):
    waiting_for_email = State()
    waiting_for_payment_method = State()

class ManualCardPayment(StatesGroup):
    waiting_for_receipt = State()

class Broadcast(StatesGroup):
    waiting_for_message = State()
    waiting_for_button_option = State()
    waiting_for_button_text = State()
    waiting_for_button_url = State()
    waiting_for_confirmation = State()

class WithdrawStates(StatesGroup):
    waiting_for_details = State()


def is_valid_email(email: str) -> bool:
    return re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', email) is not None


async def show_main_menu(message: types.Message, edit_message: bool = False):
    user_id = message.chat.id
    user_db_data = get_user(user_id)
    user_keys = get_user_keys(user_id)
    trial_available = not (user_db_data and user_db_data.get('trial_used'))
    is_admin = str(user_id) == str(ADMIN_ID)
    text = '🏠 **Главное меню**\n\nВыберите действие:'
    keyboard = keyboards.create_main_menu_keyboard(user_keys, trial_available, is_admin)
    if edit_message:
        try:
            await message.edit_text(text, reply_markup=keyboard)
        except TelegramBadRequest:
            pass
    else:
        await message.answer(text, reply_markup=keyboard)


def registration_required(f):
    @wraps(f)
    async def decorated_function(event: types.Update, *args, **kwargs):
        user_id = event.from_user.id
        user_data = get_user(user_id)
        if user_data:
            return await f(*args, **kwargs)
        message_text = 'Пожалуйста, для начала работы со мной, отправьте команду /start'
        if isinstance(event, types.CallbackQuery):
            await event.answer(message_text, show_alert=True)
        else:
            await event.answer(message_text)
    return decorated_function


def build_manual_card_text(plan: dict, order_id: str) -> str:
    card_number = get_setting('card_number') or '----'
    card_holder_name = get_setting('card_holder_name') or '----'
    bank_name = get_setting('bank_name') or '----'
    amount = int(plan.get('price_toman') or plan.get('price') or 0)
    traffic_gb = int(plan.get('traffic_gb') or 0)
    months = int(plan.get('months') or 1)
    return (
        '💳 پرداخت کارت به کارت\n\n'
        f'مبلغ: {amount:,} تومان\n'
        f'پلن: {plan.get("plan_name")} | {traffic_gb}GB | {months} ماه\n'
        f'شماره کارت: `{card_number}`\n'
        f'نام دارنده: {card_holder_name}\n'
        f'بانک: {bank_name}\n'
        f'کد سفارش: `{order_id}`\n\n'
        'بعد از واریز، اسکرین‌شات رسید را همینجا ارسال کنید.'
    )


async def start_card_to_card_payment(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    plan_id = data.get('plan_id')
    plan = get_plan_by_id(plan_id)
    if not plan:
        await callback.message.edit_text('خطا در دریافت پلن.')
        await state.clear()
        return
    order_id = str(uuid.uuid4())[:8]
    metadata = {
        'user_id': callback.from_user.id,
        'action': data.get('action'),
        'key_id': data.get('key_id'),
        'host_name': data.get('host_name'),
        'plan_id': plan_id,
        'customer_email': data.get('customer_email'),
        'payment_method': 'CardToCard',
        'traffic_gb': int(plan.get('traffic_gb') or 0),
        'months': int(plan.get('months') or 1),
        'order_id': order_id,
    }
    create_pending_transaction(order_id, callback.from_user.id, float(plan.get('price_toman') or plan.get('price') or 0), metadata)
    await state.update_data(manual_order_id=order_id)
    await callback.message.edit_text(build_manual_card_text(plan, order_id), parse_mode='Markdown')
    await state.set_state(ManualCardPayment.waiting_for_receipt)


@user_router.message(ManualCardPayment.waiting_for_receipt)
@registration_required
async def handle_manual_receipt(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data.get('manual_order_id')
    if not order_id:
        await message.answer('کد سفارش پیدا نشد. دوباره تلاش کنید.')
        await state.clear()
        return
    if not message.photo:
        await message.answer('لطفاً تصویر رسید را ارسال کنید.')
        return
    largest = message.photo[-1]
    receipt_file_id = largest.file_id
    receipt_hash = hashlib.sha256(receipt_file_id.encode()).hexdigest()
    mark_transaction_receipt(order_id, receipt_file_id, receipt_hash)
    kb = InlineKeyboardBuilder()
    kb.button(text='✅ تایید پرداخت', callback_data=f'approve_manual_{order_id}')
    kb.button(text='❌ رد پرداخت', callback_data=f'reject_manual_{order_id}')
    kb.adjust(1)
    caption = (
        'رسید جدید کارت به کارت\n\n'
        f'User ID: {message.from_user.id}\n'
        f'Order ID: {order_id}\n'
        f'Receipt Hash: {receipt_hash}'
    )
    admin_id = int(get_setting('admin_telegram_id') or 0)
    if admin_id:
        await message.bot.send_photo(admin_id, receipt_file_id, caption=caption, reply_markup=kb.as_markup())
    await message.answer('رسید شما ثبت شد و پس از بررسی، اشتراک فعال می‌شود.')
    await state.clear()


@admin_router.callback_query(F.data.startswith('approve_manual_'))
async def approve_manual_payment(callback: types.CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_ID):
        await callback.answer('Access denied', show_alert=True)
        return
    order_id = callback.data.replace('approve_manual_', '', 1)
    tx = approve_manual_transaction(order_id, 'approved by admin')
    if not tx:
        await callback.answer('Transaction not found or already processed', show_alert=True)
        return
    try:
        metadata = json.loads(tx['metadata']) if tx.get('metadata') else {}
    except Exception:
        metadata = {}
    await callback.answer('Approved')
    await callback.message.edit_caption((callback.message.caption or '') + '\n\n✅ Approved')


@admin_router.callback_query(F.data.startswith('reject_manual_'))
async def reject_manual_payment(callback: types.CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_ID):
        await callback.answer('Access denied', show_alert=True)
        return
    await callback.answer('Rejected')
    await callback.message.edit_caption((callback.message.caption or '') + '\n\n❌ Rejected')


async def is_url_reachable(url: str) -> bool:
    pattern = re.compile(r'^https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(/?.*)?$')
    if not re.match(pattern, url):
        return False
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.head(url, allow_redirects=True) as response:
                return response.status < 400
    except Exception:
        return False


async def gettonconnectinstance(userid: int) -> TonConnect:
    return TonConnect('https://raw.githubusercontent.com/ton-blockchain/ton-connect/main/requests-responses-v2.json')


async def showpaymentoptionsmessage(message: types.Message, state: FSMContext):
    data = await state.get_data()
    user = get_user(message.chat.id)
    plan = get_plan_by_id(data.get('plan_id'))
    if not plan:
        await message.answer('خطا در دریافت پلن')
        await state.clear()
        return
    price = Decimal(str(plan.get('price_toman') or plan.get('price') or 0))
    finalprice = price
    if user and user.get('referred_by') and user.get('total_spent', 0) == 0:
        discount_percentage = Decimal(get_setting('referral_discount') or '0')
        if discount_percentage > 0:
            finalprice = (price - (price * discount_percentage / 100)).quantize(Decimal('1'))
    await state.update_data(final_price=float(finalprice))
    builder = InlineKeyboardBuilder()
    if PAYMENT_METHODS.get('card_to_card') and get_setting('card_to_card_enabled') == 'true':
        builder.button(text='💳 کارت به کارت', callback_data='pay_card_to_card')
    if PAYMENT_METHODS.get('cryptobot'):
        builder.button(text='🤖 CryptoBot', callback_data='pay_cryptobot')
    if PAYMENT_METHODS.get('tonconnect'):
        builder.button(text='🪙 TON Connect', callback_data='pay_tonconnect')
    builder.button(text='⬅️ بازگشت', callback_data='back_to_email_prompt')
    builder.adjust(1)
    await message.answer(CHOOSE_PAYMENT_METHOD_MESSAGE, reply_markup=builder.as_markup())
    await state.set_state(PaymentProcess.waiting_for_payment_method)


@user_router.callback_query(PaymentProcess.waiting_for_payment_method, F.data == 'pay_card_to_card')
async def pay_card_to_card(callback: types.CallbackQuery, state: FSMContext):
    await callback.answer()
    await start_card_to_card_payment(callback, state)


@user_router.message(F.text == '🏠 Главное меню')
@registration_required
async def main_menu_handler(message: types.Message):
    await show_main_menu(message)

# NOTE: keep the rest of your existing handlers unchanged and merge this file carefully.