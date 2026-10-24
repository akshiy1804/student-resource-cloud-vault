"""Production-style server that works on Windows (waitress) and Linux.  python serve.py"""
import os

from waitress import serve

from app import create_app

if __name__ == "__main__":
    host, port = os.getenv("HOST", "127.0.0.1"), int(os.getenv("PORT", "8000"))
    print(f"Serving on http://{host}:{port}")
    serve(create_app(), host=host, port=port, threads=4)
