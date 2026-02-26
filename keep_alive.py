from threading import Thread

from flask import Flask

app = Flask("")


@app.get("/")
def home() -> str:
    return "OK"


def run() -> None:
    app.run(host="0.0.0.0", port=8080)


def keep_alive() -> None:
    server = Thread(target=run)
    server.daemon = True
    server.start()
