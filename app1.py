

from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import URLSafeTimedSerializer
import requests
import hashlib
import datetime
import uuid
# Initialize Flask app and SQLAlchemy
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key_here'  # Change this to a random secret key
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'  # Use SQLite for simplicity
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Initialize serializer for token generation
s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# API URL and Key
api_url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash-latest:generateContent"
api_key = "AIzaSyBUseAwUtOB_E0wW3LsNCQ43OhhFTzcmJQ"  # Replace with your actual API key

# User model
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)  # Store hashed password
    subscription_plan = db.Column(db.String(50), nullable=True)  # e.g., "unlimited", "1-month", "6-month"
    subscription_expiry = db.Column(db.Date, nullable=True)  # Expiry date for subscription
    token_count = db.Column(db.Integer, default=5)  # 5 tokens for non-subscribed users

    def set_password(self, password):
        self.password = hashlib.sha256(password.encode()).hexdigest()  # Hashing the password

    def check_password(self, password):
        return self.password == hashlib.sha256(password.encode()).hexdigest()

# Chat history model
class ChatHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    conversation_id = db.Column(db.String(100), nullable=False)  # New field for grouping conversations
    message = db.Column(db.String(500), nullable=False)
    response = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    user = db.relationship('User', backref=db.backref('chats', lazy=True))

# Create all tables within the app context
with app.app_context():
    db.create_all()  # Create database tables
    # db.drop_all()
# Route for home page
@app.route('/')
def index():
    return render_template('index.html')

# Route for signup page
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()

        if user:
            return 'Email already exists, please log in.'

        new_user = User(email=email)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('signup.html')

# Route for login page
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()

        if user and user.check_password(password):
            session['user_id'] = user.id
            session['conversation_id'] = str(uuid.uuid4())  # New conversation ID for each login
            return redirect(url_for('chat'))
        else:
            return 'Invalid credentials'

    return render_template('login.html')
# Route for logout
@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('index'))

# Chat page (Authenticated users can access)
@app.route('/chat', methods=['GET', 'POST'])
def chat():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])

    if request.method == 'POST':
        user_input = request.form['user_input']

        # Generate a chat title based on the first message, limited to 50 characters
        if 'conversation_id' not in session:
            session['conversation_id'] = str(uuid.uuid4())
            session['chat_title'] = user_input[:50]  # Use first message as chat title

        # Check if user has a subscription or sufficient tokens
        if user.subscription_plan == 'unlimited' or (user.subscription_expiry and user.subscription_expiry > datetime.date.today()):
            pass
        else:
            if user.token_count > 0:
                user.token_count -= 1
                db.session.commit()
            else:
                return jsonify({'ai_response': 'Token limit reached. Please subscribe for unlimited access.'})

        # Send request to Gemini API
        headers = {"Content-Type": "application/json"}
        data = {"contents": [{"parts": [{"text": user_input}]}]}
        response = requests.post(f"{api_url}?key={api_key}", headers=headers, json=data)

        if response.status_code == 200:
            content = response.json()
            ai_response = content['candidates'][0]['content']['parts'][0]['text']

            # Save chat history with conversation_id
            new_chat = ChatHistory(
                user_id=user.id,
                conversation_id=session['conversation_id'],
                message=user_input,
                response=ai_response
            )
            db.session.add(new_chat)
            db.session.commit()

            # Limit AI response to 50 words
            words = ai_response.split()[:50]
            limited_response = ' '.join(words)

            return jsonify({'ai_response': limited_response})

        else:
            return jsonify({'ai_response': 'Error occurred, please try again.'})

    # Fetch user's chat titles for display
    chat_titles = ChatHistory.query.with_entities(ChatHistory.conversation_id, ChatHistory.message) \
                    .filter_by(user_id=user.id).distinct(ChatHistory.conversation_id).all()
    
    # Generate titles (first message limited to 50 characters)
    chat_titles_display = [{"conversation_id": chat[0], "title": chat[1][:50]} for chat in chat_titles]

    return render_template('chat.html', user=user, chat_titles=chat_titles_display)


@app.route('/conversation/<conversation_id>')
def conversation(conversation_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    chats = ChatHistory.query.filter_by(user_id=user.id, conversation_id=conversation_id).all()

    return render_template('conversation.html', chats=chats)


# Route to get chat history
@app.route('/get_history')
def get_history():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user = User.query.get(session['user_id'])
    chat_history = ChatHistory.query.filter_by(user_id=user.id).all()

    history = [{"message": chat.message, "response": chat.response} for chat in chat_history]
    return jsonify({'history': history})

if __name__ == '__main__':
    app.run(debug=True)
