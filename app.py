from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room
import json, time, math, os, hashlib
from datetime import datetime

app = Flask(__name__, template_folder='templates')
app.secret_key = 'campus_erickshaw_secret_2026'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# In-memory stores (replace with DB in production)
drivers = {}       # empID -> driver data
sessions = {}      # session_token -> empID
active_drivers = {}  # empID -> {location, cart_no, is_full, name, phone, picked_up_users}
pending_calls = {}   # call_id -> {user_lat, user_lng, driver_id, status}

CARTS = [f"Cart-{i:02d}" for i in range(1, 11)]

def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def eta_minutes(dist_m, speed_kmh=15):
    return round((dist_m / 1000) / speed_kmh * 60, 1)

# ─── PAGES ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return redirect(url_for('user_page'))

@app.route('/user')
def user_page():
    return render_template('user.html')

@app.route('/driver')
def driver_page():
    return render_template('driver.html')

# ─── DRIVER AUTH API ──────────────────────────────────────────────────────────

@app.route('/api/driver/register', methods=['POST'])
def driver_register():
    d = request.json
    emp_id = d.get('emp_id', '').strip()
    if not emp_id:
        return jsonify({'ok': False, 'msg': 'Employee ID required'})
    if emp_id in drivers:
        return jsonify({'ok': False, 'msg': 'Employee ID already registered'})
    drivers[emp_id] = {
        'emp_id': emp_id,
        'name': d.get('name', ''),
        'phone': d.get('phone', ''),
        'password': hash_pw(d.get('password', '')),
        'registered_at': datetime.now().isoformat()
    }
    return jsonify({'ok': True, 'msg': 'Registered successfully'})

@app.route('/api/driver/login', methods=['POST'])
def driver_login():
    d = request.json
    emp_id = d.get('emp_id', '').strip()
    pw = d.get('password', '')
    driver = drivers.get(emp_id)
    if not driver or driver['password'] != hash_pw(pw):
        return jsonify({'ok': False, 'msg': 'Invalid credentials'})
    token = hashlib.sha256(f"{emp_id}{time.time()}".encode()).hexdigest()[:32]
    sessions[token] = emp_id
    return jsonify({'ok': True, 'token': token, 'name': driver['name'], 'emp_id': emp_id})

@app.route('/api/driver/logout', methods=['POST'])
def driver_logout():
    token = request.json.get('token')
    emp_id = sessions.pop(token, None)
    if emp_id and emp_id in active_drivers:
        del active_drivers[emp_id]
        socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True})

def auth_driver(token):
    return sessions.get(token)

# ─── DRIVER ACTIVE API ────────────────────────────────────────────────────────

@app.route('/api/driver/go_online', methods=['POST'])
def go_online():
    d = request.json
    emp_id = auth_driver(d.get('token'))
    if not emp_id:
        return jsonify({'ok': False, 'msg': 'Unauthorized'})
    driver = drivers[emp_id]
    active_drivers[emp_id] = {
        'emp_id': emp_id,
        'name': driver['name'],
        'phone': driver['phone'],
        'cart_no': d.get('cart_no'),
        'lat': d.get('lat'),
        'lng': d.get('lng'),
        'is_full': False,
        'pending_user': None,   # {call_id, lat, lng}
        'last_update': time.time()
    }
    socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True})

@app.route('/api/driver/update_location', methods=['POST'])
def update_location():
    d = request.json
    emp_id = auth_driver(d.get('token'))
    if not emp_id or emp_id not in active_drivers:
        return jsonify({'ok': False})
    active_drivers[emp_id]['lat'] = d.get('lat')
    active_drivers[emp_id]['lng'] = d.get('lng')
    active_drivers[emp_id]['last_update'] = time.time()
    socketio.emit('drivers_update', get_active_drivers_public())
    # If driver has pending user, emit updated ETA to that user
    pu = active_drivers[emp_id].get('pending_user')
    if pu:
        dist = haversine(d['lat'], d['lng'], pu['lat'], pu['lng'])
        eta = eta_minutes(dist)
        socketio.emit(f"eta_update_{pu['call_id']}", {'eta': eta, 'dist': round(dist)})
    return jsonify({'ok': True})

