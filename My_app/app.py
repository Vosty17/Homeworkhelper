
import os
import requests
import base64
from datetime import datetime, timedelta
import hashlib
import secrets
import time
from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_session import Session
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from PIL import Image
import io
import logging
import psycopg2
from psycopg2 import sql
from urllib.parse import urlparse
import sqlite3

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('FLASK_SECRET_KEY', 'dev-secret-key')
app.config['SESSION_TYPE'] = 'filesystem'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg'}

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)

# Initialize server-side session
Session(app)

class Database:
    def __init__(self):
        self.conn = self.get_db_connection()
        self.create_tables()

    def get_db_connection(self):
        """Connect to PostgreSQL in production, SQLite in development"""
        db_url = os.getenv('DATABASE_URL')
        
        if db_url:
            # Production - PostgreSQL
            parsed = urlparse(db_url)
            conn = psycopg2.connect(
                dbname=parsed.path[1:],
                user=parsed.username,
                password=parsed.password,
                host=parsed.hostname,
                port=parsed.port
            )
            logging.info("Connected to PostgreSQL database")
        else:
            # Development - SQLite
            conn = sqlite3.connect('homework_helper.db', check_same_thread=False)
            logging.info("Connected to SQLite database")
        return conn

    def create_tables(self):
        cursor = self.conn.cursor()
        
        # Users table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            email TEXT UNIQUE,
            phone TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Subscriptions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            plan_type TEXT NOT NULL,
            start_date TIMESTAMP NOT NULL,
            end_date TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
        )
        ''')
        
        # Payments table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            amount REAL NOT NULL,
            mpesa_receipt TEXT,
            phone_number TEXT NOT NULL,
            transaction_date TIMESTAMP,
            status TEXT DEFAULT 'pending'
        )
        ''')
        
        # Questions table
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS questions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            question_type TEXT NOT NULL,
            content TEXT,
            image_path TEXT,
            response TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            cost REAL NOT NULL,
            payment_id INTEGER REFERENCES payments(id)
        )
        ''')
        
        self.conn.commit()
        logging.info("Database tables created/verified")

    def add_user(self, username, password, email=None, phone=None):
        password_hash = generate_password_hash(password)
        
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            INSERT INTO users (username, password_hash, email, phone)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            ''', (username, password_hash, email, phone))
            user_id = cursor.fetchone()[0]
            self.conn.commit()
            return user_id
        except Exception as e:
            logging.error(f"Error adding user: {str(e)}")
            raise Exception("Username, email or phone already exists")

    def authenticate_user(self, username, password):
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT id, password_hash FROM users WHERE username = %s
            ''', (username,))
            result = cursor.fetchone()
            
            if not result:
                return None
                
            user_id, stored_hash = result
            if check_password_hash(stored_hash, password):
                return user_id
            return None
        except Exception as e:
            logging.error(f"Authentication error: {str(e)}")
            return None

    def get_user(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('''
        SELECT id, username, email, phone FROM users WHERE id = %s
        ''', (user_id,))
        return cursor.fetchone()

    def get_user_subscription(self, user_id):
        cursor = self.conn.cursor()
        cursor.execute('''
        SELECT plan_type, end_date FROM subscriptions 
        WHERE user_id = %s AND is_active = TRUE AND end_date > CURRENT_TIMESTAMP
        ORDER BY end_date DESC LIMIT 1
        ''', (user_id,))
        return cursor.fetchone()

    def create_subscription(self, user_id, plan_type, payment_id=None):
        start_date = datetime.now()
        end_date = start_date + timedelta(days=30) if plan_type == "monthly" else None
            
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO subscriptions (user_id, plan_type, start_date, end_date)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        ''', (user_id, plan_type, start_date, end_date))
        sub_id = cursor.fetchone()[0]
        self.conn.commit()
        return sub_id

    def create_payment(self, user_id, amount, phone_number):
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO payments (user_id, amount, phone_number, transaction_date)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        ''', (user_id, amount, phone_number, datetime.now()))
        payment_id = cursor.fetchone()[0]
        self.conn.commit()
        return payment_id

    def update_payment(self, payment_id, mpesa_receipt, status='completed'):
        cursor = self.conn.cursor()
        cursor.execute('''
        UPDATE payments 
        SET mpesa_receipt = %s, status = %s, transaction_date = %s
        WHERE id = %s
        ''', (mpesa_receipt, status, datetime.now(), payment_id))
        self.conn.commit()

    def record_question(self, user_id, question_type, content, image_path, response, cost, payment_id=None):
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO questions (user_id, question_type, content, image_path, response, cost, payment_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        ''', (user_id, question_type, content, image_path, response, cost, payment_id))
        question_id = cursor.fetchone()[0]
        self.conn.commit()
        return question_id

    def get_user_questions(self, user_id, limit=10):
        cursor = self.conn.cursor()
        cursor.execute('''
        SELECT id, question_type, content, image_path, response, timestamp, cost 
        FROM questions 
        WHERE user_id = %s
        ORDER BY timestamp DESC
        LIMIT %s
        ''', (user_id, limit))
        return cursor.fetchall()

    def get_user_payments(self, user_id, limit=10):
        cursor = self.conn.cursor()
        cursor.execute('''
        SELECT id, amount, mpesa_receipt, status, transaction_date 
        FROM payments 
        WHERE user_id = %s
        ORDER BY transaction_date DESC
        LIMIT %s
        ''', (user_id, limit))
        return cursor.fetchall()

