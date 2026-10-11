"""Entry point for the web UI: python app.py  ->  http://127.0.0.1:8888"""

from djset2spotify.webapp import create_app, run

app = create_app()  # for `flask --app app run` / WSGI servers

if __name__ == "__main__":
    run()
