from flask import Flask, jsonify
from pymongo import MongoClient
from urllib.parse import quote_plus
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

@app.route('/')
def get_expenses():  # Renamed to match purpose
    """Fetches all documents from the '2025' collection."""
    if client is None:
        return jsonify({"error": "Database connection not available."}), 500
    try:
        # Find all documents in the collection
        expenses = list(collection.find({}))  # Keep _id for now, handle serialization below
        
        # Convert ObjectId and datetime to strings for JSON serialization
        for expense in expenses:
            if '_id' in expense:
                expense['_id'] = str(expense['_id'])
            for daily in expense.get('daily_expenses', []):
                if 'date' in daily:
                    daily['date'] = daily['date'].isoformat()
            for credit in expense.get('credits', []):
                if 'date' in credit:
                    credit['date'] = credit['date'].isoformat()
        
        return jsonify(expenses)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

import os

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
