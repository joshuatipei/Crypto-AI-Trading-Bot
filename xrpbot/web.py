"""Authenticated loopback dashboard. Deploy behind a TLS reverse proxy."""
import base64
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .paper import load_broker, save_broker


def server(store, host='127.0.0.1', port=8080):
    token = os.environ.get('DASHBOARD_TOKEN', '')
    if len(token) < 24:
        raise ValueError('Set DASHBOARD_TOKEN to a random secret of at least 24 characters')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass  # Never log authorization headers or query strings.

        def authorized(self):
            expected = 'Basic ' + base64.b64encode(f'paper:{token}'.encode()).decode()
            return hmac.compare_digest(self.headers.get('Authorization', ''), expected)

        def respond(self, status, body, content_type='application/json'):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if status == 401:
                self.send_header('WWW-Authenticate', 'Basic realm="XRP paper", charset="UTF-8"')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.authorized():
                return self.respond(401, {'error': 'Authentication required'})
            if self.path == '/':
                return self.respond(200, Path(__file__).with_name('dashboard.html').read_bytes(), 'text/html; charset=utf-8')
            if self.path == '/api/state':
                state = store.transact(lambda p: dict(p))
                state['mode'] = 'paper'
                state['stale'] = time.time() - state.get('updated_at', 0) > 90
                return self.respond(200, state)
            self.respond(404, {'error': 'Not found'})

        def do_POST(self):
            if not self.authorized():
                return self.respond(401, {'error': 'Authentication required'})
            # A custom header plus no CORS preflight support blocks cross-site form controls.
            if self.headers.get('X-Paper-Control') != '1' or self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return self.respond(403, {'error': 'Control header required'})
            if self.path not in ('/api/pause', '/api/resume', '/api/emergency'):
                return self.respond(404, {'error': 'Not found'})
            def control(payload):
                broker = load_broker(payload)
                a = broker.account
                if self.path == '/api/pause':
                    a.paused = True
                elif self.path == '/api/resume':
                    if a.halted:
                        return {'error': 'Risk/emergency halt is latched; offline review required'}
                    a.paused = False
                else:
                    a.halted = a.paused = True
                    # Do not invent a fill on an old quote. Flatten on next validated tick.
                broker.event(int(time.time()), self.path.rsplit('/', 1)[-1])
                save_broker(payload, broker)
                return {'status': 'ok', 'halted': a.halted, 'paused': a.paused}
            result = store.transact(control)
            self.respond(409 if 'error' in result else 200, result)

    return ThreadingHTTPServer((host, port), Handler)
