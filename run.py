import os
from wsgiref.simple_server import make_server

from app import application


if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    with make_server(host, port, application) as server:
        print(f"Застосунок запущено: http://{host}:{port}")
        server.serve_forever()
