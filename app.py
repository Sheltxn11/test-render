from flask import Flask, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient
from urllib.parse import quote_plus
from datetime import datetime
from dotenv import load_dotenv
import logging
import os

# Load environment variables from .env file
load_dotenv()

logging.basicConfig(level=logging.INFO)

# Constants
TRANSACTION_TYPE_PURCHASE = 'purchase'
TRANSACTION_TYPE_PAYMENT = 'payment'
DATE_FORMAT = "%Y-%m-%d"
MONTHS = ["January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December"]

client = None 
db = None
db_error_message = None  # Store error for debugging

try:
    # Get credentials from environment variables
    MONGO_USERNAME = os.getenv("MONGO_USERNAME")
    MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
    MONGO_CLUSTER_URI = os.getenv("MONGO_CLUSTER_URI")
    MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "grocery")
    
    # Debug: Log which env vars are set (without revealing values)
    logging.info(f"[DEBUG] MONGO_USERNAME set: {bool(MONGO_USERNAME)}")
    logging.info(f"[DEBUG] MONGO_PASSWORD set: {bool(MONGO_PASSWORD)}")
    logging.info(f"[DEBUG] MONGO_CLUSTER_URI set: {bool(MONGO_CLUSTER_URI)}")
    logging.info(f"[DEBUG] MONGO_CLUSTER_URI value: {MONGO_CLUSTER_URI}")
    logging.info(f"[DEBUG] MONGO_DB_NAME: {MONGO_DB_NAME}")
    
    if not MONGO_USERNAME or not MONGO_PASSWORD or not MONGO_CLUSTER_URI:
        missing = []
        if not MONGO_USERNAME: missing.append("MONGO_USERNAME")
        if not MONGO_PASSWORD: missing.append("MONGO_PASSWORD")
        if not MONGO_CLUSTER_URI: missing.append("MONGO_CLUSTER_URI")
        db_error_message = f"Missing environment variables: {', '.join(missing)}"
        raise ValueError(db_error_message)
    
    # URL-encode the username and password
    encoded_username = quote_plus(MONGO_USERNAME)
    encoded_password = quote_plus(MONGO_PASSWORD)

    # Construct the secure connection string
    MONGO_URI = (
        f"mongodb+srv://{encoded_username}:{encoded_password}@{MONGO_CLUSTER_URI}/"
        f"?retryWrites=true&w=majority&appName=1PM"
    )
    
    logging.info(f"[DEBUG] Attempting MongoDB connection to cluster: {MONGO_CLUSTER_URI}")
        
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) 
    client.admin.command('ismaster')
    
    db = client[MONGO_DB_NAME]

    logging.info("Successfully connected to MongoDB.")
    db_error_message = None  # Clear any error on success
except Exception as e:
    db_error_message = str(e)
    logging.critical(f"Could not connect to MongoDB: {e}")
    logging.critical(f"[DEBUG] Exception type: {type(e).__name__}")
    client = None

