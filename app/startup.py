from app.cache import init_db

def initialize():
    """Run once at app startup."""
    init_db()
    print("[Startup] Database initialized")