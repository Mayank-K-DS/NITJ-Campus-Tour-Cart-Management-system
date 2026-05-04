from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_socketio import SocketIO
from flask_sqlalchemy import SQLAlchemy
import time, math, hashlib
from datetime import datetime

app = Flask(__name__, template_folder='templates')
app.secret_key = 'campus_erickshaw_secret_2026'

MAX_PICKUP_DISTANCE = 50  

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ---------------- DB CONFIG ----------------
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///erickshaw.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ---------------- MODELS ----------------

class Driver(db.Model):
    emp_id = db.Column(db.String(50), primary_key=True)
    name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    password = db.Column(db.String(256))
    registered_at = db.Column(db.String(100))


class Session(db.Model):
    token = db.Column(db.String(64), primary_key=True)
    emp_id = db.Column(db.String(50), db.ForeignKey('driver.emp_id'))


class ActiveDriver(db.Model):
    emp_id = db.Column(db.String(50), db.ForeignKey('driver.emp_id'), primary_key=True)
    cart_no = db.Column(db.String(20))
    lat = db.Column(db.Float)
    lng = db.Column(db.Float)
    is_full = db.Column(db.Boolean, default=False)
    pending_call_id = db.Column(db.String(50), nullable=True)
    last_update = db.Column(db.Float)


class PendingCall(db.Model):
    call_id = db.Column(db.String(50), primary_key=True)
    driver_id = db.Column(db.String(50), db.ForeignKey('driver.emp_id'))
    user_lat = db.Column(db.Float)
    user_lng = db.Column(db.Float)
    status = db.Column(db.String(20))
    ts = db.Column(db.Float)

# ---------------- INIT DB ----------------
with app.app_context():
    db.create_all()

# ---------------- UTILS ----------------

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat1 - lat2)
    dlambda = math.radians(lon1 - lon2)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def eta_minutes(dist_m, speed_kmh=15):
    return round((dist_m / 1000) / speed_kmh * 60, 1)

def auth_driver(token):
    s = Session.query.get(token)
    return s.emp_id if s else None

# ---------------- PAGES ----------------

@app.route('/')
def index():
    return redirect(url_for('user_page'))

@app.route('/user')
def user_page():
    return render_template('user.html')

@app.route('/driver')
def driver_page():
    return render_template('driver.html')

# ---------------- DRIVER AUTH ----------------

@app.route('/api/driver/register', methods=['POST'])
def driver_register():
    d = request.json
    emp_id = d.get('emp_id')

    if Driver.query.get(emp_id):
        return jsonify({'ok': False, 'msg': 'Already exists'})

    driver = Driver(
        emp_id=emp_id,
        name=d.get('name'),
        phone=d.get('phone'),
        password=hash_pw(d.get('password')),
        registered_at=datetime.now().isoformat()
    )

    db.session.add(driver)
    db.session.commit()

    return jsonify({'ok': True})


@app.route('/api/driver/login', methods=['POST'])
def driver_login():
    d = request.json
    emp_id = d.get('emp_id')
    pw = d.get('password')

    driver = Driver.query.get(emp_id)

    if not driver or driver.password != hash_pw(pw):
        return jsonify({'ok': False, 'msg': 'Employee ID or Password do not match'})

    token = hashlib.sha256(f"{emp_id}{time.time()}".encode()).hexdigest()[:32]

    db.session.add(Session(token=token, emp_id=emp_id))
    db.session.commit()

    return jsonify({'ok': True, 'token': token, 'name': driver.name, 'emp_id': emp_id})


@app.route('/api/driver/logout', methods=['POST'])
def driver_logout():
    token = request.json.get('token')
    emp_id = auth_driver(token)

    Session.query.filter_by(token=token).delete()
    ActiveDriver.query.filter_by(emp_id=emp_id).delete()

    db.session.commit()

    socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True})

# ---------------- DRIVER ACTIVE ----------------

@app.route('/api/driver/go_online', methods=['POST'])
def go_online():
    d = request.json
    emp_id = auth_driver(d.get('token'))

    driver = Driver.query.get(emp_id)

    active = ActiveDriver(
        emp_id=emp_id,
        cart_no=d.get('cart_no'),
        lat=d.get('lat'),
        lng=d.get('lng'),
        is_full=False,
        last_update=time.time()
    )

    db.session.merge(active)
    db.session.commit()

    socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True})


