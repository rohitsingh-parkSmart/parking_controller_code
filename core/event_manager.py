import logging
import threading
import time


class EventManager:

    def __init__(
        self,
        duplicate_window=10
    ):

        self.logger = logging.getLogger(
            "parksmart"
        )

        self.duplicate_window = (
            duplicate_window
        )

        self.lock = threading.Lock()

        self.last_events = {}

    def is_duplicate(
        self,
        tag,
        direction
    ):

        key = (
            str(tag).upper(),
            str(direction).upper()
        )

        now = time.monotonic()

        with self.lock:

            previous = self.last_events.get(
                key
            )

            if previous is not None:

                if (
                    now - previous
                    < self.duplicate_window
                ):

                    return True

            self.last_events[
                key
            ] = now

            # Remove old entries
            expired = [
                k
                for k, timestamp
                in self.last_events.items()
                if now - timestamp >
                self.duplicate_window * 2
            ]

            for k in expired:

                self.last_events.pop(
                    k,
                    None
                )

            return False