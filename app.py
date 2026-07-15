import os
import csv
from datetime import date, datetime, time, timedelta
from io import BytesIO, StringIO
from urllib.parse import urlparse, urlunparse

from flask import Flask, Response, jsonify, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
import razorpay

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv()

app = Flask(__name__)
os.makedirs(app.instance_path, exist_ok=True)

database_url = os.getenv('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    parsed_url = urlparse(database_url)
    database_url = urlunparse(parsed_url._replace(scheme='postgresql'))

app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-me')
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or f"sqlite:///{os.path.join(app.instance_path, 'project.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

RAZORPAY_KEY_ID = os.getenv('RAZORPAY_KEY_ID', '')
RAZORPAY_KEY_SECRET = os.getenv('RAZORPAY_KEY_SECRET', '')
razorpay_client = (
    razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
    if RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET
    else None
)

# --- DATABASE MODELS ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user') # 'user' or 'admin'
    orders = db.relationship('Order', backref='customer', lazy=True)

    def __init__(self, name, email, password_hash, role='user'):
        self.name = name
        self.email = email
        self.password_hash = password_hash
        self.role = role

class MenuItem(db.Model):
    __allow_unmapped__ = True

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False) # 'breakfast', 'tea', 'drinks'
    price = db.Column(db.Float, nullable=False)
    image_url = db.Column(db.String(500))
    is_available = db.Column(db.Boolean, default=True)
    stock_quantity = db.Column(db.Integer, nullable=False, default=25)
    quantity: int
    subtotal: float

    def __init__(self, name, category, price, image_url=None, is_available=True, stock_quantity=25):
        self.name = name
        self.category = category
        self.price = price
        self.image_url = image_url
        self.is_available = is_available
        self.stock_quantity = stock_quantity

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50), nullable=False, default='COD') # 'upi' or 'cod'
    schedule_time = db.Column(db.String(10)) # Time like '12:00'
    status = db.Column(db.String(20), default='Pending') # Pending, Completed, Cancelled, Pending Payment
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Razorpay Details
    rzp_order_id = db.Column(db.String(100), nullable=True)
    rzp_payment_id = db.Column(db.String(100), nullable=True)
    rzp_signature = db.Column(db.String(256), nullable=True)
    
    # structured items
    items = db.relationship('OrderItem', backref='parent_order', lazy=True, cascade="all, delete-orphan")

    def __init__(
        self,
        user_id,
        total_amount,
        payment_method='cod',
        schedule_time=None,
        status='Pending',
        created_at=None,
        rzp_order_id=None,
        rzp_payment_id=None,
        rzp_signature=None,
    ):
        self.user_id = user_id
        self.total_amount = total_amount
        self.payment_method = payment_method
        self.schedule_time = schedule_time
        self.status = status
        self.created_at = created_at or datetime.utcnow()
        self.rzp_order_id = rzp_order_id
        self.rzp_payment_id = rzp_payment_id
        self.rzp_signature = rzp_signature

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    menu_item_id = db.Column(db.Integer, db.ForeignKey('menu_item.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    price_at_order = db.Column(db.Float, nullable=False)
    
    menu_item = db.relationship('MenuItem', backref='sold_in_orders', lazy=True)

    def __init__(self, order_id, menu_item_id, quantity=1, price_at_order=0):
        self.order_id = order_id
        self.menu_item_id = menu_item_id
        self.quantity = quantity
        self.price_at_order = price_at_order

with app.app_context():
    db.create_all()

    inspector = inspect(db.engine)
    order_columns = [column['name'] for column in inspector.get_columns('order')]
    if 'created_at' not in order_columns:
        with db.engine.begin() as connection:
            try:
                connection.execute(text('ALTER TABLE "order" ADD COLUMN created_at DATETIME'))
            except Exception as exc:
                if 'duplicate column' not in str(exc).lower():
                    raise
            connection.execute(text('UPDATE "order" SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL'))

    menu_columns = [column['name'] for column in inspector.get_columns('menu_item')]
    if 'stock_quantity' not in menu_columns:
        with db.engine.begin() as connection:
            try:
                connection.execute(text('ALTER TABLE menu_item ADD COLUMN stock_quantity INTEGER DEFAULT 25 NOT NULL'))
            except Exception as exc:
                if 'duplicate column' not in str(exc).lower():
                    raise

# --- HELPER FUNCTIONS ---
def get_current_user():
    if 'user_id' in session:
        return db.session.get(User, session['user_id'])
    return None

def normalize_role(role):
    return role if role in {'user', 'admin'} else 'user'

def normalize_payment_method(payment_method):
    return payment_method if payment_method in {'upi', 'cod'} else None

def parse_price(value):
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if price >= 0 else None

def parse_stock(value):
    try:
        stock = int(value)
    except (TypeError, ValueError):
        return None
    return stock if stock >= 0 else None

def get_cart():
    cart = session.get('cart', {})
    return cart if isinstance(cart, dict) else {}

def calculate_cart_items(cart):
    items = []
    total = 0.0
    stale_ids = []

    for i_id_str, qty in cart.items():
        try:
            item_id = int(i_id_str)
            quantity = int(qty)
        except (TypeError, ValueError):
            stale_ids.append(i_id_str)
            continue

        if quantity <= 0:
            stale_ids.append(i_id_str)
            continue

        item = db.session.get(MenuItem, item_id)
        if not item:
            stale_ids.append(i_id_str)
            continue

        item.quantity = quantity
        item.subtotal = float(item.price) * quantity
        items.append(item)
        total += item.subtotal

    if stale_ids:
        for stale_id in stale_ids:
            cart.pop(stale_id, None)
        session['cart'] = cart
        session.modified = True

    return items, total

def parse_report_date(value):
    if not value:
        return date.today()
    try:
        return date.fromisoformat(value)
    except ValueError:
        return date.today()

def get_daily_sales_report(selected_date):
    start_at = datetime.combine(selected_date, time.min)
    end_at = start_at + timedelta(days=1)
    counted_statuses = ['Pending', 'Preparing', 'Completed']

    orders = (
        Order.query
        .filter(Order.created_at >= start_at, Order.created_at < end_at)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .all()
    )
    counted_orders = [order for order in orders if order.status in counted_statuses]
    completed_orders = [order for order in orders if order.status == 'Completed']

    item_sales = (
        db.session.query(
            MenuItem.name,
            db.func.sum(OrderItem.quantity).label('quantity'),
            db.func.sum(OrderItem.quantity * OrderItem.price_at_order).label('revenue'),
        )
        .join(OrderItem, MenuItem.id == OrderItem.menu_item_id)
        .join(Order, Order.id == OrderItem.order_id)
        .filter(
            Order.created_at >= start_at,
            Order.created_at < end_at,
            Order.status.in_(counted_statuses),
        )
        .group_by(MenuItem.name)
        .order_by(db.desc('quantity'))
        .all()
    )

    return {
        'date': selected_date,
        'orders': orders,
        'item_sales': item_sales,
        'total_orders': len(counted_orders),
        'total_items_sold': sum(sum(item.quantity for item in order.items) for order in counted_orders),
        'gross_revenue': sum(float(order.total_amount) for order in counted_orders),
        'completed_revenue': sum(float(order.total_amount) for order in completed_orders),
        'cancelled_orders': len([order for order in orders if order.status == 'Cancelled']),
        'pending_payment_orders': len([order for order in orders if order.status == 'Pending Payment']),
    }

def build_order_filters(args):
    status = args.get('status', '').strip()
    payment = args.get('payment', '').strip()
    selected_date = args.get('date', '').strip()
    search = args.get('q', '').strip()

    query = Order.query.join(User)

    if status:
        query = query.filter(Order.status == status)
    if payment:
        query = query.filter(Order.payment_method == payment)
    if selected_date:
        try:
            parsed_date = date.fromisoformat(selected_date)
            start_at = datetime.combine(parsed_date, time.min)
            query = query.filter(Order.created_at >= start_at, Order.created_at < start_at + timedelta(days=1))
        except ValueError:
            selected_date = ''
    if search:
        if search.isdigit():
            query = query.filter(db.or_(Order.id == int(search), User.name.ilike(f'%{search}%')))
        else:
            query = query.filter(User.name.ilike(f'%{search}%'))

    filters = {
        'status': status,
        'payment': payment,
        'date': selected_date,
        'q': search,
    }
    return query, filters

def decrement_stock_for_order(order):
    for order_item in order.items:
        item = order_item.menu_item
        item.stock_quantity = max(0, int(item.stock_quantity or 0) - int(order_item.quantity or 0))
        if item.stock_quantity == 0:
            item.is_available = False

def build_daily_report_csv(report):
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(['CampusBite Daily Sales Report'])
    writer.writerow(['Date', report['date'].isoformat()])
    writer.writerow([])
    writer.writerow(['Metric', 'Value'])
    writer.writerow(['Total Orders', report['total_orders']])
    writer.writerow(['Items Sold', report['total_items_sold']])
    writer.writerow(['Gross Revenue', f'{report["gross_revenue"]:.2f}'])
    writer.writerow(['Completed Revenue', f'{report["completed_revenue"]:.2f}'])
    writer.writerow(['Cancelled Orders', report['cancelled_orders']])
    writer.writerow(['Pending Payment Orders', report['pending_payment_orders']])
    writer.writerow([])
    writer.writerow(['Item', 'Quantity Sold', 'Revenue'])
    for item_name, quantity, revenue in report['item_sales']:
        writer.writerow([item_name, int(quantity or 0), f'{float(revenue or 0):.2f}'])
    writer.writerow([])
    writer.writerow(['Order ID', 'Time', 'Customer', 'Status', 'Payment', 'Total', 'Items'])
    for order in report['orders']:
        items = '; '.join(f'{item.menu_item.name} x{item.quantity}' for item in order.items)
        writer.writerow([
            order.id,
            order.created_at.strftime('%H:%M'),
            order.customer.name,
            order.status,
            order.payment_method.upper() if order.payment_method else 'N/A',
            f'{order.total_amount:.2f}',
            items,
        ])
    return output.getvalue()

def escape_pdf_text(value):
    return str(value).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')

def build_simple_pdf(title, lines):
    page_size = 44
    pages = []
    remaining_lines = list(lines)

    while remaining_lines or not pages:
        page_lines = remaining_lines[:page_size]
        remaining_lines = remaining_lines[page_size:]
        pages.append(page_lines)

    objects = [
        '1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n',
        '3 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n',
    ]
    page_object_ids = []

    next_object_id = 4
    for page_index, page_lines in enumerate(pages, start=1):
        page_id = next_object_id
        content_id = next_object_id + 1
        next_object_id += 2
        page_object_ids.append(page_id)

        content_lines = [
            'BT',
            '/F1 18 Tf',
            '50 790 Td',
            f'({escape_pdf_text(title)}) Tj',
            '/F1 9 Tf',
            '0 -18 Td',
            f'(Page {page_index} of {len(pages)}) Tj',
            '/F1 10 Tf',
            '0 -26 Td',
        ]
        for line in page_lines:
            content_lines.append(f'({escape_pdf_text(line)}) Tj')
            content_lines.append('0 -16 Td')
        content_lines.append('ET')

        stream = '\n'.join(content_lines)
        objects.append(
            f'{page_id} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] '
            f'/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >> endobj\n'
        )
        objects.append(
            f'{content_id} 0 obj << /Length {len(stream.encode("latin-1", "replace"))} >> stream\n'
            f'{stream}\nendstream endobj\n'
        )

    kids = ' '.join(f'{page_id} 0 R' for page_id in page_object_ids)
    objects.insert(1, f'2 0 obj << /Type /Pages /Kids [{kids}] /Count {len(page_object_ids)} >> endobj\n')

    pdf = BytesIO()
    pdf.write(b'%PDF-1.4\n')
    offsets = [0]
    for obj in objects:
        offsets.append(pdf.tell())
        pdf.write(obj.encode('latin-1', 'replace'))
    xref_at = pdf.tell()
    pdf.write(f'xref\n0 {len(objects) + 1}\n'.encode('ascii'))
    pdf.write(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        pdf.write(f'{offset:010d} 00000 n \n'.encode('ascii'))
    pdf.write(f'trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF'.encode('ascii'))
    return pdf.getvalue()

@app.context_processor
def inject_payment_config():
    cart = get_cart()
    return {
        'razorpay_enabled': bool(razorpay_client),
        'cart_count': sum(cart.values()) if cart else 0,
    }

def init_mock_menu():
    """Adds some initial data if the database is empty."""
    if MenuItem.query.count() == 0:
        items = [
            MenuItem(name='Poha', category='breakfast', price=25, stock_quantity=40, image_url='https://madhurasrecipe.com/wp-content/uploads/2023/07/Kande-Pohe-Featured.jpg'),
            MenuItem(name='Upma', category='breakfast', price=20, stock_quantity=35, image_url='https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSdRC__zljPJy8xKamU7NyUnb9k18zyAeNvwIhBTM6RihMOUH9WGBYPV1mrbELSl4xEK9o6tQn8m9vFSwikVxU-3gAsNwjc7m50tAsiIQ&s=10'),
            MenuItem(name='Idli', category='breakfast', price=30, stock_quantity=50, image_url='https://c.ndtvimg.com/2019-03/g49icpdk_world-idli-day-idli-generic_625x300_29_March_19.jpg'),
            MenuItem(name='Cappuccino', category='tea', price=35, stock_quantity=30, image_url='https://cdn2.foodviva.com/static-content/food-images/tea-recipes/milk-tea-recipe/milk-tea-recipe.jpg'),
            MenuItem(name='Cold Coffee', category='drinks', price=60, stock_quantity=25, image_url='https://images.unsplash.com/photo-1581006852262-e4307cf6283a?w=400')
        ]
        db.session.bulk_save_objects(items)
        db.session.commit()

with app.app_context():
    init_mock_menu()

# --- ROUTES ---

@app.route('/')
def home():
    init_mock_menu()
    user = get_current_user()
    
    query = request.args.get('q', '')
    if query:
        breakfast = MenuItem.query.filter(MenuItem.category == 'breakfast', MenuItem.name.contains(query)).all()
        tea = MenuItem.query.filter(MenuItem.category == 'tea', MenuItem.name.contains(query)).all()
        drinks = MenuItem.query.filter(MenuItem.category == 'drinks', MenuItem.name.contains(query)).all()
    else:
        breakfast = MenuItem.query.filter_by(category='breakfast').all()
        tea = MenuItem.query.filter_by(category='tea').all()
        drinks = MenuItem.query.filter_by(category='drinks').all()

    # Calculate cart count (total items)
    cart = get_cart()
    cart_count = sum(cart.values())
    return render_template('index.html', user=user, breakfast=breakfast, tea=tea, drinks=drinks, cart_count=cart_count, search_query=query)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        role = normalize_role(request.form.get('role'))

        if not name or not email or not password:
            flash('Please fill in all required fields.', 'error')
            return redirect(url_for('register'))

        name = name.strip()
        email = email.strip().lower()

        if password != confirm_password:
            flash('Passwords do not match!', 'error')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered!', 'error')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        new_user = User(name=name, email=email, password_hash=hashed_password, role=role)
        db.session.add(new_user)
        db.session.commit()
        
        flash('Registration successful! Please login.', 'success')
        return redirect(url_for('login'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        role = request.form.get('role') # from form mapping

        user = User.query.filter_by(email=email).first()
        
        if user and check_password_hash(user.password_hash, password):
            if role and user.role != role:
                flash('Incorrect role selected', 'error')
                return redirect(url_for('login'))
                
            session['user_id'] = user.id
            session['role'] = user.role
            
            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('home'))
        else:
            flash('Invalid email or password', 'error')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

# --- CUSTOMER CART AND ORDERS ---

@app.route('/add_to_cart/<int:item_id>')
def add_to_cart(item_id):
    item = db.session.get(MenuItem, item_id)
    if not item or not item.is_available or int(item.stock_quantity or 0) <= 0:
        flash(f"Sorry, {item.name if item else 'Item'} is currently sold out.", "error")
        return redirect(url_for('home'))

    if 'cart' not in session or not isinstance(session['cart'], dict):
        session['cart'] = {}
    
    cart = session['cart']
    item_id_str = str(item_id)
    current_qty = int(cart.get(item_id_str, 0))
    if current_qty >= int(item.stock_quantity or 0):
        flash(f"Only {item.stock_quantity} {item.name} left in stock.", "error")
        return redirect(url_for('home'))

    if item_id_str in cart:
        cart[item_id_str] += 1
    else:
        cart[item_id_str] = 1
    session['cart'] = cart
    session.modified = True
    return redirect(url_for('home'))

@app.route('/update_cart/<int:item_id>/<string:action>')
def update_cart_quantity(item_id, action):
    if 'cart' not in session or not isinstance(session['cart'], dict):
        return redirect(url_for('view_cart'))
    
    cart = session['cart']
    item_id_str = str(item_id)
    
    if item_id_str in cart:
        if action == 'plus':
            item = db.session.get(MenuItem, item_id)
            if item and cart[item_id_str] >= int(item.stock_quantity or 0):
                flash(f"Only {item.stock_quantity} available.", "error")
                return redirect(url_for('view_cart'))
            cart[item_id_str] += 1
        elif action == 'minus':
            cart[item_id_str] -= 1
            if cart[item_id_str] <= 0:
                del cart[item_id_str]
        elif action == 'remove':
            del cart[item_id_str]
            
    session['cart'] = cart
    session.modified = True
    return redirect(url_for('view_cart'))

@app.route('/cart')
def view_cart():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
        
    cart = get_cart()
    items, total = calculate_cart_items(cart)
            
    return render_template('cart.html', items=items, total=total, user=user)

@app.route('/clear_cart')
def clear_cart():
    session.pop('cart', None)
    return redirect(url_for('view_cart'))

@app.route('/payment', methods=['GET', 'POST'])
def payment():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
        
    cart = get_cart()
    if not cart:
        return redirect(url_for('home'))

    if request.method == 'POST':
        # Process order
        payment_method = normalize_payment_method(request.form.get('payment'))
        schedule_time = request.form.get('schedule_time') or None

        if not payment_method:
            flash("Please choose a valid payment method.", "error")
            return redirect(url_for('payment'))
        
        # Calculate total and verify availability
        total = 0
        order_items_to_create = []
        
        for i_id_str, qty in cart.items():
            item = db.session.get(MenuItem, int(i_id_str))
            if item:
                if not item.is_available or int(item.stock_quantity or 0) < int(qty):
                    flash(f"Sorry, {item.name} is currently sold out.", "error")
                    return redirect(url_for('view_cart'))
                
                price = float(item.price)
                total += price * qty
                order_items_to_create.append({
                    'menu_item_id': item.id,
                    'quantity': qty,
                    'price_at_order': price
                })

        if not order_items_to_create:
            flash("Your cart is empty or contains unavailable items.", "error")
            return redirect(url_for('view_cart'))
        
        # Determine status
        initial_status = 'Pending Payment' if payment_method == 'upi' else 'Pending'
        
        # Create Order
        new_order = Order(
            user_id=user.id, 
            total_amount=total, 
            payment_method=payment_method, 
            schedule_time=schedule_time,
            status=initial_status
        )
        
        if payment_method == 'upi':
            if not razorpay_client:
                flash("Online payments are not configured yet. Please use cash on delivery.", "error")
                return redirect(url_for('payment', payment='cod'))

            # Create Razorpay Order
            data = {
                "amount": int(total * 100), # Amount in paise
                "currency": "INR",
                "payment_capture": "1"
            }
            try:
                razorpay_order = razorpay_client.order.create(data=data)
                new_order.rzp_order_id = razorpay_order['id']
            except Exception:
                flash("Error creating payment order. Please try again.", "error")
                return redirect(url_for('payment'))

        db.session.add(new_order)
        db.session.flush()
        
        for item_data in order_items_to_create:
            oi = OrderItem(order_id=new_order.id, **item_data)
            db.session.add(oi)
            
        db.session.commit()

        if payment_method == 'cod':
            decrement_stock_for_order(new_order)
            db.session.commit()
        
        if payment_method == 'upi':
            # Pass details for frontend to trigger Razorpay
            return render_template('payment_gateway.html', 
                                 order=new_order, 
                                 rzp_order_id=new_order.rzp_order_id,
                                 rzp_key_id=RAZORPAY_KEY_ID,
                                 user=user)
        
        session.pop('cart', None)
        return redirect(url_for('order_success', order_id=new_order.id))
        
    # GET request
    schedule_time = request.args.get('order-time') or None
    preselected = request.args.get('payment', '')
    _, total = calculate_cart_items(cart)
    return render_template('payment.html', user=user, total=total, schedule_time=schedule_time, preselected=preselected)

@app.route('/verify-payment', methods=['POST'])
def verify_payment():
    if not razorpay_client:
        return jsonify({'status': 'failure', 'error': 'Online payments are not configured.'}), 400

    data = request.get_json(silent=True) or {}
    rzp_payment_id = data.get('razorpay_payment_id')
    rzp_order_id = data.get('razorpay_order_id')
    rzp_signature = data.get('razorpay_signature')

    if not all([rzp_payment_id, rzp_order_id, rzp_signature]):
        return jsonify({'status': 'failure', 'error': 'Missing payment verification data.'}), 400
    
    params_dict = {
        'razorpay_order_id': rzp_order_id,
        'razorpay_payment_id': rzp_payment_id,
        'razorpay_signature': rzp_signature
    }
    
    try:
        razorpay_client.utility.verify_payment_signature(params_dict)
        # Payment verified
        order = Order.query.filter_by(rzp_order_id=rzp_order_id).first()
        if order:
            order.status = 'Pending' # Now it moves from 'Pending Payment' to 'Pending' (active)
            order.rzp_payment_id = rzp_payment_id
            order.rzp_signature = rzp_signature
            decrement_stock_for_order(order)
            db.session.commit()
            session.pop('cart', None)
            return jsonify({'status': 'success', 'order_id': order.id})
    except Exception as e:
        return jsonify({'status': 'failure', 'error': str(e)}), 400
    
    return jsonify({'status': 'failure'}), 400

@app.route('/profile')
def profile():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
    return render_template('profile.html', user=user)

@app.route('/order_success')
def order_success():
    order_id = request.args.get('order_id')
    return render_template('order_success.html', user=get_current_user(), order_id=order_id)

@app.route('/orders')
def my_orders():
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
        
    user_orders = Order.query.filter_by(user_id=user.id).all()
    return render_template('orders.html', orders=user_orders, user=user)

# --- ADMIN ROUTES ---

@app.route('/admin')
def admin_dashboard():
    user = get_current_user()
    if not user or user.role != 'admin':
        return redirect(url_for('home'))

    order_query, filters = build_order_filters(request.args)
    orders = order_query.order_by(Order.id.desc()).all()
    active_orders_count = Order.query.filter(Order.status.in_(['Pending', 'Preparing'])).count()
    items_count = MenuItem.query.count()
    
    return render_template(
        'admin.html',
        user=user,
        orders=orders,
        total_orders=len(orders),
        total_items=items_count,
        active_orders=active_orders_count,
        filters=filters,
    )

@app.route('/admin/analytics')
def admin_analytics():
    user = get_current_user()
    if not user or user.role != 'admin':
        return redirect(url_for('home'))
        

    # Total Revenue (Only from Completed orders)
    total_revenue = db.session.query(db.func.sum(Order.total_amount)).filter(Order.status == 'Completed').scalar() or 0
    
    # Popular Items (Exclude Cancelled orders)
    popular_items = db.session.query(
        MenuItem.name, 
        db.func.sum(OrderItem.quantity).label('total_qty')
    ).join(OrderItem).join(Order).filter(Order.status != 'Cancelled').group_by(MenuItem.name).order_by(db.desc('total_qty')).limit(5).all()
    
    # Recent Orders for a mini-table
    recent_orders = Order.query.order_by(Order.id.desc()).limit(10).all()
    report_date = parse_report_date(request.args.get('date'))
    daily_report = get_daily_sales_report(report_date)
    
    return render_template('admin_analytics.html', 
                           user=user, 
                           total_revenue=total_revenue, 
                           popular_items=popular_items,
                           recent_orders=recent_orders,
                           report_date=report_date,
                           daily_report=daily_report)

@app.route('/admin/analytics/daily-report.pdf')
def daily_sales_pdf():
    user = get_current_user()
    if not user or user.role != 'admin':
        return redirect(url_for('home'))

    report_date = parse_report_date(request.args.get('date'))
    report = get_daily_sales_report(report_date)
    lines = [
        f'Date: {report_date.isoformat()}',
        f'Generated at: {datetime.now().strftime("%Y-%m-%d %H:%M")}',
        '',
        f'Total counted orders: {report["total_orders"]}',
        f'Total items sold: {report["total_items_sold"]}',
        f'Gross revenue: INR {report["gross_revenue"]:.2f}',
        f'Completed revenue: INR {report["completed_revenue"]:.2f}',
        f'Cancelled orders: {report["cancelled_orders"]}',
        f'Pending payment orders: {report["pending_payment_orders"]}',
        '',
        'Item-wise sales',
    ]

    if report['item_sales']:
        for item_name, quantity, revenue in report['item_sales']:
            lines.append(f'- {item_name}: {int(quantity or 0)} sold, INR {float(revenue or 0):.2f}')
    else:
        lines.append('- No item sales for this date.')

    lines.extend(['', 'Order details'])
    if report['orders']:
        for order in report['orders']:
            items = ', '.join(f'{item.menu_item.name} x{item.quantity}' for item in order.items)
            lines.append(
                f'#{order.id} | {order.created_at.strftime("%H:%M")} | {order.customer.name} | '
                f'{order.status} | {order.payment_method.upper()} | INR {order.total_amount:.2f} | {items}'
            )
    else:
        lines.append('No orders for this date.')

    pdf_bytes = build_simple_pdf(f'CampusBite Daily Sales Report - {report_date.isoformat()}', lines)
    filename = f'campusbite-daily-sales-{report_date.isoformat()}.pdf'
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )

@app.route('/admin/analytics/daily-report.csv')
def daily_sales_csv():
    user = get_current_user()
    if not user or user.role != 'admin':
        return redirect(url_for('home'))

    report_date = parse_report_date(request.args.get('date'))
    report = get_daily_sales_report(report_date)
    csv_text = build_daily_report_csv(report)
    filename = f'campusbite-daily-sales-{report_date.isoformat()}.csv'
    return Response(
        csv_text,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )

@app.route('/admin/update_order_status/<int:order_id>', methods=['POST'])
def update_order_status(order_id):
    user = get_current_user()
    if user and user.role == 'admin':
        order = db.session.get(Order, order_id)
        if order:
            if order.status == 'Pending':
                order.status = 'Preparing'
            elif order.status == 'Preparing':
                order.status = 'Completed'
            db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/cancel_order/<int:order_id>', methods=['POST'])
def cancel_order(order_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
        
    order = db.session.get(Order, order_id)
    if not order:
        flash("Order not found.", "error")
        return redirect(url_for('home'))
        
    # Logic: Admin can cancel any non-completed order
    # User can only cancel their own 'Pending' order
    if user.role == 'admin':
        if order.status != 'Completed' and order.status != 'Cancelled':
            order.status = 'Cancelled'
            db.session.commit()
            flash(f"Order #{order_id} has been cancelled.", "success")
        elif order.status == 'Cancelled':
            flash("Order is already cancelled.", "info")
        else:
            flash("Cannot cancel a completed order.", "error")
        return redirect(url_for('admin_dashboard'))
    else:
        if order.user_id == user.id:
            if order.status == 'Pending':
                order.status = 'Cancelled'
                db.session.commit()
                flash("Your order has been cancelled.", "success")
            elif order.status == 'Cancelled':
                flash("Order is already cancelled.", "info")
            else:
                flash("Cannot cancel order once preparation has started.", "error")
        else:
            flash("Unauthorized action.", "error")
        return redirect(url_for('my_orders'))

@app.route('/admin/add-item', methods=['GET', 'POST'])
def add_item():
    user = get_current_user()
    if not user or user.role != 'admin':
        return redirect(url_for('home'))
        
    if request.method == 'POST':
        name = request.form.get('name')
        category = request.form.get('category')
        price = parse_price(request.form.get('price'))
        stock_quantity = parse_stock(request.form.get('stock_quantity'))
        image_url = request.form.get('image_url')
        is_available = True if request.form.get('is_available') == 'on' else False

        if not name or category not in {'breakfast', 'tea', 'drinks'} or price is None or stock_quantity is None:
            flash('Please provide a valid name, category, price, and stock quantity.', 'error')
            return redirect(url_for('add_item'))
        
        new_item = MenuItem(
            name=name.strip(),
            category=category,
            price=price,
            image_url=image_url,
            is_available=is_available and stock_quantity > 0,
            stock_quantity=stock_quantity,
        )
        db.session.add(new_item)
        db.session.commit()
        
        flash(f'Successfully added {name} to the menu!', 'success')
        return redirect(url_for('manage_items'))
        
    return render_template('add-item.html', user=user)

@app.route('/admin/edit-item/<int:item_id>', methods=['GET', 'POST'])
def edit_item(item_id):
    user = get_current_user()
    if not user or user.role != 'admin':
        return redirect(url_for('home'))
        
    item = db.session.get(MenuItem, item_id)
    if not item:
        flash("Item not found", "error")
        return redirect(url_for('manage_items'))

    if request.method == 'POST':
        name = request.form.get('name')
        category = request.form.get('category')
        price = parse_price(request.form.get('price'))
        stock_quantity = parse_stock(request.form.get('stock_quantity'))

        if not name or category not in {'breakfast', 'tea', 'drinks'} or price is None or stock_quantity is None:
            flash('Please provide a valid name, category, price, and stock quantity.', 'error')
            return redirect(url_for('edit_item', item_id=item.id))

        item.name = name
        item.category = category
        item.price = price
        item.stock_quantity = stock_quantity
        item.image_url = request.form.get('image_url')
        item.is_available = True if request.form.get('is_available') == 'on' and stock_quantity > 0 else False
        
        db.session.commit()
        flash(f'Successfully updated {item.name}!', 'success')
        return redirect(url_for('manage_items'))
        
    return render_template('edit-item.html', user=user, item=item)

@app.route('/admin/manage-items')
def manage_items():
    user = get_current_user()
    if not user or user.role != 'admin':
        return redirect(url_for('home'))
        
    items = MenuItem.query.all()
    return render_template('manage-items.html', user=user, items=items)

@app.route('/admin/delete-item/<int:item_id>', methods=['POST'])
def delete_item(item_id):
    user = get_current_user()
    if user and user.role == 'admin':
        item = db.session.get(MenuItem, item_id)
        if item:
            name = item.name
            if item.sold_in_orders:
                item.is_available = False
                db.session.commit()
                flash(f'{name} has previous orders, so it was marked sold out instead of deleted.', 'info')
            else:
                db.session.delete(item)
                db.session.commit()
                flash(f'Successfully deleted {name} from the menu.', 'success')
            
    return redirect(url_for('manage_items'))

@app.route('/admin/toggle-availability/<int:item_id>', methods=['POST'])
def toggle_item_availability(item_id):
    user = get_current_user()
    if user and user.role == 'admin':
        item = db.session.get(MenuItem, item_id)
        if item:
            if not item.is_available and int(item.stock_quantity or 0) <= 0:
                flash(f'Add stock before marking {item.name} available.', 'error')
                return redirect(url_for('manage_items'))
            item.is_available = not item.is_available
            db.session.commit()
            status = "available" if item.is_available else "sold out"
            flash(f'{item.name} is now {status}.', 'success')
    return redirect(url_for('manage_items'))

@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_server_error(e):
    db.session.rollback()
    return render_template('500.html'), 500

if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG', '0') == '1')
