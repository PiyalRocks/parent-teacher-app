# Optional: the app now creates tables and demo data automatically when it starts.
# Run this only to RESET the three demo passwords:  python seed.py
from app import app, init_db

with app.app_context():
    init_db(reset_demo=True)
    print("Demo logins reset: admin@school.com/admin123  teacher@school.com/teacher123  parent@school.com/parent123")