class MpesaGateway:
    def __init__(self):
        self.consumer_key = os.getenv('MPESA_CONSUMER_KEY')
        self.consumer_secret = os.getenv('MPESA_CONSUMER_SECRET')
        self.business_shortcode = os.getenv('MPESA_BUSINESS_SHORTCODE')
        self.passkey = os.getenv('MPESA_PASSKEY')
        self.callback_url = os.getenv('MPESA_CALLBACK_URL')
        self.access_token = None
        self.token_expiry = None
        
    def get_access_token(self):
        if self.access_token and datetime.now() < self.token_expiry:
            return self.access_token
            
        auth_url = 'https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials'
        auth = (self.consumer_key, self.consumer_secret)
        
        try:
            response = requests.get(auth_url, auth=auth)
            response.raise_for_status()
            data = response.json()
            self.access_token = data['access_token']
            self.token_expiry = datetime.now() + timedelta(seconds=data['expires_in'] - 60)
            return self.access_token
        except Exception as e:
            logging.error(f"Failed to get M-Pesa access token: {str(e)}")
            raise Exception(f"Failed to get access token: {str(e)}")
    
    def stk_push(self, phone_number, amount, account_reference, description):
        token = self.get_access_token()
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password = base64.b64encode(
            f"{self.business_shortcode}{self.passkey}{timestamp}".encode()
        ).decode()
        
        payload = {
            "BusinessShortCode": self.business_shortcode,
            "Password": password,
            "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": amount,
            "PartyA": phone_number,
            "PartyB": self.business_shortcode,
            "PhoneNumber": phone_number,
            "CallBackURL": f"{self.callback_url}/callback",
            "AccountReference": account_reference,
            "TransactionDesc": description
        }
        
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        try:
            response = requests.post(
                'https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest',
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logging.error(f"STK push failed: {str(e)}")
            raise Exception(f"STK push failed: {str(e)}")

# Initialize services
db = Database()
mpesa = MpesaGateway()

# Pricing configuration
PRICING = {
    "pay_per_use": {"price": 10, "currency": "KES", "name": "Pay-per-Use"},
    "monthly": {"price": 500, "currency": "KES", "name": "Monthly Subscription"}
}

# Helper functions
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def process_image(image_path):
    with Image.open(image_path) as img:
        if max(img.size) > 1024:
            img.thumbnail((1024, 1024))
        
        buffered = io.BytesIO()
        img_format = "PNG"
        img.save(buffered, format=img_format)
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

def get_ai_response(prompt, image_base64=None):
    headers = {
        "Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY')}",
        "Content-Type": "application/json"
    }
    
    messages = [{"role": "user", "content": prompt}]
    
    if image_base64:
        messages[0]["content"] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": f"data:image/jpeg;base64,{image_base64}"}
        ]
    
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000
    }
    
    try:
        response = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers=headers,
            json=payload
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logging.error(f"AI API error: {str(e)}")
        raise Exception(f"AI API error: {str(e)}")

# Routes
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        try:
            user_id = db.authenticate_user(username, password)
            if user_id:
                session['user_id'] = user_id
                return redirect(url_for('dashboard'))
            else:
                return render_template('login.html', error="Invalid credentials")
        except Exception as e:
            return render_template('login.html', error=str(e))
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        email = request.form.get('email')
        phone = request.form.get('phone')
        
        try:
            db.add_user(username, password, email, phone)
            return redirect(url_for('login'))
        except Exception as e:
            return render_template('register.html', error=str(e))
    
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    try:
        user = db.get_user(user_id)
        subscription = db.get_user_subscription(user_id)
        questions = db.get_user_questions(user_id)
        payments = db.get_user_payments(user_id)
        
        return render_template('dashboard.html', 
                             user=user,
                             subscription=subscription,
                             questions=questions,
                             payments=payments,
                             pricing=PRICING)
    except Exception as e:
        logging.error(f"Dashboard error: {str(e)}")
        return render_template('500.html'), 500

