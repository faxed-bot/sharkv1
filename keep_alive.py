import os
from threading import Thread

from flask import Flask

app = Flask(__name__)


@app.get("/")
def home() -> str:
    return "OK"


def run() -> None:
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


def keep_alive() -> None:
    server = Thread(target=run, daemon=True)
    server.start()