@app.route('/api/driver/set_full', methods=['POST'])
def set_full():
    d = request.json
    emp_id = auth_driver(d.get('token'))
    if not emp_id or emp_id not in active_drivers:
        return jsonify({'ok': False})
    active_drivers[emp_id]['is_full'] = d.get('is_full', True)
    socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True})

@app.route('/api/driver/picked_up', methods=['POST'])
def picked_up():
    d = request.json
    emp_id = auth_driver(d.get('token'))
    if not emp_id or emp_id not in active_drivers:
        return jsonify({'ok': False})
    call_id = d.get('call_id')
    # Notify user their call was picked up
    socketio.emit(f"call_picked_{call_id}", {'msg': 'Driver is here!'})
    active_drivers[emp_id]['pending_user'] = None
    if call_id in pending_calls:
        del pending_calls[call_id]
    socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True})

@app.route('/api/driver/go_offline', methods=['POST'])
def go_offline():
    d = request.json
    emp_id = auth_driver(d.get('token'))
    if emp_id and emp_id in active_drivers:
        del active_drivers[emp_id]
        socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True})

@app.route('/api/driver/status', methods=['POST'])
def driver_status():
    d = request.json
    emp_id = auth_driver(d.get('token'))
    if not emp_id or emp_id not in active_drivers:
        return jsonify({'ok': True, 'online': False, 'data': None})
    return jsonify({'ok': True, 'online': True, 'data': active_drivers[emp_id]})

# ─── USER API ─────────────────────────────────────────────────────────────────

def get_active_drivers_public():
    result = []
    for emp_id, drv in active_drivers.items():
        if not drv['is_full'] and drv['lat'] is not None:
            result.append({
                'emp_id': emp_id,
                'name': drv['name'],
                'phone': drv['phone'],
                'cart_no': drv['cart_no'],
                'lat': drv['lat'],
                'lng': drv['lng'],
                'has_pending': drv['pending_user'] is not None
            })
    return result

@app.route('/api/user/drivers', methods=['GET'])
def get_drivers():
    return jsonify({'drivers': get_active_drivers_public()})

@app.route('/api/user/call', methods=['POST'])
def call_driver():
    d = request.json
    driver_id = d.get('driver_id')
    user_lat = d.get('lat')
    user_lng = d.get('lng')
    if driver_id not in active_drivers:
        return jsonify({'ok': False, 'msg': 'Driver not available'})
    drv = active_drivers[driver_id]
    if drv['is_full']:
        return jsonify({'ok': False, 'msg': 'Cart is full'})
    call_id = hashlib.sha256(f"{driver_id}{time.time()}".encode()).hexdigest()[:16]
    pending_calls[call_id] = {
        'driver_id': driver_id, 'user_lat': user_lat, 'user_lng': user_lng,
        'status': 'pending', 'ts': time.time()
    }
    active_drivers[driver_id]['pending_user'] = {
        'call_id': call_id, 'lat': user_lat, 'lng': user_lng
    }
    # Calculate initial ETA
    dist = haversine(drv['lat'], drv['lng'], user_lat, user_lng)
    eta = eta_minutes(dist)
    # Notify driver
    socketio.emit(f"new_call_{driver_id}", {
        'call_id': call_id, 'lat': user_lat, 'lng': user_lng,
        'eta': eta, 'dist': round(dist)
    })
    socketio.emit('drivers_update', get_active_drivers_public())
    return jsonify({'ok': True, 'call_id': call_id, 'eta': eta, 'dist': round(dist),
                    'driver_name': drv['name'], 'cart_no': drv['cart_no']})

@app.route('/api/carts', methods=['GET'])
def get_carts():
    occupied = {v['cart_no'] for v in active_drivers.values()}
    return jsonify({'carts': CARTS, 'occupied': list(occupied)})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000)