app = Flask(__name__)
# Simplified CORS configuration - remove redundant headers
CORS(app, origins="*", methods=['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
     allow_headers=['Content-Type', 'Authorization'])

# Import and initialize Telegram bot (after db and helper functions are defined)
telegram_bot = None  # Will be initialized after helper functions are defined

def get_month_name_from_date(date_str: str) -> str:
    """Converts date string (YYYY-MM-DD) to month name like 'January', 'February', etc.
    
    Args:
        date_str: Date in YYYY-MM-DD format
        
    Returns:
        Month name (e.g., 'January')
        
    Raises:
        ValueError: If date format is invalid
    """
    try:
        date_obj = datetime.strptime(date_str, DATE_FORMAT)
        return date_obj.strftime("%B")
    except ValueError:
        raise ValueError(f"Invalid date format: '{date_str}'. Expected format: YYYY-MM-DD")

def get_previous_month_name(month_name: str) -> str:
    """Get the name of the previous month.
    
    Args:
        month_name: Current month name (e.g., 'January')
        
    Returns:
        Previous month name (e.g., 'December' for January input)
    """
    try:
        current_index = MONTHS.index(month_name)
        previous_index = (current_index - 1) % 12
        return MONTHS[previous_index]
    except ValueError:
        logging.warning(f"Invalid month name: {month_name}")
        return None

def get_collection_by_year(year: int):
    """Get MongoDB collection for a specific year.
    
    Args:
        year: The year for the collection
        
    Returns:
        MongoDB collection object
    """
    return db[str(year)]

def get_previous_month_balance(month_name: str, year: int) -> float:
    """Get the balance from the previous month, handling cross-year transitions.
    
    Args:
        month_name: Current month name
        year: Current year
        
    Returns:
        Previous month's balance (0 if not found)
    """
    previous_month_name = get_previous_month_name(month_name)
    if not previous_month_name:
        return 0
    
    # If current month is January, look at December of previous year
    if month_name == "January":
        prev_year = year - 1
        prev_collection = get_collection_by_year(prev_year)
        prev_doc = prev_collection.find_one({"month": "December"})
        if prev_doc:
            balance = prev_doc.get('balance', 0)
            logging.info(f"January {year}: Inherited balance {balance} from December {prev_year}")
            return balance
    else:
        # Same year, same collection
        collection_obj = get_collection_by_year(year)
        prev_doc = collection_obj.find_one({"month": previous_month_name})
        if prev_doc:
            return prev_doc.get('balance', 0)
    
    return 0

def serialize_document(doc: dict) -> dict:
    """Convert MongoDB document to JSON-serializable format.
    
    Args:
        doc: MongoDB document
        
    Returns:
        JSON-serializable dictionary
    """
    if not doc:
        return doc
        
    if '_id' in doc:
        doc['_id'] = str(doc['_id'])
    
    # Handle daily_expenses dates
    for expense in doc.get('daily_expenses', []):
        if 'date' in expense and isinstance(expense['date'], datetime):
            expense['date'] = expense['date'].isoformat()
    
    # Handle credits dates
    for credit in doc.get('credits', []):
        if 'date' in credit and isinstance(credit['date'], datetime):
            credit['date'] = credit['date'].isoformat()
    
    return doc

def create_month_skeleton(month_name: str, year: int) -> dict:
    """Create a new month document with empty arrays and inherited balance.
    
    Args:
        month_name: Month name (e.g., 'January')
        year: Year for the month
        
    Returns:
        Month document dictionary
    """
    previous_balance = get_previous_month_balance(month_name, year)
    
    return {
        "month": month_name,
        "daily_expenses": [],
        "credits": [],
        "total_expense": 0,
        "balance": previous_balance
    }

def recalculate_month_totals(doc: dict, previous_balance: float = 0) -> dict:
    """Recalculate total_expense and balance for a month document.
    
    Args:
        doc: Month document
        previous_balance: Balance carried over from previous month
        
    Returns:
        Updated month document with recalculated totals
    """
    # Calculate total expenses (purchases) for this month
    total_expense = sum(expense.get('amount', 0) for expense in doc.get('daily_expenses', []))
    
    # Calculate total credits (payments) for this month
    total_credits = sum(credit.get('amount', 0) for credit in doc.get('credits', []))
    
    doc['total_expense'] = total_expense
    
    # Balance = previous month's balance + this month's purchases - this month's payments
    doc['balance'] = previous_balance + total_expense - total_credits
    
    logging.info(f"Recalculated {doc.get('month', 'Unknown')}: "
                f"expenses={total_expense}, credits={total_credits}, "
                f"prev_balance={previous_balance}, new_balance={doc['balance']}")
    
    return doc

# ==================== TELEGRAM BOT INITIALIZATION ====================

from telegram_bot import init_bot, get_bot, TelegramBot, WEBHOOK_SECRET

# Initialize Telegram bot with database helpers
db_helpers = {
    'get_collection_by_year': get_collection_by_year,
    'get_previous_month_balance': get_previous_month_balance,
    'create_month_skeleton': create_month_skeleton,
    'recalculate_month_totals': recalculate_month_totals,
    'get_month_name_from_date': get_month_name_from_date,
}
telegram_bot = init_bot(db_helpers)

# ==================== API ROUTES ====================

@app.route('/health')
def health_check():
    """Health check endpoint."""
    return jsonify({"status": "healthy", "message": "Backend is running"}), 200

@app.route('/api/debug/env')
def debug_env():
    """Debug endpoint to check environment variables (remove in production)."""
    return jsonify({
        "db_connected": client is not None,
        "db_error": db_error_message,
        "env_vars": {
            "MONGO_USERNAME_set": bool(os.getenv("MONGO_USERNAME")),
            "MONGO_PASSWORD_set": bool(os.getenv("MONGO_PASSWORD")),
            "MONGO_CLUSTER_URI_set": bool(os.getenv("MONGO_CLUSTER_URI")),
            "MONGO_CLUSTER_URI_value": os.getenv("MONGO_CLUSTER_URI"),  # Safe to show
            "MONGO_DB_NAME": os.getenv("MONGO_DB_NAME", "grocery"),
            "TELEGRAM_BOT_TOKEN_set": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
            "TELEGRAM_CHAT_ID_set": bool(os.getenv("TELEGRAM_CHAT_ID")),
        }
    }), 200

# ==================== TELEGRAM BOT ROUTES ====================

@app.route('/api/telegram/webhook', methods=['POST'])
def telegram_webhook():
    """Webhook endpoint for Telegram - receives messages automatically."""
    try:
        # Verify secret token (Telegram sends this in header)
        secret_token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
        
        update = request.get_json()
        if not update:
            return jsonify({"status": "ok"}), 200
        
        bot = get_bot()
        if not bot:
            logging.error("Telegram bot not initialized")
            return jsonify({"status": "ok"}), 200
        
        result = bot.handle_webhook(update, secret_token)
        logging.info(f"Webhook processed: {result}")
        
        # Always return 200 to Telegram (even on errors)
        return jsonify({"status": "ok"}), 200
        
    except Exception as e:
        logging.error(f"Webhook error: {e}")
        return jsonify({"status": "ok"}), 200

@app.route('/api/telegram/setup-webhook')
def setup_telegram_webhook():
    """One-time setup: Register webhook with Telegram after deploying to Vercel."""
    try:
        # Get the host from request or use a provided URL
        webhook_base = request.args.get('url')
        
        if not webhook_base:
            # Try to construct from request
            host = request.host_url.rstrip('/')
            if 'localhost' in host or '127.0.0.1' in host:
                return jsonify({
                    "error": "Cannot setup webhook on localhost",
                    "hint": "Deploy to Vercel first, then call: /api/telegram/setup-webhook?url=https://your-app.vercel.app"
                }), 400
            webhook_base = host
        
        webhook_url = f"{webhook_base}/api/telegram/webhook"
        
        bot = get_bot()
        if not bot:
            return jsonify({"error": "Telegram bot not initialized"}), 500
        
        result = bot.setup_webhook(webhook_url)
        
        if result.get('success'):
            return jsonify({
                "message": "Webhook registered successfully!",
                "webhook_url": webhook_url,
                "next_steps": [
                    "Send /help in your Telegram group",
                    "The bot should reply automatically"
                ]
            }), 200
        else:
            return jsonify({"error": result.get('error')}), 500
            
    except Exception as e:
        logging.error(f"Setup webhook error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/telegram/remove-webhook')
def remove_telegram_webhook():
    """Remove webhook (switch back to polling mode)."""
    try:
        bot = get_bot()
        if not bot:
            return jsonify({"error": "Telegram bot not initialized"}), 500
        
        result = bot.remove_webhook()
        
        if result.get('success'):
            return jsonify({
                "message": "Webhook removed successfully",
                "mode": "You can now use polling via /api/telegram/poll"
            }), 200
        else:
            return jsonify({"error": result.get('error')}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/telegram/webhook-info')
def get_webhook_info():
    """Check current webhook status."""
    try:
        bot = get_bot()
        if not bot:
            return jsonify({"error": "Telegram bot not initialized"}), 500
        
        info = bot.get_webhook_info()
        return jsonify(info), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/telegram/get-updates')
def telegram_get_updates():
    """Helper endpoint to get Telegram updates and find chat ID."""
    try:
        bot = get_bot()
        if not bot:
            return jsonify({"error": "Telegram bot not initialized"}), 500
        
        data = bot.get_updates()
        
        chats = []
        for update in data.get('result', []):
            message = update.get('message', {})
            chat = message.get('chat', {})
            if chat:
                chats.append({
                    "chat_id": chat.get('id'),
                    "type": chat.get('type'),
                    "title": chat.get('title', chat.get('first_name', 'Unknown'))
                })
        
        return jsonify({
            "instruction": "Find your family group chat_id below",
            "chats": chats,
            "total_updates": len(data.get('result', [])),
            "hint": "If empty, make sure bot is added to group and you sent a message"
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/telegram/send-reminder', methods=['POST'])
def send_reminder():
    """Send a due reminder to the family Telegram group."""
    try:
        bot = get_bot()
        if not bot:
            return jsonify({"error": "Telegram bot not initialized"}), 500
        
        message = bot.generate_due_summary()
        success = bot.send_message(message)
        
        if success:
            return jsonify({"message": "Reminder sent successfully!"}), 200
        else:
            return jsonify({"error": "Failed to send reminder"}), 500
    except Exception as e:
        logging.error(f"Error sending reminder: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/telegram/send-custom', methods=['POST'])
def send_custom_message():
    """Send a custom message to the family Telegram group."""
    try:
        data = request.get_json()
        custom_message = data.get('message', '')
        
        if not custom_message:
            return jsonify({"error": "Message is required"}), 400
        
        bot = get_bot()
        if not bot:
            return jsonify({"error": "Telegram bot not initialized"}), 500
        
        success = bot.send_message(f"<b>Message from Grocery Tracker</b>\n\n{custom_message}")
        
        if success:
            return jsonify({"message": "Message sent successfully!"}), 200
        else:
            return jsonify({"error": "Failed to send message"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==================== DATA ROUTES ====================

@app.route('/api/available-years')
def get_available_years():
    """Get list of years that have data in the database."""
    if client is None:
        return jsonify({
            "error": "Database connection not available.",
            "debug_reason": db_error_message or "Unknown error during startup"
        }), 500
    
    try:
        # Get all collection names (which are years)
        all_collections = db.list_collection_names()
        
        # Filter to only numeric collections (years) and sort
        years = sorted([int(c) for c in all_collections if c.isdigit()])
        
        # Always include current year and next year
        current_year = datetime.now().year
        years_set = set(years)
        years_set.add(current_year)
        years_set.add(current_year + 1)
        
        return jsonify(sorted(list(years_set))), 200
    except Exception as e:
        logging.error(f"Error fetching available years: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/chart-data')
def get_chart_data():
    """Get last N months of data for charts (spans across years)."""
    if client is None:
        return jsonify({
            "error": "Database connection not available.",
            "debug_reason": db_error_message or "Unknown error during startup"
        }), 500
    
    try:
        months_count = int(request.args.get('months', 10))
        months_count = min(max(months_count, 1), 24)  # Limit between 1-24
        
        current_date = datetime.now()
        result = []
        
        # Go back N months
        for i in range(months_count - 1, -1, -1):
            # Calculate the month and year
            target_month = current_date.month - i
            target_year = current_date.year
            
            while target_month <= 0:
                target_month += 12
                target_year -= 1
            
            month_name = MONTHS[target_month - 1]
            collection_obj = get_collection_by_year(target_year)
            month_doc = collection_obj.find_one({"month": month_name})
            
            if month_doc:
                total_purchases = month_doc.get('total_expense', 0)
                total_payments = sum(c.get('amount', 0) for c in month_doc.get('credits', []))
                balance = month_doc.get('balance', 0)
            else:
                total_purchases = 0
                total_payments = 0
                balance = 0
            
            result.append({
                "month": month_name[:3],
                "month_full": month_name,
                "year": target_year,
                "purchases": total_purchases,
                "payments": total_payments,
                "balance": balance
            })
        
        return jsonify(result), 200
    except Exception as e:
        logging.error(f"Error fetching chart data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/prev-month-paid')
def get_prev_month_paid():
    """Get total payments made in the previous month."""
    if client is None:
        return jsonify({"error": "Database connection not available."}), 500
    
    try:
        # Get month and year from query parameters
        month_name = request.args.get('month')
        year = int(request.args.get('year', datetime.now().year))
        
        if not month_name:
            return jsonify({"error": "Month parameter is required"}), 400
        
        previous_month_name = get_previous_month_name(month_name)
        if not previous_month_name:
            return jsonify({"prev_month_paid": 0}), 200
        
        # Handle cross-year (January looks at December of previous year)
        if month_name == "January":
            prev_year = year - 1
            prev_collection = get_collection_by_year(prev_year)
            prev_doc = prev_collection.find_one({"month": "December"})
        else:
            prev_collection = get_collection_by_year(year)
            prev_doc = prev_collection.find_one({"month": previous_month_name})
        
        if prev_doc:
            # Calculate total payments (credits) from previous month
            total_paid = sum(credit.get('amount', 0) for credit in prev_doc.get('credits', []))
            return jsonify({"prev_month_paid": total_paid}), 200
        
        return jsonify({"prev_month_paid": 0}), 200
    except Exception as e:
        logging.error(f"Error fetching prev month paid: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/monthly-data')
def get_monthly_data():
    """Fetches all monthly documents from the current year's collection."""
    if client is None:
        return jsonify({"error": "Database connection not available."}), 500
    
    try:
        # Get year from query parameter or use current year
        year = request.args.get('year', datetime.now().year)
        collection_obj = db[str(year)]
        
        # Find all documents in the collection
        expenses = list(collection_obj.find({}))
        
        # Serialize documents for JSON response
        serialized_expenses = [serialize_document(expense) for expense in expenses]
        
        return jsonify(serialized_expenses), 200
    except Exception as e:
        logging.error(f"Error fetching monthly data: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/transactions', methods=['POST', 'OPTIONS'])
def add_transaction():
    """Add a new transaction to the database."""
    if request.method == 'OPTIONS':
        return jsonify({}), 200
    
    if client is None:
        return jsonify({"error": "Database connection not available."}), 500
    
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['date', 'type', 'amount']
        if not all(key in data for key in required_fields):
            return jsonify({
                "error": f"Missing required fields. Required: {', '.join(required_fields)}"
            }), 400
        
        date_str = data['date']
        transaction_type = data['type']
        amount = float(data['amount'])
        description = data.get('description', '')  # Optional description field
        
        # Validate amount
        if amount <= 0:
            return jsonify({"error": "Amount must be greater than zero"}), 400
        
        # Validate transaction type
        valid_types = [TRANSACTION_TYPE_PURCHASE, TRANSACTION_TYPE_PAYMENT]
        if transaction_type not in valid_types:
            return jsonify({
                "error": f"Invalid transaction type. Must be one of: {', '.join(valid_types)}"
            }), 400
        
        # Get the month name from the date
        month_name = get_month_name_from_date(date_str)
        
        # Convert date string to datetime object
        transaction_date = datetime.strptime(date_str, DATE_FORMAT)
        
        # Get year from transaction date and use appropriate collection
        year = transaction_date.year
        collection_name = str(year)
        collection_obj = get_collection_by_year(year)
        
        # Check if this is a new collection/year
        existing_collections = db.list_collection_names()
        if collection_name not in existing_collections:
            logging.info(f"🎉 Creating new collection for year {year}")
        
        # Find or create month document
        month_doc = collection_obj.find_one({"month": month_name})
        
        if month_doc is None:
            # Create new month skeleton
            month_doc = create_month_skeleton(month_name, year)
            try:
                collection_obj.insert_one(month_doc)
                logging.info(f"✨ Created new month document: {month_name} {year}")
            except Exception as insert_error:
                # Handle race condition if document was created by another request
                logging.warning(f"Race condition during month creation: {insert_error}")
                month_doc = collection_obj.find_one({"month": month_name})
        
        # Create transaction object
        transaction_entry = {
            "date": transaction_date,
            "amount": amount
        }
        
        # Add description if provided
        if description:
            transaction_entry["description"] = description
        
        # Add transaction to appropriate array
        if transaction_type == TRANSACTION_TYPE_PURCHASE:
            field_name = "daily_expenses"
            action = "purchase"
        else:  # payment
            field_name = "credits"
            action = "payment"
        
        result = collection_obj.update_one(
            {"month": month_name},
            {"$push": {field_name: transaction_entry}}
        )
        logging.info(f"✅ Added {action}: ${amount} on {date_str} ({year})")
        
        # Fetch the updated document
        updated_doc = collection_obj.find_one({"month": month_name})
        
        # Get previous month's balance and recalculate totals
        previous_balance = get_previous_month_balance(month_name, year)
        updated_doc = recalculate_month_totals(updated_doc, previous_balance)
        
        # Update the document with new totals
        update_result = collection_obj.update_one(
            {"month": month_name},
            {"$set": {
                "total_expense": updated_doc['total_expense'], 
                "balance": updated_doc['balance']
            }}
        )
        logging.info(f"Updated totals for {month_name} {year}. Modified: {update_result.modified_count}")
        
        # Fetch the final updated document
        final_doc = collection_obj.find_one({"month": month_name})
        
        # Serialize and return
        serialized_doc = serialize_document(final_doc)
        
        return jsonify({
            "message": f"Transaction added successfully to {month_name} {year}",
            "data": serialized_doc
        }), 201
        
    except ValueError as e:
        logging.error(f"Validation error: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error adding transaction: {e}")
        return jsonify({"error": str(e)}), 500

import os

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
