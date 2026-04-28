from flask import Flask, render_template, jsonify, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_login import (LoginManager, UserMixin, login_user,
                         logout_user, login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime
from dotenv import load_dotenv
import requests, time, os

load_dotenv()

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'kinetix-secret-2026')

# Render/Heroku inject DATABASE_URL as postgres:// or postgresql://
# We need postgresql+psycopg:// for the psycopg v3 driver
_db_url = os.environ.get('DATABASE_URL', 'postgresql+psycopg://postgres:joetel88@localhost/kinetix_loan')
if _db_url.startswith('postgres://'):
    _db_url = _db_url.replace('postgres://', 'postgresql+psycopg://', 1)
elif _db_url.startswith('postgresql://') and '+' not in _db_url.split('://')[0]:
    _db_url = _db_url.replace('postgresql://', 'postgresql+psycopg://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = _db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)

COINS = ['USDT', 'USDC', 'CAD', 'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'TRX', 'LTC', 'DOGE']

# ── Models ────────────────────────────────────────────────
class User(db.Model, UserMixin):
    __tablename__ = 'users'
    id            = db.Column(db.Integer, primary_key=True)
    firstname     = db.Column(db.String(100), nullable=False)
    lastname      = db.Column(db.String(100), nullable=False)
    username      = db.Column(db.String(80),  unique=True, nullable=False)
    email         = db.Column(db.String(120), unique=True, nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    is_admin      = db.Column(db.Boolean, default=False)
    loan_limit    = db.Column(db.Float,   default=350000.0)
    profile_pic   = db.Column(db.String(256), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)
    assets        = db.relationship('UserAsset', backref='user',
                                    lazy=True, cascade='all, delete-orphan')

    def set_password(self, pw):
        self.password_hash = generate_password_hash(pw)

    def check_password(self, pw):
        return check_password_hash(self.password_hash, pw)


class UserAsset(db.Model):
    __tablename__ = 'user_assets'
    id      = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    coin    = db.Column(db.String(10), nullable=False)
    balance = db.Column(db.Float, default=0.0)
    __table_args__ = (db.UniqueConstraint('user_id', 'coin'),)


@login_manager.user_loader
def load_user(uid):
    return User.query.get(int(uid))

@login_manager.unauthorized_handler
def unauthorized():
    return redirect(url_for('home'))

# ── Helpers ───────────────────────────────────────────────
def seed_coins(user_id):
    for coin in COINS:
        exists = UserAsset.query.filter_by(user_id=user_id, coin=coin).first()
        if not exists:
            db.session.add(UserAsset(user_id=user_id, coin=coin, balance=0.0))
    db.session.commit()


def get_or_create_asset(user_id, coin):
    asset = UserAsset.query.filter_by(user_id=user_id, coin=coin).first()
    if not asset:
        asset = UserAsset(user_id=user_id, coin=coin, balance=0.0)
        db.session.add(asset)
        db.session.commit()
    return asset


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated


def seed_admin():
    if not User.query.filter_by(username='admin').first():
        admin = User(firstname='Admin', lastname='Kinetix',
                     username='admin', email='admin@kinetix.com',
                     is_admin=True, loan_limit=999999.0)
        admin.set_password('joetel88')
        db.session.add(admin)
        db.session.commit()
        seed_coins(admin.id)


# ── Prices cache ──────────────────────────────────────────
COINGECKO_IDS = [
    'bitcoin', 'ethereum', 'solana', 'binancecoin', 'ripple',
    'tron', 'litecoin', 'dogecoin', 'cardano', 'tether', 'cad-coin'
]
_prices_cache = []
_cache_ts = 0
CACHE_TTL = 13

def _ensure_prices():
    """Fetch from CoinGecko if cache is empty or stale."""
    global _prices_cache, _cache_ts
    now = time.time()
    if _prices_cache and (now - _cache_ts) < CACHE_TTL:
        return
    try:
        ids = ','.join(COINGECKO_IDS)
        url = (
            'https://api.coingecko.com/api/v3/coins/markets'
            f'?vs_currency=usd&ids={ids}'
            '&order=market_cap_desc&sparkline=false'
            '&price_change_percentage=24h'
        )
        data = requests.get(url, timeout=10).json()
        if isinstance(data, list) and data:
            data = [c for c in data if c.get('current_price')]
            _prices_cache = data
            _cache_ts = now
    except Exception:
        pass

# ── Public routes ─────────────────────────────────────────
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/markets')
def markets():
    return render_template('markets.html')

@app.route('/api/prices')
def api_prices():
    _ensure_prices()
    return jsonify(_prices_cache)


# ── Auth routes ───────────────────────────────────────────
@app.route('/login', methods=['POST'])
def login():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        login_user(user)
        return redirect(url_for('admin_dashboard') if user.is_admin else url_for('dashboard'))
    return render_template('home.html', login_error='Invalid username or password.')


@app.route('/register', methods=['POST'])
def register():
    firstname = request.form.get('firstname', '').strip()
    lastname  = request.form.get('lastname',  '').strip()
    email     = request.form.get('email',     '').strip()
    username  = request.form.get('username',  '').strip()
    password  = request.form.get('password',  '')
    confirm   = request.form.get('confirm_password', '')

    if password != confirm:
        return render_template('home.html', register_error='Passwords do not match.', open_register=True)
    if User.query.filter_by(username=username).first():
        return render_template('home.html', register_error='Username already taken.', open_register=True)
    if email and User.query.filter_by(email=email).first():
        return render_template('home.html', register_error='Email already registered.', open_register=True)

    user = User(firstname=firstname, lastname=lastname,
                username=username, email=email or None)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    seed_coins(user.id)
    login_user(user)
    return redirect(url_for('dashboard'))


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('home'))

# ── User dashboard ────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    assets = {a.coin: a.balance for a in current_user.assets}
    return render_template('dashboard.html', user=current_user,
                           assets=assets, coins=COINS)


# ── Profile picture ──────────────────────────────────────
@app.route('/upload-avatar', methods=['POST'])
@login_required
def upload_avatar():
    file = request.files.get('avatar')
    if not file or file.filename == '':
        return redirect(url_for('dashboard'))
    if not allowed_file(file.filename):
        return redirect(url_for('dashboard'))

    ext      = file.filename.rsplit('.', 1)[1].lower()
    filename = f"user_{current_user.id}.{ext}"
    save_dir = os.path.join(app.root_path, 'static', 'avatars')
    os.makedirs(save_dir, exist_ok=True)
    file.save(os.path.join(save_dir, filename))

    current_user.profile_pic = filename
    db.session.commit()
    return redirect(url_for('dashboard'))


# ── Admin routes ──────────────────────────────────────────
@app.route('/admin')
@login_required
@admin_required
def admin_dashboard():
    users = (User.query.filter_by(is_admin=False)
             .order_by(User.created_at.desc()).all())
    return render_template('admin.html', users=users, coins=COINS)


@app.route('/admin/assets', methods=['POST'])
@login_required
@admin_required
def admin_assets():
    username = request.form.get('username', '').strip()
    coin     = request.form.get('coin', '').strip().upper()
    mode     = request.form.get('mode', 'set')
    try:
        amount = float(request.form.get('amount', 0))
    except ValueError:
        amount = 0.0

    users = User.query.filter_by(is_admin=False).order_by(User.created_at.desc()).all()
    user  = User.query.filter_by(username=username).first()
    if not user:
        return render_template('admin.html', users=users, coins=COINS,
                               message=f"User '{username}' not found.", success=False)

    asset = get_or_create_asset(user.id, coin)
    asset.balance = amount if mode == 'set' else max(0.0, asset.balance + amount)
    db.session.commit()

    return render_template('admin.html', users=users, coins=COINS,
                           message=f"{coin} balance for '{username}' updated to {asset.balance:,.4f}.",
                           success=True)


@app.route('/admin/user/<int:user_id>/update', methods=['POST'])
@login_required
@admin_required
def admin_update_user(user_id):
    user = User.query.get_or_404(user_id)
    try:
        user.loan_limit = float(request.form.get('loan_limit', user.loan_limit))
    except ValueError:
        pass
    db.session.commit()
    return redirect(url_for('admin_dashboard'))


# ── Dashboard API routes ──────────────────────────────────
TICKER_COINS = ['BTC', 'ETH', 'SOL', 'XRP']
COINGECKO_TO_SYMBOL = {
    'bitcoin': 'BTC', 'ethereum': 'ETH', 'solana': 'SOL',
    'binancecoin': 'BNB', 'ripple': 'XRP', 'tron': 'TRX',
    'litecoin': 'LTC', 'dogecoin': 'DOGE', 'tether': 'USDT',
    'usd-coin': 'USDC', 'cad-coin': 'CAD',
}

def _prices_by_symbol():
    return {COINGECKO_TO_SYMBOL[c['id']]: c
            for c in _prices_cache
            if c.get('id') in COINGECKO_TO_SYMBOL}


@app.route('/api/ticker_snapshot')
def api_ticker_snapshot():
    _ensure_prices()
    by_sym = _prices_by_symbol()
    result = {}
    for sym in TICKER_COINS:
        c = by_sym.get(sym)
        if c:
            result[sym]          = c.get('current_price', 0)
            result[sym + '_CHG'] = c.get('price_change_percentage_24h', 0)
    return jsonify(result)


@app.route('/api/markets')
def api_markets():
    _ensure_prices()
    by_sym = _prices_by_symbol()
    result = []
    for sym in COINS:
        c = by_sym.get(sym)
        if c:
            result.append({
                'symbol':       sym,
                'name':         c.get('name', sym),
                'price':        c.get('current_price', 0),
                'change':       c.get('price_change_percentage_24h', 0),
                'high':         c.get('high_24h', 0),
                'low':          c.get('low_24h', 0),
                'market_cap':   c.get('market_cap', 0),
                'total_volume': c.get('total_volume', 0),
                'volume':       c.get('total_volume', 0),
                'image':        c.get('image', ''),
            })
    return jsonify(result)


@app.route('/api/assets')
@login_required
def api_assets():
    by_sym = _prices_by_symbol()
    CAD_RATE = 1.36
    assets_list = []
    total_usd = 0.0
    cad_balance = 0.0
    for asset in current_user.assets:
        c = by_sym.get(asset.coin)
        price_usd = c.get('current_price', 0) if c else 0
        value_usd = asset.balance * price_usd
        total_usd += value_usd
        if asset.coin == 'CAD':
            cad_balance = asset.balance
        assets_list.append({
            'coin':      asset.coin,
            'amount':    asset.balance,
            'price_usd': price_usd,
            'value_usd': value_usd,
            'value_cad': value_usd * CAD_RATE,
        })
    return jsonify({
        'total_cad':     total_usd * CAD_RATE,
        'available_cad': cad_balance,
        'assets':        assets_list,
    })


@app.route('/api/orders')
@login_required
def api_orders():
    return jsonify([])


@app.route('/api/transactions')
@login_required
def api_transactions():
    return jsonify([])


with app.app_context():
    db.create_all()
    seed_admin()

if __name__ == '__main__':
    app.run(debug=True)
