# Granian unread WSGI input / cyclic-GC reproduction

A small, framework-free reproduction of a local stall when unread `wsgi.input`
is retained by an **unreachable reference cycle** after an early HTTP response.

**This deliberately models delayed garbage collection.** Returning an early 429
without retaining the input works in the control. This is not a claim that all
early responses are broken, nor proof of any production incident's root cause.
It requires no Django, reverse proxy, external service, or `gc.disable()`.

## Setup

Tested on macOS arm64, Python 3.13.1, Granian 2.8.5. Independent Linux
verification is still needed.

```sh
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

## Run

```sh
python repro.py
```

The harness starts one Granian worker on a randomly chosen **127.0.0.1** port,
with a backpressure limit of 16. It sends complete 64 KiB POST bodies, fully
reads each response, explicitly closes each response and client connection,
then checks health using a fresh connection.

Observed result:

- Initial health succeeds.
- 16 POST requests receive HTTP 429 responses.
- The next POST and a fresh health request time out while the worker remains alive.
- The worker has 16 accepted sockets in `CLOSE_WAIT`.

The exact number of completed requests may vary with garbage-collection timing.
An exit code of 0 means the expected live-server timeout was observed; 1 means
it was not reproduced by that run. A server crash is not counted as a reproduction.

If `lsof` or `ss` is available, the harness prints a socket snapshot limited to
the test worker or its port. On exit it terminates its **own private process
group**, including its workers. It does not signal other Granian processes.
No root access is required.

## Control: no retained input cycle

```sh
python repro.py --control
```

This runs the same app and traffic without the reference cycle. In the observed
control, Granian adds `Connection: close`, no accepted sockets remain after
clients disconnect, and fresh health requests return 200. Exit code 0 means the
control remained healthy.

## Essential application behavior

From `app.py`:

```python
cycle = [environ["wsgi.input"]]
cycle.append(cycle)
start_response("429 Too Many Requests", [("Content-Length", "1")])
return [b"x"]
```

There is no global reference to `cycle`; it becomes unreachable when the WSGI
callable returns. It remains alive until cyclic GC runs, retaining the unread
input in the meantime. The control removes only this cycle.

`CLOSE_WAIT` indicates that the peer has closed its side but the server socket
has not finished closing. This observation, plus the backpressure limit and
fresh-connection timeout, is the specific behavior to investigate; the exact
upstream cleanup responsibility remains to be established.

## Additional experiments

```sh
python repro.py --backpressure 128 --requests 10000
```

A larger limit may let GC run often enough for the server to keep making
progress. A healthy result exits 1 in reproduction mode because it did **not**
observe the expected stall. This is a diagnostic experiment, not a claim that
raising limits fixes the underlying input lifetime.

To test another Granian version, install it in the isolated environment first.
The harness prints the actual Python and Granian versions it uses.

Do not run this artificial workload against a production endpoint. The harness
intentionally has no remote-host option.
