"""Run a synthetic localhost-only reproduction and clean up its subprocesses."""
import argparse
import http.client
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time


def exchange(port, method, path, timeout, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    response = None
    try:
        connection.request(method, path, body=body)
        response = connection.getresponse()
        response.read()
        return response.status, response.getheaders()
    finally:
        if response is not None:
            response.close()
        connection.close()


def snapshot(log, port):
    log.seek(0)
    workers = re.findall(r"Spawning worker-1 with PID: (\d+)", log.read())
    if workers and (lsof := shutil.which("lsof")):
        command = [lsof, "-nP", "-a", "-p", workers[-1], "-iTCP"]
    elif ss := shutil.which("ss"):
        command = [ss, "-antp", f"( sport = :{port} )"]
    else:
        print("Socket inspection skipped: neither lsof nor ss is available.")
        return
    result = subprocess.run(command, capture_output=True, text=True, timeout=5)
    print("Server socket snapshot:", flush=True)
    print(result.stdout or result.stderr, flush=True)


def run(args):
    if os.name != "posix":
        raise SystemExit("The subprocess cleanup in this harness requires macOS or Linux.")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {
        **os.environ,
        "REPRO_RETAIN_INPUT": "0" if args.control else "1",
    }
    command = [
        sys.executable, "-m", "granian", "--interface", "wsgi",
        "--host", "127.0.0.1", "--port", str(port), "--workers", "1",
        "--runtime-mode", "mt", "--runtime-threads", "2",
        "--blocking-threads", "12", "--backlog", "128",
        "--backpressure", str(args.backpressure), "app:application",
    ]
    print(json.dumps({
        "python": sys.version.split()[0],
        "granian": importlib.metadata.version("granian"),
        "mode": "control" if args.control else "unreachable input cycle",
        "backpressure": args.backpressure,
        "port": port,
    }), flush=True)
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen(
            command, cwd=Path(__file__).resolve().parent, env=environment,
            stdout=log, stderr=log, start_new_session=True,
        )
        try:
            for _ in range(100):
                if process.poll() is not None:
                    log.seek(0)
                    raise RuntimeError(log.read())
                try:
                    if exchange(port, "GET", "/health", 0.2)[0] == 200:
                        break
                except OSError:
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError("The local test server did not become healthy.")
            statuses = []
            failure = None
            for index in range(args.requests):
                try:
                    status, headers = exchange(
                        port, "POST", "/reject", args.timeout, b"x" * args.body_size,
                    )
                    statuses.append(status)
                    if index == 0:
                        print("First response headers:", headers, flush=True)
                except OSError as error:
                    failure = type(error).__name__
                    break
            try:
                health = exchange(port, "GET", "/health", args.timeout)[0]
            except OSError as error:
                health = type(error).__name__
            print(json.dumps({
                "completed": len(statuses),
                "responses": {str(code): statuses.count(code) for code in set(statuses)},
                "next_request_error": failure,
                "fresh_health_request": health,
                "server_exit_code": process.poll(),
            }), flush=True)
            snapshot(log, port)
            observed_stall = (
                failure == "TimeoutError"
                and health == "TimeoutError"
                and process.poll() is None
            )
            expected_result = health == 200 if args.control else observed_stall
            return 0 if expected_result else 1
        finally:
            # Each run owns a private process group. Never signal other servers.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", action="store_true", help="Do not retain the unread input.")
    parser.add_argument("--requests", type=int, default=64)
    parser.add_argument("--body-size", type=int, default=65536)
    parser.add_argument("--backpressure", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=1.0)
    sys.exit(run(parser.parse_args()))
