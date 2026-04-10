from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
import razorpay
import json

app = Flask(__name__)
app.secret_key = 'super_secret_key_change_in_production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///project.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

# Razorpay Configuration (Replace with your actual keys)
RAZORPAY_KEY_ID = 'rzp_test_SUHtPMDA7ql8tP'
RAZORPAY_KEY_SECRET = 'Val1TCQjEB8ccStgRSDgzErV'
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

# --- DATABASE MODELS ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default='user') # 'user' or 'admin'
    orders = db.relationship('Order', backref='customer', lazy=True)

class MenuItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False) # 'breakfast', 'tea', 'drinks'
    price = db.Column(db.Float, nullable=False)
    image_url = db.Column(db.String(500))
    is_available = db.Column(db.Boolean, default=True)

class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    payment_method = db.Column(db.String(50), nullable=False, default='COD') # 'upi' or 'cod'
    schedule_time = db.Column(db.String(10)) # Time like '12:00'
    status = db.Column(db.String(20), default='Pending') # Pending, Completed, Cancelled, Pending Payment
    
    # Razorpay Details
    rzp_order_id = db.Column(db.String(100), nullable=True)
    rzp_payment_id = db.Column(db.String(100), nullable=True)
    rzp_signature = db.Column(db.String(256), nullable=True)
    
    # structured items
    items = db.relationship('OrderItem', backref='parent_order', lazy=True, cascade="all, delete-orphan")