@app.route('/api/driver/update_location', methods=['POST'])
def update_location():
    d = request.json
    emp_id = auth_driver(d.get('token'))

    drv = ActiveDriver.query.get(emp_id)
    if not drv:
        return jsonify({'ok': False})

    drv.lat = d.get('lat')
    drv.lng = d.get('lng')
    drv.last_update = time.time()

    db.session.commit()

    socketio.emit('drivers_update', get_active_drivers_public())

    if drv.pending_call_id:
        call = PendingCall.query.get(drv.pending_call_id)
        dist = haversine(drv.lat, drv.lng, call.user_lat, call.user_lng)
        eta = eta_minutes(dist)

        socketio.emit(f"eta_update_{call.call_id}", {'eta': eta, 'dist': round(dist)})

    return jsonify({'ok': True})


@app.route('/api/driver/set_full', methods=['POST'])
def set_full():
    d = request.json
    emp_id = auth_driver(d.get('token'))

    drv = ActiveDriver.query.get(emp_id)
    drv.is_full = d.get('is_full', True)

    db.session.commit()

    socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True})


@app.route('/api/driver/picked_up', methods=['POST'])
def picked_up():
    d = request.json
    emp_id = auth_driver(d.get('token'))
    call_id = d.get('call_id')

    drv = ActiveDriver.query.get(emp_id)
    call = PendingCall.query.get(call_id)

    if not drv or not call:
        return jsonify({'ok': False, 'msg': 'Invalid request'})

    # 🚨 DISTANCE CHECK
    dist = haversine(drv.lat, drv.lng, call.user_lat, call.user_lng)

    if dist > MAX_PICKUP_DISTANCE:
        return jsonify({
            'ok': False,
            'msg': f'Too far to pick up! ({round(dist)}m away)'
        })

    # ✅ ALLOW PICKUP
    socketio.emit(f"call_picked_{call_id}", {'msg': 'Driver is here!'})

    PendingCall.query.filter_by(call_id=call_id).delete()
    drv.pending_call_id = None

    db.session.commit()

    socketio.emit('drivers_update', get_active_drivers_public())

    return jsonify({'ok': True})


@app.route('/api/driver/go_offline', methods=['POST'])
def go_offline():
    d = request.json
    emp_id = auth_driver(d.get('token'))

    ActiveDriver.query.filter_by(emp_id=emp_id).delete()
    db.session.commit()

    socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True})


@app.route('/api/driver/status', methods=['POST'])
def driver_status():
    d = request.json
    emp_id = auth_driver(d.get('token'))

    drv = ActiveDriver.query.get(emp_id)

    if not drv:
        return jsonify({'ok': True, 'online': False})

    return jsonify({'ok': True, 'online': True, 'data': {
        'cart_no': drv.cart_no,
        'lat': drv.lat,
        'lng': drv.lng,
        'is_full': drv.is_full
    }})

# ---------------- USER ----------------

def get_active_drivers_public():
    drivers = ActiveDriver.query.filter_by(is_full=False).all()
    result = []

    for drv in drivers:
        info = Driver.query.get(drv.emp_id)

        if drv.lat is not None:
            result.append({
                'emp_id': drv.emp_id,
                'name': info.name,
                'phone': info.phone,
                'cart_no': drv.cart_no,
                'lat': drv.lat,
                'lng': drv.lng,
                'has_pending': drv.pending_call_id is not None
            })

    return result


@app.route('/api/user/drivers')
def get_drivers():
    return jsonify({'drivers': get_active_drivers_public()})


@app.route('/api/user/call', methods=['POST'])
def call_driver():
    d = request.json
    driver_id = d.get('driver_id')

    drv = ActiveDriver.query.get(driver_id)

    if not drv or drv.is_full:
        return jsonify({'ok': False})

    call_id = hashlib.sha256(f"{driver_id}{time.time()}".encode()).hexdigest()[:16]

    call = PendingCall(
        call_id=call_id,
        driver_id=driver_id,
        user_lat=d.get('lat'),
        user_lng=d.get('lng'),
        status='pending',
        ts=time.time()
    )

    db.session.add(call)
    drv.pending_call_id = call_id
    db.session.commit()

    info = Driver.query.get(driver_id)

    dist = haversine(drv.lat, drv.lng, d.get('lat'), d.get('lng'))
    eta = eta_minutes(dist)

    socketio.emit(f"new_call_{driver_id}", {
        'call_id': call_id,
        'lat': d.get('lat'),
        'lng': d.get('lng'),
        'eta': eta,
        'dist': round(dist)
    })

    socketio.emit('drivers_update', get_active_drivers_public())

    return jsonify({
        'ok': True,
        'call_id': call_id,
        'eta': eta,
        'dist': round(dist),
        'driver_name': info.name,
        'cart_no': drv.cart_no
    })


CARTS = [f"Cart-{i:02d}" for i in range(1, 11)]

@app.route('/api/carts')
def get_carts():
    active = ActiveDriver.query.all()
    occupied = [d.cart_no for d in active]

    return jsonify({'carts': CARTS, 'occupied': occupied})


# ---------------- RUN ----------------

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)
