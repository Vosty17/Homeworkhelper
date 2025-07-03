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
from psycopg2 import sql, errors
from urllib.parse import urlparse
import sqlite3

# Initialize Flask app
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ['FLASK_SECRET_KEY']  # No fallback!
app.config['SESSION_TYPE'] = 'filesystem'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg'}
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB upload limit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)

# Initialize server-side session
Session(app)

class Database:
    def __init__(self):
        self.conn = self.get_db_connection()
        self.create_tables()
        self.create_indexes()

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
            conn.row_factory = sqlite3.Row
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_active BOOLEAN DEFAULT TRUE
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
            is_active BOOLEAN DEFAULT TRUE,
            payment_id INTEGER REFERENCES payments(id)
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
            status TEXT DEFAULT 'pending',
            CHECK (status IN ('pending', 'completed', 'failed'))
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

    def create_indexes(self):
        cursor = self.conn.cursor()
        try:
            # Index for faster subscription checks
            cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_subscriptions_user_active 
            ON subscriptions (user_id, end_date) WHERE is_active = TRUE
            ''')
            
            # Index for payment lookups
            cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_payments_user_status 
            ON payments (user_id, status)
            ''')
            
            self.conn.commit()
        except Exception as e:
            logging.error(f"Index creation failed: {e}")
            self.conn.rollback()

    def add_user(self, username, password, email=None, phone=None):
        password_hash = generate_password_hash(password)
        cursor = None
        
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
        except (psycopg2.IntegrityError, sqlite3.IntegrityError) as e:
            self.conn.rollback()
            raise ValueError("Username, email or phone already exists")
        except Exception as e:
            self.conn.rollback()
            logging.error(f"Error adding user: {e}")
            raise RuntimeError("Failed to create user")
        finally:
            if cursor:
                cursor.close()

    def authenticate_user(self, username, password):
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT id, password_hash FROM users 
            WHERE username = %s AND is_active = TRUE
            ''', (username,))
            result = cursor.fetchone()
            
            if not result:
                return None
                
            user_id, stored_hash = result
            if check_password_hash(stored_hash, password):
                return user_id
            return None
        except Exception as e:
            logging.error(f"Authentication error: {e}")
            return None
        finally:
            if cursor:
                cursor.close()

    def get_user(self, user_id):
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT id, username, email, phone FROM users 
            WHERE id = %s AND is_active = TRUE
            ''', (user_id,))
            return cursor.fetchone()
        except Exception as e:
            logging.error(f"Get user error: {e}")
            return None
        finally:
            if cursor:
                cursor.close()

    def get_user_subscription(self, user_id):
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT plan_type, end_date FROM subscriptions 
            WHERE user_id = %s AND is_active = TRUE AND end_date > CURRENT_TIMESTAMP
            ORDER BY end_date DESC LIMIT 1
            ''', (user_id,))
            return cursor.fetchone()
        except Exception as e:
            logging.error(f"Get subscription error: {e}")
            return None
        finally:
            if cursor:
                cursor.close()

    def create_subscription(self, user_id, plan_type, payment_id=None):
        start_date = datetime.now()
        end_date = start_date + timedelta(days=30) if plan_type == "monthly" else None
        cursor = None
        
        try:
            cursor = self.conn.cursor()
            # Deactivate any existing subscriptions
            cursor.execute('''
            UPDATE subscriptions 
            SET is_active = FALSE 
            WHERE user_id = %s AND is_active = TRUE
            ''', (user_id,))
            
            # Create new subscription
            cursor.execute('''
            INSERT INTO subscriptions (user_id, plan_type, start_date, end_date, payment_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            ''', (user_id, plan_type, start_date, end_date, payment_id))
            
            sub_id = cursor.fetchone()[0]
            self.conn.commit()
            return sub_id
        except Exception as e:
            self.conn.rollback()
            logging.error(f"Create subscription error: {e}")
            raise RuntimeError("Failed to create subscription")
        finally:
            if cursor:
                cursor.close()

    def create_payment(self, user_id, amount, phone_number):
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            INSERT INTO payments (user_id, amount, phone_number, transaction_date)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            ''', (user_id, amount, phone_number, datetime.now()))
            
            payment_id = cursor.fetchone()[0]
            self.conn.commit()
            return payment_id
        except Exception as e:
            self.conn.rollback()
            logging.error(f"Create payment error: {e}")
            raise RuntimeError("Failed to create payment record")
        finally:
            if cursor:
                cursor.close()

    def update_payment(self, payment_id, mpesa_receipt, status='completed'):
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            UPDATE payments 
            SET mpesa_receipt = %s, status = %s, transaction_date = %s
            WHERE id = %s
            ''', (mpesa_receipt, status, datetime.now(), payment_id))
            
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            logging.error(f"Update payment error: {e}")
            raise RuntimeError("Failed to update payment")
        finally:
            if cursor:
                cursor.close()

    def record_question(self, user_id, question_type, content, image_path, response, cost, payment_id=None):
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            INSERT INTO questions (user_id, question_type, content, image_path, response, cost, payment_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            ''', (user_id, question_type, content, image_path, response, cost, payment_id))
            
            question_id = cursor.fetchone()[0]
            self.conn.commit()
            return question_id
        except Exception as e:
            self.conn.rollback()
            logging.error(f"Record question error: {e}")
            raise RuntimeError("Failed to record question")
        finally:
            if cursor:
                cursor.close()

    def get_user_questions(self, user_id, limit=10):
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT id, question_type, content, image_path, response, timestamp, cost 
            FROM questions 
            WHERE user_id = %s
            ORDER BY timestamp DESC
            LIMIT %s
            ''', (user_id, limit))
            
            return cursor.fetchall()
        except Exception as e:
            logging.error(f"Get questions error: {e}")
            return []
        finally:
            if cursor:
                cursor.close()

    def get_user_payments(self, user_id, limit=10):
        cursor = None
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
            SELECT id, amount, mpesa_receipt, status, transaction_date 
            FROM payments 
            WHERE user_id = %s
            ORDER BY transaction_date DESC
            LIMIT %s
            ''', (user_id, limit))
            
            return cursor.fetchall()
        except Exception as e:
            logging.error(f"Get payments error: {e}")
            return []
        finally:
            if cursor:
                cursor.close()

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
            response = requests.get(auth_url, auth=auth, timeout=10)
            response.raise_for_status()
            data = response.json()
            self.access_token = data['access_token']
            self.token_expiry = datetime.now() + timedelta(seconds=data['expires_in'] - 60)
            return self.access_token
        except requests.exceptions.RequestException as e:
            logging.error(f"Failed to get M-Pesa access token: {e}")
            raise RuntimeError("M-Pesa authentication failed")

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
                json=payload,
                timeout=15
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"STK push failed: {e}")
            raise RuntimeError("Payment request failed")

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
    if not filename:
        return False
    # Prevent directory traversal
    if '../' in filename or '..\\' in filename:
        return False
    return ('.' in filename and 
            filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS'])

def process_image(image_path):
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if needed (for PNG with transparency)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            
            # Resize if too large
            if max(img.size) > 1024:
                img.thumbnail((1024, 1024))
            
            # Optimize image quality
            buffered = io.BytesIO()
            img.save(buffered, format='JPEG', quality=85)
            return base64.b64encode(buffered.getvalue()).decode('utf-8')
    except Exception as e:
        logging.error(f"Image processing failed: {e}")
        raise RuntimeError("Failed to process image")

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
            json=payload,
            timeout=30
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except requests.exceptions.RequestException as e:
        logging.error(f"AI API error: {e}")
        raise RuntimeError("AI service is currently unavailable. Please try again later.")

# Routes
@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return redirect(url_for('dashboard'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        
        if not username or not password:
            return render_template('login.html', error="Username and password are required")
        
        try:
            user_id = db.authenticate_user(username, password)
            if user_id:
                session['user_id'] = user_id
                return redirect(url_for('dashboard'))
            else:
                return render_template('login.html', error="Invalid credentials")
        except Exception as e:
            logging.error(f"Login error: {e}")
            return render_template('login.html', error="Login failed. Please try again.")
    
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        email = request.form.get('email', '').strip()
        phone = request.form.get('phone', '').strip()
        
        if not username or not password:
            return render_template('register.html', error="Username and password are required")
        
        try:
            db.add_user(username, password, email, phone)
            return redirect(url_for('login'))
        except ValueError as e:
            return render_template('register.html', error=str(e))
        except Exception as e:
            logging.error(f"Registration error: {e}")
            return render_template('register.html', error="Registration failed. Please try again.")
    
    return render_template('register.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    try:
        user = db.get_user(user_id)
        if not user:
            session.clear()
            return redirect(url_for('login'))
        
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
        logging.error(f"Dashboard error: {e}")
        return render_template('error.html', message="Failed to load dashboard"), 500

@app.route('/ask', methods=['GET', 'POST'])
def ask_question():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    user = db.get_user(user_id)
    if not user:
        session.clear()
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        question = request.form.get('question', '').strip()
        image = request.files.get('image')
        
        if not question and not image:
            return render_template('ask.html', error="Please provide a question or image")
        
        try:
            # Check subscription first
            subscription = db.get_user_subscription(user_id)
            payment_id = None
            
            if not subscription or subscription[0] != "monthly":
                # Process payment for non-subscribers
                payment_id = db.create_payment(user_id, PRICING['pay_per_use']['price'], user[3])
                response = mpesa.stk_push(
                    phone_number=user[3],
                    amount=PRICING['pay_per_use']['price'],
                    account_reference=f"HW{payment_id}",
                    description="Homework Question"
                )
                
                if 'ResponseCode' not in response or response['ResponseCode'] != '0':
                    raise RuntimeError("Payment initiation failed")
                
                # In production, wait for callback instead of sleeping
                return render_template('payment_pending.html', payment_id=payment_id)
            
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
            
        except RuntimeError as e:
            return render_template('ask.html', error=str(e))
        except Exception as e:
            logging.error(f"Ask question error: {e}")
            return render_template('ask.html', error="Failed to process question. Please try again.")
    
    return render_template('ask.html')

@app.route('/subscribe', methods=['POST'])
def subscribe():
    if 'user_id' not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    
    user_id = session['user_id']
    user = db.get_user(user_id)
    if not user:
        return jsonify({"success": False, "error": "User not found"}), 404
    
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
            raise RuntimeError("Payment initiation failed")
        
        return jsonify({
            "success": True,
            "message": "Payment initiated. You'll be notified when completed."
        })
    except RuntimeError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        logging.error(f"Subscription error: {e}")
        return jsonify({"success": False, "error": "Subscription failed"}), 500

@app.route('/callback', methods=['POST'])
def callback():
    """Handle M-Pesa payment callback"""
    try:
        data = request.get_json()
        logging.info(f"Received M-Pesa callback: {data}")
        
        # Example implementation (simplified)
        result_code = data.get('Body', {}).get('stkCallback', {}).get('ResultCode')
        merchant_request_id = data.get('Body', {}).get('stkCallback', {}).get('MerchantRequestID')
        checkout_request_id = data.get('Body', {}).get('stkCallback', {}).get('CheckoutRequestID')
        
        if result_code == '0':
            # Payment successful
            # In a real app, you would:
            # 1. Verify the callback is from M-Pesa
            # 2. Update your database
            # 3. Handle subscriptions if applicable
            
            # For demo purposes, we'll just log it
            logging.info(f"Payment successful: {merchant_request_id}")
        else:
            logging.warning(f"Payment failed: {data}")
        
        return jsonify({"ResultCode": 0, "ResultDesc": "Accepted"})
    except Exception as e:
        logging.error(f"Callback error: {e}")
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
    logging.error(f"Server error: {e}")
    return render_template('500.html'), 500

if __name__ == '__main__':
    # Create uploads directory if it doesn't exist
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    
    # Get port from environment or default to 5000
    port = int(os.environ.get('PORT', 5000))
    
    # Run the app
    app.run(host='0.0.0.0', port=port, threaded=True)
