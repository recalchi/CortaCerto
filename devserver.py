"""Quick launcher: starts just the FastAPI backend for dev mode."""
import sys, os, time
sys.path.insert(0, os.path.dirname(__file__))

from src.api.server import run_server

print("=" * 50)
print("CortaCerto API  ->  http://127.0.0.1:7472")
print("=" * 50)

run_server(host="127.0.0.1", port=7472)
print("Server is ready.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nServer stopped.")
