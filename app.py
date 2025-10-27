from flask import Flask, jsonify, request
from flask_cors import CORS
from pymongo import MongoClient
from urllib.parse import quote_plus
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)

client = None 
db = None
collection = None 

try:
    # 1. Get credentials and cluster info
    MONGO_USERNAME = "Shelton"
    MONGO_PASSWORD = "Shelton@2004"
    MONGO_CLUSTER_URI = "1pm.onh1q0g.mongodb.net"
    
    # 2. URL-encode the username and password using quote_plus
    encoded_username = quote_plus(MONGO_USERNAME)
    encoded_password = quote_plus(MONGO_PASSWORD)

    # 3. Construct the secure connection string
    MONGO_URI = (
        f"mongodb+srv://{encoded_username}:{encoded_password}@{MONGO_CLUSTER_URI}/"
        f"?retryWrites=true&w=majority&appName=1PM"
    )

    if not MONGO_USERNAME or not MONGO_PASSWORD:
        raise ValueError("MongoDB credentials not set.")  # Fixed: removed backslash
        
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000) 
    client.admin.command('ismaster')
    
    db = client.grocery        
    collection = db['2025']

    logging.info("Successfully connected to MongoDB.")
except Exception as e:
    logging.critical(f"Could not connect to MongoDB: {e}")
    client = None

app = Flask(__name__)
CORS(app)

def get_month_name_from_date(date_str):
    """Converts date string (YYYY-MM-DD) to month name like 'January', 'February', etc."""
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        return date_obj.strftime("%B")
    except ValueError:
        raise ValueError(f"Invalid date format: {date_str}")

def get_previous_month_name(month_name):
    """Get the name of the previous month."""
    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    try:
        current_index = months.index(month_name)
        previous_index = (current_index - 1) % 12
        return months[previous_index]
    except ValueError:
        return None

def serialize_document(doc):
    """Convert MongoDB document to JSON-serializable format."""
    if '_id' in doc:
        doc['_id'] = str(doc['_id'])
    for daily in doc.get('daily_expenses', []):
        if 'date' in daily:
            daily['date'] = daily['date'].isoformat()
    for credit in doc.get('credits', []):
        if 'date' in credit:
            credit['date'] = credit['date'].isoformat()
    return doc

def create_month_skeleton(month_name):
    """Create a new month document with empty arrays."""
    # Get the balance from the previous month
    previous_month_name = get_previous_month_name(month_name)
    previous_balance = 0
    
    if previous_month_name:
        prev_doc = collection.find_one({"month": previous_month_name})
        if prev_doc:
            previous_balance = prev_doc.get('balance', 0)
    
    return {
        "month": month_name,
        "daily_expenses": [],
        "credits": [],
        "total_expense": 0,
        "balance": previous_balance
    }

def recalculate_month_totals(doc, previous_balance=0):
    """Recalculate total_expense and balance for a month document."""
    # Calculate total expenses (purchases) for this month
    total_expense = sum(expense.get('amount', 0) for expense in doc.get('daily_expenses', []))
    
    # Calculate total credits (payments) for this month
    total_credits = sum(credit.get('amount', 0) for credit in doc.get('credits', []))
    
    doc['total_expense'] = total_expense
    
    # Balance = previous month's balance + this month's purchases - this month's payments
    # Or simplified: previous_balance + total_expense - total_credits
    doc['balance'] = previous_balance + total_expense - total_credits
    
    logging.info(f"Recalculated totals for {doc.get('month', 'Unknown')}: expenses={total_expense}, credits={total_credits}, previous_balance={previous_balance}, new_balance={doc['balance']}")
    
    return doc

@app.route('/')
def get_expenses():
    """Fetches all documents from the '2025' collection."""
    if client is None:
        return jsonify({"error": "Database connection not available."}), 500
    try:
        # Find all documents in the collection
        expenses = list(collection.find({}))
        
        # Serialize documents for JSON response
        serialized_expenses = [serialize_document(expense) for expense in expenses]
        
        return jsonify(serialized_expenses)
    except Exception as e:
        logging.error(f"Error fetching expenses: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/api/transactions', methods=['POST'])
def add_transaction():
    """Add a new transaction to the database."""
    if client is None:
        return jsonify({"error": "Database connection not available."}), 500
    
    try:
        data = request.get_json()
        
        # Validate required fields
        if not all(key in data for key in ['date', 'type', 'amount']):
            return jsonify({"error": "Missing required fields: date, type, amount"}), 400
        
        date_str = data['date']
        transaction_type = data['type']
        amount = float(data['amount'])
        
        # Validate transaction type
        if transaction_type not in ['purchase', 'payment']:
            return jsonify({"error": "Invalid transaction type. Must be 'purchase' or 'payment'"}), 400
        
        # Get the month name from the date
        month_name = get_month_name_from_date(date_str)
        
        # Convert date string to datetime object
        transaction_date = datetime.strptime(date_str, "%Y-%m-%d")
        
        # Find or create month document
        month_doc = collection.find_one({"month": month_name})
        
        if month_doc is None:
            # Create new month skeleton
            month_doc = create_month_skeleton(month_name)
            collection.insert_one(month_doc)
            month_doc['_id'] = str(month_doc['_id'])
            logging.info(f"Created new month document for {month_name}")
        
        # Create transaction object
        transaction_entry = {
            "date": transaction_date,
            "amount": amount
        }
        
        # Add transaction to appropriate array
        if transaction_type == 'purchase':
            result = collection.update_one(
                {"month": month_name},
                {"$push": {"daily_expenses": transaction_entry}}
            )
            logging.info(f"Added purchase transaction: {amount} on {date_str}. Modified count: {result.modified_count}")
        else:  # payment
            result = collection.update_one(
                {"month": month_name},
                {"$push": {"credits": transaction_entry}}
            )
            logging.info(f"Added payment transaction: {amount} on {date_str}. Modified count: {result.modified_count}")
        
        # Log current state before recalculation
        temp_doc = collection.find_one({"month": month_name})
        logging.info(f"Current state - Expenses count: {len(temp_doc.get('daily_expenses', []))}, Credits count: {len(temp_doc.get('credits', []))}")
        
        # Recalculate totals
        # Fetch the updated document
        updated_doc = collection.find_one({"month": month_name})
        
        # Get previous month's balance
        previous_month_name = get_previous_month_name(month_name)
        previous_balance = 0
        
        if previous_month_name:
            prev_doc = collection.find_one({"month": previous_month_name})
            if prev_doc:
                previous_balance = prev_doc.get('balance', 0)
        
        # Recalculate totals with previous month's balance
        updated_doc = recalculate_month_totals(updated_doc, previous_balance)
        
        # Update the document with new totals
        update_result = collection.update_one(
            {"month": month_name},
            {"$set": {"total_expense": updated_doc['total_expense'], "balance": updated_doc['balance']}}
        )
        logging.info(f"Updated totals for {month_name}. Modified count: {update_result.modified_count}")
        
        # Fetch the final updated document
        final_doc = collection.find_one({"month": month_name})
        
        # Serialize and return
        serialized_doc = serialize_document(final_doc)
        
        return jsonify({
            "message": f"Transaction added successfully to {month_name}",
            "data": serialized_doc
        }), 201
        
    except ValueError as e:
        logging.error(f"Validation error: {e}")
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logging.error(f"Error adding transaction: {e}")
        return jsonify({"error": str(e)}), 500
    
@app.route('/health')
def health_check():
    print("Health Check")
    return jsonify({"status": "ok"}), 200

import os

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