class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    menu_item_id = db.Column(db.Integer, db.ForeignKey('menu_item.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    price_at_order = db.Column(db.Float, nullable=False)
    
    menu_item = db.relationship('MenuItem', backref='sold_in_orders', lazy=True)

with app.app_context():
    db.create_all()

# --- HELPER FUNCTIONS ---
def get_current_user():
    if 'user_id' in session:
        return User.query.get(session['user_id'])
    return None

def init_mock_menu():
    """Adds some initial data if the database is empty."""
    if MenuItem.query.count() == 0:
        items = [
            MenuItem(name='Poha', category='breakfast', price=25, image_url='https://madhurasrecipe.com/wp-content/uploads/2023/07/Kande-Pohe-Featured.jpg'), # type: ignore
            MenuItem(name='Upma', category='breakfast', price=20, image_url='https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcSdRC__zljPJy8xKamU7NyUnb9k18zyAeNvwIhBTM6RihMOUH9WGBYPV1mrbELSl4xEK9o6tQn8m9vFSwikVxU-3gAsNwjc7m50tAsiIQ&s=10'), # type: ignore
            MenuItem(name='Idli', category='breakfast', price=30, image_url='https://c.ndtvimg.com/2019-03/g49icpdk_world-idli-day-idli-generic_625x300_29_March_19.jpg'), # type: ignore
            MenuItem(name='Cappuccino', category='tea', price=35, image_url='https://cdn2.foodviva.com/static-content/food-images/tea-recipes/milk-tea-recipe/milk-tea-recipe.jpg'), # type: ignore
            MenuItem(name='Cold Coffee', category='drinks', price=60, image_url='https://images.unsplash.com/photo-1581006852262-e4307cf6283a?w=400') # type: ignore
        ]
        db.session.bulk_save_objects(items)
        db.session.commit()

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
    cart = session.get('cart', {})
    if not isinstance(cart, dict):
        cart = {}
    cart_count = sum(cart.values())
    return render_template('index.html', user=user, breakfast=breakfast, tea=tea, drinks=drinks, cart_count=cart_count, search_query=query)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        confirm_password = request.form.get('confirm_password')
        role = request.form.get('role')

        if password != confirm_password:
            flash('Passwords do not match!', 'error')
            return redirect(url_for('register'))

        if User.query.filter_by(email=email).first():
            flash('Email already registered!', 'error')
            return redirect(url_for('register'))

        hashed_password = generate_password_hash(password)
        new_user = User(name=name, email=email, password_hash=hashed_password, role=role) # type: ignore
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
    item = MenuItem.query.get(item_id)
    if not item or not item.is_available:
        flash(f"Sorry, {item.name if item else 'Item'} is currently sold out.", "error")
        return redirect(url_for('home'))

    if 'cart' not in session or not isinstance(session['cart'], dict):
        session['cart'] = {}
    
    cart = session['cart']
    item_id_str = str(item_id)
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
        
    cart = session.get('cart', {})
    if not isinstance(cart, dict):
        cart = {}
        
    items = []
    total = 0
    for i_id_str, qty in cart.items():
        item = MenuItem.query.get(int(i_id_str))
        if item:
            # We add quantity to the item object for template access
            item.quantity = qty
            item.subtotal = float(item.price) * qty
            items.append(item)
            total += item.subtotal
            
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
        
    cart = session.get('cart', {})
    if not cart or not isinstance(cart, dict):
        return redirect(url_for('home'))

    if request.method == 'POST':
        # Process order
        payment_method = request.form.get('payment')
        schedule_time = request.form.get('schedule_time') or None
        
        # Calculate total and verify availability
        total = 0
        order_items_to_create = []
        
        for i_id_str, qty in cart.items():
            item = MenuItem.query.get(int(i_id_str))
            if item:
                if not item.is_available:
                    flash(f"Sorry, {item.name} is currently sold out.", "error")
                    return redirect(url_for('view_cart'))
                
                price = float(item.price)
                total += price * qty
                order_items_to_create.append({
                    'menu_item_id': item.id,
                    'quantity': qty,
                    'price_at_order': price
                })
        
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
            # Create Razorpay Order
            data = {
                "amount": int(total * 100), # Amount in paise
                "currency": "INR",
                "payment_capture": "1"
            }
            try:
                razorpay_order = razorpay_client.order.create(data=data)
                new_order.rzp_order_id = razorpay_order['id']
            except Exception as e:
                flash("Error creating payment order. Please try again.", "error")
                return redirect(url_for('payment'))

        db.session.add(new_order)
        db.session.flush()
        
        for item_data in order_items_to_create:
            oi = OrderItem(order_id=new_order.id, **item_data)
            db.session.add(oi)
            
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
    total = 0
    schedule_time = request.args.get('order-time') or None
    preselected = request.args.get('payment', '')
    for i_id_str, qty in cart.items():
        item = MenuItem.query.get(int(i_id_str))
        if item:
            total += float(item.price)
    return render_template('payment.html', user=user, total=total, schedule_time=schedule_time, preselected=preselected)

@app.route('/verify-payment', methods=['POST'])
def verify_payment():
    data = request.json
    rzp_payment_id = data.get('razorpay_payment_id')
    rzp_order_id = data.get('razorpay_order_id')
    rzp_signature = data.get('razorpay_signature')
    
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
            db.session.commit()
            session.pop('cart', None)
            return json.dumps({'status': 'success', 'order_id': order.id})
    except Exception as e:
        return json.dumps({'status': 'failure', 'error': str(e)}), 400
    
    return json.dumps({'status': 'failure'}), 400

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
        
    orders = Order.query.order_by(Order.id.desc()).all()
    active_orders_count = Order.query.filter(Order.status.in_(['Pending', 'Preparing'])).count()
    items_count = MenuItem.query.count()
    
    return render_template('admin.html', user=user, orders=orders, total_orders=len(orders), total_items=items_count, active_orders=active_orders_count)

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
    
    return render_template('admin_analytics.html', 
                           user=user, 
                           total_revenue=total_revenue, 
                           popular_items=popular_items,
                           recent_orders=recent_orders)

@app.route('/admin/update_order_status/<int:order_id>')
def update_order_status(order_id):
    user = get_current_user()
    if user and user.role == 'admin':
        order = Order.query.get(order_id)
        if order:
            if order.status == 'Pending':
                order.status = 'Preparing'
            elif order.status == 'Preparing':
                order.status = 'Completed'
            db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/cancel_order/<int:order_id>')
def cancel_order(order_id):
    user = get_current_user()
    if not user:
        return redirect(url_for('login'))
        
    order = Order.query.get(order_id)
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
        price = request.form.get('price')
        image_url = request.form.get('image_url')
        is_available = True if request.form.get('is_available') == 'on' else False
        
        new_item = MenuItem(name=name, category=category, price=price, image_url=image_url, is_available=is_available) # type: ignore
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
        
    item = MenuItem.query.get(item_id)
    if not item:
        flash("Item not found", "error")
        return redirect(url_for('manage_items'))

    if request.method == 'POST':
        item.name = request.form.get('name')
        item.category = request.form.get('category')
        item.price = request.form.get('price')
        item.image_url = request.form.get('image_url')
        item.is_available = True if request.form.get('is_available') == 'on' else False
        
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
        item = MenuItem.query.get(item_id)
        if item:
            name = item.name
            db.session.delete(item)
            db.session.commit()
            flash(f'Successfully deleted {name} from the menu.', 'success')
            
    return redirect(url_for('manage_items'))

@app.route('/admin/toggle-availability/<int:item_id>')
def toggle_item_availability(item_id):
    user = get_current_user()
    if user and user.role == 'admin':
        item = MenuItem.query.get(item_id)
        if item:
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
    return render_template('500.html'), 500

if __name__ == '__main__':
    app.run(debug=True)
