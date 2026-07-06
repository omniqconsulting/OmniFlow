import os
from dotenv import load_dotenv

# Load .env file from project root (if it exists) before the app starts.
# Variables already set in the OS environment are NOT overwritten.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

import uvicorn
from app.database import create_tables

if __name__ == "__main__":
    create_tables()

    port = int(os.getenv("PORT", "8000"))

    # ── Startup banner ────────────────────────────────────────────────────────
    ai_key = os.getenv("ANTHROPIC_API_KEY", "")
    ai_status = "enabled" if ai_key else "NOT configured (set ANTHROPIC_API_KEY in .env)"

    print("\n============================================")
    print("  OmniFlow is starting...")
    print("============================================")
    print(f"  URL      : http://localhost:{port}")
    print(f"  Register : http://localhost:{port}/register")
    print(f"  AI status: {ai_status}")
    print("  Press Ctrl+C to stop")
    print("============================================\n")

    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
