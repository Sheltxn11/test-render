"""
Telegram Bot Module for Grocery Tracker
Handles all Telegram bot functionality including webhooks and commands.
"""

import logging
import requests
import os
from datetime import datetime, timedelta
from typing import Callable, Optional, Tuple
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration from environment
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID", "0"))
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "default_secret")

# Constants
DATE_FORMAT = "%Y-%m-%d"
TELEGRAM_API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


class TelegramBot:
    """Handles all Telegram bot operations."""
    
    def __init__(self, db_helpers: dict = None):
        """
        Initialize the bot with database helper functions.
        
        Args:
            db_helpers: Dictionary containing database helper functions:
                - get_collection_by_year
                - get_previous_month_balance
                - create_month_skeleton
                - recalculate_month_totals
                - get_month_name_from_date
        """
        self.db_helpers = db_helpers or {}
        self.last_update_id = 0
    
    # ==================== TELEGRAM API METHODS ====================
    
    def send_message(self, message: str, reply_to_message_id: int = None) -> bool:
        """Send a message to the configured chat."""
        if not CHAT_ID:
            logging.warning("Telegram Chat ID not configured")
            return False
        
        try:
            url = f"{TELEGRAM_API_BASE}/sendMessage"
            payload = {
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            }
            if reply_to_message_id:
                payload["reply_to_message_id"] = reply_to_message_id
            
            response = requests.post(url, json=payload, timeout=10)
            if response.ok:
                logging.info("Telegram message sent successfully")
                return True
            else:
                logging.error(f"Telegram API error: {response.text}")
                return False
        except Exception as e:
            logging.error(f"Error sending Telegram message: {e}")
            return False
    
    def get_updates(self, offset: int = None) -> dict:
        """Get updates from Telegram."""
        try:
            url = f"{TELEGRAM_API_BASE}/getUpdates"
            params = {}
            if offset:
                params["offset"] = offset
            params["timeout"] = 1
            
            response = requests.get(url, params=params, timeout=10)
            return response.json()
        except Exception as e:
            logging.error(f"Error getting updates: {e}")
            return {"ok": False, "error": str(e)}
    
    # ==================== WEBHOOK MANAGEMENT ====================
    
    def setup_webhook(self, webhook_url: str) -> dict:
        """Register webhook URL with Telegram."""
        try:
            url = f"{TELEGRAM_API_BASE}/setWebhook"
            payload = {
                "url": webhook_url,
                "secret_token": WEBHOOK_SECRET,
                "allowed_updates": ["message"]
            }
            
            response = requests.post(url, json=payload, timeout=10)
            data = response.json()
            
            if data.get("ok"):
                logging.info(f"Webhook set successfully: {webhook_url}")
                return {"success": True, "message": "Webhook registered successfully"}
            else:
                logging.error(f"Failed to set webhook: {data}")
                return {"success": False, "error": data.get("description", "Unknown error")}
        except Exception as e:
            logging.error(f"Error setting webhook: {e}")
            return {"success": False, "error": str(e)}
    
    def remove_webhook(self) -> dict:
        """Remove webhook from Telegram (switch back to polling)."""
        try:
            url = f"{TELEGRAM_API_BASE}/deleteWebhook"
            response = requests.post(url, timeout=10)
            data = response.json()
            
            if data.get("ok"):
                logging.info("Webhook removed successfully")
                return {"success": True, "message": "Webhook removed successfully"}
            else:
                return {"success": False, "error": data.get("description", "Unknown error")}
        except Exception as e:
            logging.error(f"Error removing webhook: {e}")
            return {"success": False, "error": str(e)}
    
    def get_webhook_info(self) -> dict:
        """Get current webhook configuration."""
        try:
            url = f"{TELEGRAM_API_BASE}/getWebhookInfo"
            response = requests.get(url, timeout=10)
            return response.json()
        except Exception as e:
            logging.error(f"Error getting webhook info: {e}")
            return {"ok": False, "error": str(e)}
    
    def verify_webhook_secret(self, secret_token: str) -> bool:
        """Verify the webhook secret token matches."""
        return secret_token == WEBHOOK_SECRET
    
    # ==================== DATE PARSING ====================
    
    def parse_flexible_date(self, date_str: str) -> Tuple[bool, str]:
        """
        Parse date from various formats.
        
        Returns:
            tuple: (success: bool, date_str in YYYY-MM-DD format or error message)
        """
        if not date_str:
            return (True, datetime.now().strftime(DATE_FORMAT))
        
        date_str = date_str.strip().lower()
        current_year = datetime.now().year
        
        if date_str == 'today':
            return (True, datetime.now().strftime(DATE_FORMAT))
        elif date_str == 'yesterday':
            yesterday = datetime.now() - timedelta(days=1)
            return (True, yesterday.strftime(DATE_FORMAT))
        
        formats_to_try = [
            ("%d/%m/%Y", None),
            ("%d-%m-%Y", None),
            ("%d.%m.%Y", None),
            ("%Y-%m-%d", None),
            ("%d %b %Y", None),
            ("%d %B %Y", None),
            ("%b %d %Y", None),
            ("%B %d %Y", None),
            ("%d/%m", current_year),
            ("%d-%m", current_year),
            ("%d %b", current_year),
            ("%d %B", current_year),
        ]
        
        for fmt, default_year in formats_to_try:
            try:
                parsed = datetime.strptime(date_str, fmt)
                if default_year:
                    parsed = parsed.replace(year=default_year)
                return (True, parsed.strftime(DATE_FORMAT))
            except ValueError:
                continue
        
        return (False, "Invalid date format")
    
    # ==================== FORMATTING HELPERS ====================
    
    @staticmethod
    def format_currency(amount: float) -> str:
        """Format amount as Indian Rupees."""
        return f"Rs. {amount:,.0f}"
    
    def get_last_transaction(self, month_doc: dict, txn_type: str) -> Optional[str]:
        """Get the last transaction of a specific type."""
        items = month_doc.get('daily_expenses' if txn_type == 'purchase' else 'credits', [])
        if not items:
            return None
        
        sorted_items = sorted(items, key=lambda x: x.get('date', ''), reverse=True)
        if sorted_items:
            item = sorted_items[0]
            item_date = item.get('date')
            if isinstance(item_date, datetime):
                display_date = item_date.strftime("%d %b")
            else:
                display_date = str(item_date)[:10] if item_date else 'Unknown'
            return f"{self.format_currency(item.get('amount', 0))} on {display_date}"
        return None
    
    # ==================== RESPONSE GENERATORS ====================
    
    def generate_transaction_response(self, txn_type: str, amount: float, date_str: str,
                                       month_doc: dict, month_name: str, year: int,
                                       username: str = None) -> str:
        """Generate response message after a transaction."""
        try:
            date_obj = datetime.strptime(date_str, DATE_FORMAT)
            display_date = date_obj.strftime("%d %b %Y")
        except:
            display_date = date_str
        
        current_due = month_doc.get('balance', 0)
        total_purchases = month_doc.get('total_expense', 0)
        total_payments = sum(c.get('amount', 0) for c in month_doc.get('credits', []))
        
        action = "Purchase" if txn_type == 'purchase' else "Payment"
        
        lines = [
            f"<b>{action} Recorded</b>",
            "",
            f"Amount: {self.format_currency(amount)}",
            f"Date: {display_date}",
        ]
        
        if username:
            lines.append(f"By: @{username}")
        
        lines.extend([
            "",
            "-" * 24,
            "",
            f"<b>Current Due: {self.format_currency(current_due)}</b>",
            "",
            f"{month_name} {year}:",
            f"  Total Spent: {self.format_currency(total_purchases)}",
            f"  Total Paid: {self.format_currency(total_payments)}",
        ])
        
        # Add context about last opposite transaction
        if txn_type == 'purchase':
            last_payment = self.get_last_transaction(month_doc, 'payment')
            if last_payment:
                lines.extend(["", f"Last payment: {last_payment}"])
        else:
            last_purchase = self.get_last_transaction(month_doc, 'purchase')
            if last_purchase:
                lines.extend(["", f"Last purchase: {last_purchase}"])
        
        return "\n".join(lines)
    
    def generate_due_summary(self) -> str:
        """Generate due summary message."""
        try:
            get_collection = self.db_helpers.get('get_collection_by_year')
            get_prev_balance = self.db_helpers.get('get_previous_month_balance')
            
            if not get_collection:
                return "Database not configured."
            
            year = datetime.now().year
            month_name = datetime.now().strftime("%B")
            collection_obj = get_collection(year)
            month_doc = collection_obj.find_one({"month": month_name})
            
            if not month_doc:
                return f"<b>Grocery Due Summary</b>\n\nNo transactions for {month_name} {year} yet."
            
            current_due = month_doc.get('balance', 0)
            total_purchases = month_doc.get('total_expense', 0)
            total_payments = sum(c.get('amount', 0) for c in month_doc.get('credits', []))
            prev_balance = get_prev_balance(month_name, year) if get_prev_balance else 0
            
            lines = [
                "<b>Grocery Due Summary</b>",
                "",
                f"<b>Current Due: {self.format_currency(current_due)}</b>",
                "",
            ]
            
            if prev_balance != 0:
                lines.append(f"Previous Balance: {self.format_currency(prev_balance)}")
                lines.append("")
            
            lines.extend([
                f"{month_name} {year}:",
                f"  Total Purchases: {self.format_currency(total_purchases)}",
                f"  Total Payments: {self.format_currency(total_payments)}",
            ])
            
            # Recent activity
            all_txns = []
            for expense in month_doc.get('daily_expenses', []):
                date = expense.get('date')
                date_str = date.strftime("%d %b") if isinstance(date, datetime) else str(date)[:10]
                all_txns.append(('Purchase', expense.get('amount', 0), date_str, date))
            
            for credit in month_doc.get('credits', []):
                date = credit.get('date')
                date_str = date.strftime("%d %b") if isinstance(date, datetime) else str(date)[:10]
                all_txns.append(('Payment', credit.get('amount', 0), date_str, date))
            
            if all_txns:
                all_txns.sort(key=lambda x: x[3] if x[3] else '', reverse=True)
                lines.extend(["", "Recent Activity:"])
                for txn in all_txns[:5]:
                    lines.append(f"  {txn[2]}: {txn[0]} {self.format_currency(txn[1])}")
            
            return "\n".join(lines)
            
        except Exception as e:
            logging.error(f"Error generating due summary: {e}")
            return "Error generating summary. Please try again."
    
    @staticmethod
    def get_command_help() -> str:
        """Return help message for bot commands."""
        return """<b>Grocery Tracker Commands</b>

<b>/purchase [amount] [date]</b>
Record a purchase
  /purchase 2500
  /purchase 1500 15/1/2026

<b>/payment [amount] [date]</b>
Record a payment
  /payment 3000
  /payment 2000 15 jan

<b>/due</b>
View current due summary

<b>Date Formats:</b>
  15/1/2026 or 15-1-2026
  15 jan or 15 january 2026
  today or yesterday
  (Default: today)"""
    
    # ==================== COMMAND PROCESSING ====================
    
    def process_command(self, text: str, username: str = None) -> Optional[str]:
        """Process a bot command and return response."""
        text = text.strip()
        parts = text.split()
        
        if not parts:
            return None
        
        command = parts[0].lower().split('@')[0]
        
        if command == '/due':
            return self.generate_due_summary()
        
        if command in ['/help', '/start']:
            return self.get_command_help()
        
        if command in ['/purchase', '/payment']:
            return self._process_transaction_command(command, parts, username)
        
        return None
    
    def _process_transaction_command(self, command: str, parts: list, username: str) -> str:
        """Process /purchase or /payment command."""
        if len(parts) < 2:
            return f"Missing amount.\n\nUsage: {command} [amount] [date]\nExample: {command} 2500"
        
        try:
            amount = float(parts[1].replace(',', ''))
            if amount <= 0:
                return "Amount must be greater than zero."
        except ValueError:
            return f"Invalid amount: {parts[1]}\n\nPlease enter a valid number.\nExample: {command} 2500"
        
        date_input = ' '.join(parts[2:]) if len(parts) > 2 else None
        success, date_result = self.parse_flexible_date(date_input)
        
        if not success:
            return f"Invalid date format: {date_input}\n\nAccepted formats:\n  15/1/2026 or 15-1-2026\n  15 jan or 15 january 2026\n  today or yesterday"
        
        date_str = date_result
        txn_type = 'purchase' if command == '/purchase' else 'payment'
        
        try:
            # Get database helpers
            get_collection = self.db_helpers.get('get_collection_by_year')
            get_prev_balance = self.db_helpers.get('get_previous_month_balance')
            create_skeleton = self.db_helpers.get('create_month_skeleton')
            recalculate = self.db_helpers.get('recalculate_month_totals')
            get_month_name = self.db_helpers.get('get_month_name_from_date')
            
            if not all([get_collection, get_prev_balance, create_skeleton, recalculate, get_month_name]):
                return "Database not configured properly."
            
            month_name = get_month_name(date_str)
            transaction_date = datetime.strptime(date_str, DATE_FORMAT)
            year = transaction_date.year
            
            collection_obj = get_collection(year)
            month_doc = collection_obj.find_one({"month": month_name})
            
            if month_doc is None:
                month_doc = create_skeleton(month_name, year)
                collection_obj.insert_one(month_doc)
            
            transaction_entry = {"date": transaction_date, "amount": amount}
            field_name = "daily_expenses" if txn_type == 'purchase' else "credits"
            
            collection_obj.update_one(
                {"month": month_name},
                {"$push": {field_name: transaction_entry}}
            )
            
            updated_doc = collection_obj.find_one({"month": month_name})
            previous_balance = get_prev_balance(month_name, year)
            updated_doc = recalculate(updated_doc, previous_balance)
            
            collection_obj.update_one(
                {"month": month_name},
                {"$set": {"total_expense": updated_doc['total_expense'], "balance": updated_doc['balance']}}
            )
            
            final_doc = collection_obj.find_one({"month": month_name})
            
            logging.info(f"Telegram: {txn_type} Rs.{amount} recorded for {date_str} by {username}")
            return self.generate_transaction_response(txn_type, amount, date_str, final_doc, month_name, year, username)
            
        except Exception as e:
            logging.error(f"Error processing transaction: {e}")
            return f"Error recording {txn_type}. Please try again."
    
    # ==================== WEBHOOK HANDLER ====================
    
    def handle_webhook(self, update: dict, secret_token: str = None) -> dict:
        """
        Handle incoming webhook from Telegram.
        
        Args:
            update: The update payload from Telegram
            secret_token: The secret token from request headers
            
        Returns:
            dict with status and any response sent
        """
        # Verify secret token if provided
        if secret_token and not self.verify_webhook_secret(secret_token):
            logging.warning("Invalid webhook secret token")
            return {"status": "unauthorized", "error": "Invalid secret token"}
        
        message = update.get('message', {})
        if not message:
            return {"status": "ok", "message": "No message in update"}
        
        chat = message.get('chat', {})
        chat_id = chat.get('id')
        
        # Only respond to messages from configured chat
        if chat_id != CHAT_ID:
            logging.debug(f"Ignoring message from chat {chat_id}")
            return {"status": "ok", "message": "Chat ID not matching"}
        
        text = message.get('text', '')
        if not text or not text.startswith('/'):
            return {"status": "ok", "message": "Not a command"}
        
        from_user = message.get('from', {})
        username = from_user.get('username') or from_user.get('first_name', 'User')
        message_id = message.get('message_id')
        
        # Process the command
        response_msg = self.process_command(text, username)
        
        if response_msg:
            self.send_message(response_msg, message_id)
            return {"status": "ok", "command": text, "responded": True}
        
        return {"status": "ok", "command": text, "responded": False}


# Singleton instance - will be initialized with db_helpers from app.py
bot_instance: Optional[TelegramBot] = None


def init_bot(db_helpers: dict) -> TelegramBot:
    """Initialize the bot with database helpers."""
    global bot_instance
    bot_instance = TelegramBot(db_helpers)
    return bot_instance


def get_bot() -> Optional[TelegramBot]:
    """Get the bot instance."""
    return bot_instance

