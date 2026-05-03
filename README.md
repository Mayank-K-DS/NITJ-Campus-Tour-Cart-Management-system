# NIT-J Campus E-Rickshaw Tracking System

A real-time web app for campus e-rickshaw tracking built with Flask + Socket.IO.

## Project Structure
```
erickshaw/
├── app.py              # Flask backend + Socket.IO
├── requirements.txt    # Python dependencies
└── templates/
    ├── user.html       # Passenger-facing UI
    └── driver.html     # Driver portal (login/register + dashboard)
```

## Setup

```bash
# 1. Create virtual environment
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the app
python app.py
```

## Routes

| URL | Description |
|-----|-------------|
| `/user` | Passenger view — see nearby carts, call one, track ETA |
| `/driver` | Driver portal — register/login, go online, manage calls |

## Flow

### Driver Flow
1. Go to `/driver` → Register with EmpID, Name, Phone, Password
2. Login → Select your Cart → Go Online
3. Your GPS is tracked every ~2 seconds in real time
4. When a passenger calls, you see their location on the map
5. Drive to them → tap **Picked Up** → their pin disappears
6. Tap **Mark Cart as Full** to hide yourself from passengers

### Passenger Flow
1. Go to `/user` — no login needed
2. Allow location access for accurate ETA
3. See available carts sorted by distance
4. Tap **Call This Cart** → driver sees your location
5. Watch real-time ETA countdown
6. When driver arrives, you get a notification

## Notes
- Currently uses **in-memory storage** — data resets on server restart
- For production: replace `drivers` dict with PostgreSQL/MongoDB
- GPS simulation: open two browser tabs for testing (driver + user)
- For HTTPS (required for GPS on mobile): use `ngrok` or deploy to a server
# NITJ-Campus-Tour-Cart-Management-system
