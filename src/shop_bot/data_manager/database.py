import sqlite3
from datetime import datetime
import logging
from pathlib import Path
import json

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path('/app/project')
DB_FILE = PROJECT_ROOT / 'users.db'


def initialize_db():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute('CREATE TABLE IF NOT EXISTS users (telegram_id INTEGER PRIMARY KEY, username TEXT, total_spent REAL DEFAULT 0, total_months INTEGER DEFAULT 0, trial_used BOOLEAN DEFAULT 0, agreed_to_terms BOOLEAN DEFAULT 0, registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, is_banned BOOLEAN DEFAULT 0, referred_by INTEGER, referral_balance REAL DEFAULT 0, referral_balance_all REAL DEFAULT 0)')
            cursor.execute('CREATE TABLE IF NOT EXISTS vpn_keys (key_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, host_name TEXT NOT NULL, xui_client_uuid TEXT NOT NULL, key_email TEXT NOT NULL UNIQUE, expiry_date TIMESTAMP, created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, traffic_limit_gb INTEGER DEFAULT 0, traffic_limit_bytes INTEGER DEFAULT 0, manual_payment_status TEXT DEFAULT NULL)')
            cursor.execute('CREATE TABLE IF NOT EXISTS transactions (username TEXT, transaction_id INTEGER PRIMARY KEY AUTOINCREMENT, payment_id TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL, amount_rub REAL NOT NULL, amount_currency REAL, currency_name TEXT, payment_method TEXT, metadata TEXT, receipt_file_id TEXT, receipt_hash TEXT, admin_note TEXT, created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
            cursor.execute('CREATE TABLE IF NOT EXISTS bot_settings (key TEXT PRIMARY KEY, value TEXT)')
            cursor.execute('CREATE TABLE IF NOT EXISTS support_threads (user_id INTEGER PRIMARY KEY, thread_id INTEGER NOT NULL)')
            cursor.execute('CREATE TABLE IF NOT EXISTS xui_hosts (host_name TEXT NOT NULL, host_url TEXT NOT NULL, host_username TEXT NOT NULL, host_pass TEXT NOT NULL, host_inbound_id INTEGER NOT NULL)')
            cursor.execute('CREATE TABLE IF NOT EXISTS plans (plan_id INTEGER PRIMARY KEY AUTOINCREMENT, host_name TEXT NOT NULL, plan_name TEXT NOT NULL, months INTEGER NOT NULL, price REAL NOT NULL, traffic_gb INTEGER DEFAULT 0, price_toman INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1, FOREIGN KEY (host_name) REFERENCES xui_hosts (host_name))')
            default_settings = {'panel_login': 'admin','panel_password': 'admin','about_text': None,'terms_url': None,'privacy_url': None,'support_user': None,'support_text': None,'channel_url': None,'force_subscription': 'true','receipt_email': 'example@example.com','telegram_bot_token': None,'support_bot_token': None,'telegram_bot_username': None,'trial_enabled': 'true','trial_duration_days': '3','enable_referrals': 'true','referral_percentage': '10','referral_discount': '5','minimum_withdrawal': '100','support_group_id': None,'admin_telegram_id': None,'yookassa_shop_id': None,'yookassa_secret_key': None,'sbp_enabled': 'false','cryptobot_token': None,'heleket_merchant_id': None,'heleket_api_key': None,'domain': None,'ton_wallet_address': None,'tonapi_key': None,'card_to_card_enabled': 'true','card_number': '','card_holder_name': '','bank_name': '','display_currency': 'toman'}
            run_migration()
            for key, value in default_settings.items():
                cursor.execute('INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)', (key, value))
            conn.commit()
    except sqlite3.Error as e:
        logging.error(f'Database error on initialization: {e}')


def run_migration():
    if not DB_FILE.exists():
        return
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        def addcol(table, col, ddl):
            cursor.execute(f'PRAGMA table_info({table})')
            cols = {row[1] for row in cursor.fetchall()}
            if col not in cols:
                cursor.execute(ddl)
        addcol('users', 'referred_by', 'ALTER TABLE users ADD COLUMN referred_by INTEGER')
        addcol('users', 'referral_balance', 'ALTER TABLE users ADD COLUMN referral_balance REAL DEFAULT 0')
        addcol('users', 'referral_balance_all', 'ALTER TABLE users ADD COLUMN referral_balance_all REAL DEFAULT 0')
        addcol('plans', 'traffic_gb', 'ALTER TABLE plans ADD COLUMN traffic_gb INTEGER DEFAULT 0')
        addcol('plans', 'price_toman', 'ALTER TABLE plans ADD COLUMN price_toman INTEGER DEFAULT 0')
        addcol('plans', 'is_active', 'ALTER TABLE plans ADD COLUMN is_active INTEGER DEFAULT 1')
        addcol('vpn_keys', 'traffic_limit_gb', 'ALTER TABLE vpn_keys ADD COLUMN traffic_limit_gb INTEGER DEFAULT 0')
        addcol('vpn_keys', 'traffic_limit_bytes', 'ALTER TABLE vpn_keys ADD COLUMN traffic_limit_bytes INTEGER DEFAULT 0')
        addcol('vpn_keys', 'manual_payment_status', 'ALTER TABLE vpn_keys ADD COLUMN manual_payment_status TEXT DEFAULT NULL')
        addcol('transactions', 'receipt_file_id', 'ALTER TABLE transactions ADD COLUMN receipt_file_id TEXT')
        addcol('transactions', 'receipt_hash', 'ALTER TABLE transactions ADD COLUMN receipt_hash TEXT')
        addcol('transactions', 'admin_note', 'ALTER TABLE transactions ADD COLUMN admin_note TEXT')
        for key, value in {'card_to_card_enabled':'true','card_number':'','card_holder_name':'','bank_name':'','display_currency':'toman'}.items():
            cursor.execute('INSERT OR IGNORE INTO bot_settings (key, value) VALUES (?, ?)', (key, value))
        conn.commit()


def create_new_transactions_table(cursor: sqlite3.Cursor):
    cursor.execute('CREATE TABLE IF NOT EXISTS transactions (username TEXT, transaction_id INTEGER PRIMARY KEY AUTOINCREMENT, payment_id TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL, amount_rub REAL NOT NULL, amount_currency REAL, currency_name TEXT, payment_method TEXT, metadata TEXT, receipt_file_id TEXT, receipt_hash TEXT, admin_note TEXT, created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')


def create_host(name: str, url: str, user: str, passwd: str, inbound: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO xui_hosts (host_name, host_url, host_username, host_pass, host_inbound_id) VALUES (?, ?, ?, ?, ?)', (name, url, user, passwd, inbound))
        conn.commit()


def delete_host(host_name: str):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM plans WHERE host_name = ?', (host_name,))
        cursor.execute('DELETE FROM xui_hosts WHERE host_name = ?', (host_name,))
        conn.commit()


def get_host(host_name: str) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM xui_hosts WHERE host_name = ?', (host_name,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_all_hosts() -> list[dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM xui_hosts')
        return [dict(row) for row in cursor.fetchall()]


def get_all_keys() -> list[dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM vpn_keys')
        return [dict(row) for row in cursor.fetchall()]


def get_setting(key: str) -> str | None:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM bot_settings WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row[0] if row else None


def get_all_settings() -> dict:
    settings = {}
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT key, value FROM bot_settings')
        for row in cursor.fetchall():
            settings[row['key']] = row['value']
    return settings


def update_setting(key: str, value: str):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO bot_settings (key, value) VALUES (?, ?)', (key, value))
        conn.commit()


def create_plan(host_name: str, plan_name: str, months: int, price: float, traffic_gb: int = 0, price_toman: int | None = None):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO plans (host_name, plan_name, months, price, traffic_gb, price_toman, is_active) VALUES (?, ?, ?, ?, ?, ?, 1)', (host_name, plan_name, months, price, traffic_gb, price_toman if price_toman is not None else int(price)))
        conn.commit()


def get_plans_for_host(host_name: str) -> list[dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM plans WHERE host_name = ? ORDER BY months, traffic_gb', (host_name,))
        return [dict(plan) for plan in cursor.fetchall()]


def get_plan_by_id(plan_id: int) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM plans WHERE plan_id = ?', (plan_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def delete_plan(plan_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM plans WHERE plan_id = ?', (plan_id,))
        conn.commit()


def register_user_if_not_exists(telegram_id: int, username: str, referrer_id):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT telegram_id FROM users WHERE telegram_id = ?', (telegram_id,))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO users (telegram_id, username, registration_date, referred_by) VALUES (?, ?, ?, ?)', (telegram_id, username, datetime.now(), referrer_id))
        else:
            cursor.execute('UPDATE users SET username = ? WHERE telegram_id = ?', (username, telegram_id))
        conn.commit()


def add_to_referral_balance(user_id: int, amount: float):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET referral_balance = referral_balance + ? WHERE telegram_id = ?', (amount, user_id))
        conn.commit()


def set_referral_balance(user_id: int, value: float):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET referral_balance = ? WHERE telegram_id = ?', (value, user_id))
        conn.commit()


def set_referral_balance_all(user_id: int, value: float):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET referral_balance_all = ? WHERE telegram_id = ?', (value, user_id))
        conn.commit()


def get_referral_balance(user_id: int) -> float:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT referral_balance FROM users WHERE telegram_id = ?', (user_id,))
        row = cursor.fetchone()
        return row[0] if row else 0.0


def get_referral_count(user_id: int) -> int:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users WHERE referred_by = ?', (user_id,))
        return cursor.fetchone()[0] or 0


def log_transaction(username: str, transaction_id: str | None, payment_id: str | None, user_id: int, status: str, amount_rub: float, amount_currency: float | None, currency_name: str | None, payment_method: str, metadata: str):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO transactions (username, transaction_id, payment_id, user_id, status, amount_rub, amount_currency, currency_name, payment_method, metadata, created_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (username, transaction_id, payment_id, user_id, status, amount_rub, amount_currency, currency_name, payment_method, metadata, datetime.now()))
        conn.commit()


def create_pending_transaction(payment_id: str, user_id: int, amount_rub: float, metadata: dict) -> int:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO transactions (payment_id, user_id, status, amount_rub, metadata, payment_method) VALUES (?, ?, ?, ?, ?, ?)', (payment_id, user_id, 'pending', amount_rub, json.dumps(metadata), metadata.get('payment_method', 'unknown')))
        conn.commit()
        return cursor.lastrowid


def mark_transaction_receipt(payment_id: str, receipt_file_id: str, receipt_hash: str) -> bool:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE transactions SET receipt_file_id = ?, receipt_hash = ?, status = ? WHERE payment_id = ?', (receipt_file_id, receipt_hash, 'waiting_review', payment_id))
        conn.commit()
        return True


def approve_manual_transaction(payment_id: str, admin_note: str = 'approved') -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM transactions WHERE payment_id = ? AND status IN (?, ?)', (payment_id, 'pending', 'waiting_review'))
        row = cursor.fetchone()
        if not row:
            return None
        cursor.execute('UPDATE transactions SET status = ?, admin_note = ? WHERE payment_id = ?', ('paid', admin_note, payment_id))
        conn.commit()
        return dict(row)


def get_all_users() -> list[dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users ORDER BY registration_date DESC')
        return [dict(row) for row in cursor.fetchall()]


def get_user(telegram_id: int) -> dict | None:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_user_stats(telegram_id: int, amount_spent: float, months_purchased: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET total_spent = total_spent + ?, total_months = total_months + ? WHERE telegram_id = ?', (amount_spent, months_purchased, telegram_id))
        conn.commit()


def set_trial_used(telegram_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET trial_used = 1 WHERE telegram_id = ?', (telegram_id,))
        conn.commit()


def set_terms_agreed(telegram_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET agreed_to_terms = 1 WHERE telegram_id = ?', (telegram_id,))
        conn.commit()


def add_new_key(user_id: int, host_name: str, xui_client_uuid: str, key_email: str, expiry_timestamp_ms: int, traffic_limit_gb: int = 0):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        expiry_date = datetime.fromtimestamp(expiry_timestamp_ms / 1000)
        traffic_limit_bytes = int(traffic_limit_gb) * 1024 * 1024 * 1024
        cursor.execute('INSERT INTO vpn_keys (user_id, host_name, xui_client_uuid, key_email, expiry_date, traffic_limit_gb, traffic_limit_bytes) VALUES (?, ?, ?, ?, ?, ?, ?)', (user_id, host_name, xui_client_uuid, key_email, expiry_date, traffic_limit_gb, traffic_limit_bytes))
        new_key_id = cursor.lastrowid
        conn.commit()
        return new_key_id


def delete_key_by_email(email: str):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM vpn_keys WHERE key_email = ?', (email,))
        conn.commit()


def get_user_keys(user_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM vpn_keys WHERE user_id = ? ORDER BY key_id', (user_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_key_by_id(key_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM vpn_keys WHERE key_id = ?', (key_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_key_by_email(key_email: str):
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM vpn_keys WHERE key_email = ?', (key_email,))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_key_info(key_id: int, new_xui_uuid: str, new_expiry_ms: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        expiry_date = datetime.fromtimestamp(new_expiry_ms / 1000)
        cursor.execute('UPDATE vpn_keys SET xui_client_uuid = ?, expiry_date = ? WHERE key_id = ?', (new_xui_uuid, expiry_date, key_id))
        conn.commit()


def get_next_key_number(user_id: int) -> int:
    return len(get_user_keys(user_id)) + 1


def get_keys_for_host(host_name: str) -> list[dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM vpn_keys WHERE host_name = ?', (host_name,))
        return [dict(key) for key in cursor.fetchall()]


def get_all_vpn_users():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT user_id FROM vpn_keys')
        return [dict(row) for row in cursor.fetchall()]


def update_key_status_from_server(key_email: str, xui_client_data):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        if xui_client_data:
            expiry_date = datetime.fromtimestamp(xui_client_data.expiry_time / 1000)
            cursor.execute('UPDATE vpn_keys SET xui_client_uuid = ?, expiry_date = ? WHERE key_email = ?', (xui_client_data.id, expiry_date, key_email))
        else:
            cursor.execute('DELETE FROM vpn_keys WHERE key_email = ?', (key_email,))
        conn.commit()


def get_daily_stats_for_charts(days: int = 30) -> dict:
    stats = {'users': {}, 'keys': {}}
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT date(registration_date) as day, COUNT(*) FROM users WHERE registration_date >= date('now', ?) GROUP BY day ORDER BY day", (f'-{days} days',))
        for row in cursor.fetchall():
            stats['users'][row[0]] = row[1]
        cursor.execute("SELECT date(created_date) as day, COUNT(*) FROM vpn_keys WHERE created_date >= date('now', ?) GROUP BY day ORDER BY day", (f'-{days} days',))
        for row in cursor.fetchall():
            stats['keys'][row[0]] = row[1]
    return stats


def get_recent_transactions(limit: int = 15) -> list[dict]:
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT k.key_id, k.host_name, k.created_date, u.telegram_id, u.username FROM vpn_keys k JOIN users u ON k.user_id = u.telegram_id ORDER BY k.created_date DESC LIMIT ?', (limit,))
        return [dict(row) for row in cursor.fetchall()]


def add_support_thread(user_id: int, thread_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR REPLACE INTO support_threads (user_id, thread_id) VALUES (?, ?)', (user_id, thread_id))
        conn.commit()


def get_support_thread_id(user_id: int) -> int | None:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT thread_id FROM support_threads WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def get_user_id_by_thread(thread_id: int) -> int | None:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM support_threads WHERE thread_id = ?', (thread_id,))
        row = cursor.fetchone()
        return row[0] if row else None