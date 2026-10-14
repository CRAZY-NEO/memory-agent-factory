"""Short-lived, process-local browser sessions for the loopback demo launcher."""

import secrets
import threading
import time


class DemoSession:
    """A one-use opening ticket creates an HttpOnly browser session, never a bearer key."""

    cookie_name = 'factory_demo_session'

    def __init__(self, ticket, clock=None):
        self.clock = clock or time.monotonic
        self.ticket = ticket
        self.ticket_until = self.clock() + 300
        self.session = None
        self.session_until = 0
        self.lock = threading.Lock()

    def redeem(self, ticket):
        with self.lock:
            if not self.ticket or self.clock() >= self.ticket_until or not secrets.compare_digest(
                    ticket.encode(), self.ticket.encode()):
                return None
            self.ticket = None
            self.session = secrets.token_urlsafe(32)
            self.session_until = self.clock() + 12 * 60 * 60
            return self.session

    def valid(self, cookie):
        with self.lock:
            return bool(self.session and cookie and self.clock() < self.session_until
                        and secrets.compare_digest(cookie.encode(), self.session.encode()))

    def revoke(self):
        with self.lock:
            self.session = None
            self.session_until = 0
