"""WSGI input retained by an unreachable reference cycle; no framework required."""
import os

RETAIN_INPUT = os.environ.get("REPRO_RETAIN_INPUT", "1") == "1"


def application(environ, start_response):
    if environ["REQUEST_METHOD"] == "POST":
        if RETAIN_INPUT:
            # Deliberately model delayed collection of request/traceback cycles.
            cycle = [environ["wsgi.input"]]
            cycle.append(cycle)
        start_response("429 Too Many Requests", [("Content-Length", "1")])
        return [b"x"]
    start_response("200 OK", [("Content-Length", "2")])
    return [b"ok"]
