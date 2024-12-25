from flask import Flask, request, jsonify, session
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import URLSafeTimedSerializer
import requests
import hashlib
import datetime
import uuid
import paypalrestsdk

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db = SQLAlchemy(app)
s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# External API details
api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"
api_key = "AIzaSyBUseAwUtOB_E0wW3LsNCQ43OhhFTzcmJQ"

# PayPal SDK configuration
paypalrestsdk.configure({
    "mode": "sandbox",  # or "live" for production
    "client_id": "your_paypal_client_id",
    "client_secret": "your_paypal_client_secret"
})

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    subscription_plan = db.Column(db.String(50), nullable=True)
    subscription_expiry = db.Column(db.Date, nullable=True)
    token_count = db.Column(db.Integer, default=5)

    def set_password(self, password):
        self.password = hashlib.sha256(password.encode()).hexdigest()

    def check_password(self, password):
        return self.password == hashlib.sha256(password.encode()).hexdigest()

    def is_subscribed(self):
        if self.subscription_expiry and self.subscription_expiry >= datetime.date.today():
            return True
        return False

    def update_subscription(self, plan, months):
        self.subscription_plan = plan
        self.subscription_expiry = datetime.date.today() + datetime.timedelta(days=30 * months)
        self.token_count = float('inf')  # Unlimited tokens with subscription

# Chat history model
class ChatHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    conversation_id = db.Column(db.String(100), nullable=False)
    title = db.Column(db.String(50), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    response = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    user = db.relationship('User', backref=db.backref('chats', lazy=True))

# Create tables
with app.app_context():
    # db.drop_all()
    db.create_all()

# User registration endpoint
@app.route('/api/register', methods=['POST'])
def register():
    data = request.json
    email = data.get('email')
    password = data.get('password')

    if User.query.filter_by(email=email).first():
        return jsonify({"message": "Email already registered"}), 400

    user = User(email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    return jsonify({"message": "User registered successfully"}), 201

# User login endpoint
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    user = User.query.filter_by(email=email).first()

    if user and user.check_password(password):
        # Save user ID in session
        session['user_id'] = user.id
        
        # Check subscription status
        subscription_status = "Subscribed" if user.is_subscribed() else "Not Subscribed"
        
        return jsonify({
            "message": "Login successful",
            "subscription_status": subscription_status
        }), 200
    
    return jsonify({"message": "Invalid credentials"}), 401

# User logout endpoint
@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({"message": "Logged out successfully"}), 200

# Subscription endpoint
@app.route('/api/subscribe', methods=['POST'])
def subscribe():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized, please log in"}), 401

    data = request.json
    plan = data.get('plan')
    months = data.get('months')

    if plan not in ['1', '6', '12']:
        return jsonify({"error": "Invalid plan, choose 1, 6, or 12 months"}), 400

    user = User.query.get(session['user_id'])

    # Update subscription based on the plan
    if plan == '1':
        user.update_subscription('1 Month', 1)
    elif plan == '6':
        user.update_subscription('6 Months', 6)
    else:
        user.update_subscription('1 Year', 12)

    db.session.commit()

    # Now redirect to PayPal payment page
    payment = paypalrestsdk.Payment({
        "intent": "sale",
        "payer": {
            "payment_method": "paypal"
        },
        "transactions": [{
            "amount": {
                "total": str(10 * months),  # Example cost: 10 USD per month
                "currency": "USD"
            },
            "description": f"Subscription for {months} months"
        }],
        "redirect_urls": {
            "return_url": "http://localhost:5000/api/payment/success",
            "cancel_url": "http://localhost:5000/api/payment/cancel"
        }
    })

    if payment.create():
        for link in payment.links:
            if link.rel == "approval_url":
                return jsonify({"approval_url": link.href})

    return jsonify({"error": "Payment creation failed"}), 500

# PayPal payment success endpoint
@app.route('/api/payment/success', methods=['GET'])
def payment_success():
    payment_id = request.args.get('paymentId')
    payer_id = request.args.get('PayerID')

    payment = paypalrestsdk.Payment.find(payment_id)

    if payment.execute({"payer_id": payer_id}):
        return jsonify({"message": "Payment successful, subscription activated!"}), 200
    else:
        return jsonify({"error": "Payment execution failed"}), 500

# PayPal payment cancel endpoint
@app.route('/api/payment/cancel', methods=['GET'])
def payment_cancel():
    return jsonify({"message": "Payment cancelled"}), 200

# Chat creation endpoint
@app.route('/api/chat', methods=['POST'])
def chat():
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized, please log in"}), 401

    data = request.json
    message = data.get('message')
    title = data.get('title', 'General')

    if not message:
        return jsonify({"error": "Message is required"}), 400

    user = User.query.get(session['user_id'])

    # Check if user is subscribed and has tokens left
    if not user.is_subscribed() and user.token_count <= 0:
        return jsonify({"error": "No tokens left, please subscribe or wait for refill"}), 403

    # Prepare request to external API
    payload = {
        "contents": [
            {"parts": [{"text": message}]}
        ]
    }

    headers = {'Content-Type': 'application/json'}
    response = requests.post(f"{api_url}?key={api_key}", json=payload, headers=headers)

    # Log the response from the external API
    print(f"External API response: {response.status_code} - {response.text}")

    if response.status_code != 200:
        return jsonify({"error": "Failed to get response from API"}), 500

    response_data = response.json()
    chat_response = response_data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', 'No response')

    # Store chat in database
    conversation_id = str(uuid.uuid4())  # Generate unique conversation ID
    chat_record = ChatHistory(
        user_id=user.id,
        conversation_id=conversation_id,
        title=title,
        message=message,
        response=chat_response
    )

    if not user.is_subscribed():
        user.token_count -= 1  # Deduct a token after each chat
    db.session.add(chat_record)
    db.session.commit()

    return jsonify({
        "conversation_id": conversation_id,
        "message": message,
        "response": chat_response
    }), 200

# Fetch chat by conversation and user
@app.route('/api/chat/<conversation_id>', methods=['GET'])
def get_chat_by_id(conversation_id):
    if 'user_id' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user = User.query.get(session['user_id'])
    chat = ChatHistory.query.filter_by(user_id=user.id, conversation_id=conversation_id).first()

    if not chat:
        return jsonify({"error": "Chat not found"}), 404

    return jsonify({
        "conversation_id": chat.conversation_id,
        "title": chat.title,
        "message": chat.message,
        "response": chat.response,
        "timestamp": chat.timestamp.strftime('%Y-%m-%d %H:%M:%S')
    }), 200

# Get all chat history for logged-in user
@app.route('/api/chat_history', methods=['GET'])
def get_chat_history():
    if 'user_id' not in session:
        return jsonify({"message": "Unauthorized, please log in"}), 401

    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({"message": "User not found"}), 404

    chats = ChatHistory.query.filter_by(user_id=user.id).all()

    chat_history = [
        {
            "conversation_id": chat.conversation_id,
            "title": chat.title,
            "message": chat.message,
            "response": chat.response,
            "timestamp": chat.timestamp.strftime('%Y-%m-%d %H:%M:%S')
        }
        for chat in chats
    ]

    return jsonify({"chat_history": chat_history})

if __name__ == '__main__':
    app.run(debug=True)