@app.route('/ask', methods=['GET', 'POST'])
def ask_question():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    user = db.get_user(user_id)
    
    if request.method == 'POST':
        question = request.form.get('question')
        image = request.files.get('image')
        
        if not question and not image:
            return render_template('ask.html', error="Please provide a question or image")
        
        try:
            # Process payment
            subscription = db.get_user_subscription(user_id)
            if not subscription or subscription[0] != "monthly":
                payment_id = db.create_payment(user_id, PRICING['pay_per_use']['price'], user[3])
                response = mpesa.stk_push(
                    phone_number=user[3],
                    amount=PRICING['pay_per_use']['price'],
                    account_reference=f"HW{payment_id}",
                    description="Homework Question"
                )
                
                if 'ResponseCode' not in response or response['ResponseCode'] != '0':
                    raise Exception("Payment initiation failed")
                
                # Simulate payment confirmation (in prod, use webhook)
                time.sleep(10)
                mpesa_receipt = f"HW{payment_id}{secrets.token_hex(4)}"
                db.update_payment(payment_id, mpesa_receipt)
            else:
                payment_id = None
            
            # Process question
            image_path = None
            image_base64 = None
            
            if image and allowed_file(image.filename):
                filename = secure_filename(image.filename)
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
                image.save(image_path)
                image_base64 = process_image(image_path)
            
            prompt = "Explain this homework question in simple terms a parent can use to help their child: " + (question or "")
            ai_response = get_ai_response(prompt, image_base64)
            
            # Record question
            question_type = "image" if image else "text"
            db.record_question(
                user_id=user_id,
                question_type=question_type,
                content=question,
                image_path=image_path,
                response=ai_response,
                cost=PRICING['pay_per_use']['price'],
                payment_id=payment_id
            )
            
            return render_template('response.html', response=ai_response, image_path=image_path)
            
        except Exception as e:
            logging.error(f"Ask question error: {str(e)}")
            return render_template('ask.html', error=str(e))
    
    return render_template('ask.html')

@app.route('/subscribe', methods=['POST'])
def subscribe():
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    user_id = session['user_id']
    user = db.get_user(user_id)
    plan = request.form.get('plan')
    
    if plan not in PRICING:
        return jsonify({"success": False, "error": "Invalid plan"}), 400
    
    try:
        payment_id = db.create_payment(user_id, PRICING[plan]['price'], user[3])
        response = mpesa.stk_push(
            phone_number=user[3],
            amount=PRICING[plan]['price'],
            account_reference=f"SUB{payment_id}",
            description=f"{PRICING[plan]['name']} Subscription"
        )
        
        if 'ResponseCode' not in response or response['ResponseCode'] != '0':
            raise Exception("Payment initiation failed")
        
        # Simulate payment confirmation
        time.sleep(10)
        mpesa_receipt = f"SUB{payment_id}{secrets.token_hex(4)}"
        db.update_payment(payment_id, mpesa_receipt)
        db.create_subscription(user_id, plan, payment_id)
        
        return jsonify({"success": True, "receipt": mpesa_receipt})
    except Exception as e:
        logging.error(f"Subscription error: {str(e)}")
        return jsonify({"success": False, "error": str(e)}), 400

@app.route('/callback', methods=['POST'])
def callback():
    """Handle M-Pesa payment callback"""
    try:
        data = request.get_json()
        logging.info(f"Received M-Pesa callback: {data}")
        
        # In production: Verify the callback and update payment status
        # This is a simplified implementation
        
        # Example implementation:
        # if data.get('Body').get('stkCallback').get('ResultCode') == 0:
        #     merchant_request_id = data['Body']['stkCallback']['MerchantRequestID']
        #     update_payment_status(merchant_request_id, 'completed')
        
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})
    except Exception as e:
        logging.error(f"Callback error: {str(e)}")
        return jsonify({"ResultCode": 1, "ResultDesc": "Failed"}), 400

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# Error handlers
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(e):
    logging.error(f"Server error: {str(e)}")
    return render_template('500.html'), 500

if __name__ == '__main__':
    # Create uploads directory if it doesn't exist
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Get port from environment or default to 5000
    port = int(os.environ.get('PORT', 5000))
    
    # Run the app
    app.run(host='0.0.0.0', port=port)
