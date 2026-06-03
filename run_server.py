import sys

from app import app, ensure_dirs


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    ensure_dirs()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
