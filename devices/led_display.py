import json
import logging
import socket
import threading


class LEDDisplay:

    def __init__(
        self,
        ip,
        port,
        width=96,
        height=64,
        retries=2,
        connect_timeout=2,
        response_timeout=1,
        font_size=16
    ):

        self.ip = ip
        self.port = port

        self.width = width
        self.height = height

        self.retries = retries
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout

        self.font_size = font_size

        self.logger = logging.getLogger("parksmart")

        self.lock = threading.Lock()

        self._last_payload = None
        self._payload_lock = threading.Lock()

    # ---------------------------------------------------------
    # TEXT WIDTH
    # ---------------------------------------------------------

    def needs_scroll(self, text):

        # Approximate character width scales with font size —
        # roughly 0.75px of width per point of font size, which
        # reduces to the old hardcoded "6" at size=8 (6 = 8*0.75).
        char_width = self.font_size * 0.75

        return len(text) * char_width > self.width

    # ---------------------------------------------------------
    # ROW
    # ---------------------------------------------------------

    def row(
        self,
        text,
        color="green",
        bold=False,
        scroll=None
    ):

        # scroll=True forces sliding text, scroll=False forces
        # static text, scroll=None falls back to auto-detecting
        # whether the text is too wide for the display.

        sliding = (
            self.needs_scroll(text)
            if scroll is None
            else scroll
        )

        # We don't know for certain which field name this
        # panel's firmware actually reads for font weight — some
        # LED controllers use "bold" (boolean), others use
        # "weight"/"font_weight" (string), others "style". Send
        # all of the common conventions with the same "not bold"
        # intent so this works regardless of which one the
        # firmware actually looks at, instead of guessing one
        # and burning another deploy-and-check cycle if it's
        # wrong.
        weight_label = "bold" if bold else "normal"
        style_label = "bold" if bold else "regular"

        return {
            "text": text,
            "size": self.font_size,
            "bold": bold,
            "weight": weight_label,
            "font_weight": weight_label,
            "style": style_label,
            "sliding": sliding,
            "left_padding": 2,
            "right_padding": 2,
            "bottom_padding": 2,
            "color": color
        }

    # ---------------------------------------------------------
    # BUILD
    # ---------------------------------------------------------

    def build_payload(
        self,
        parking_name,
        companies,
        vehicle_number="",
        rfid_status="",
        display_mode="parking"
    ):

        if display_mode == "full":

            return {
                "commands": [
                    self.row(
                        "PARKING FULL",
                        color="red",
                        bold=True,
                        scroll=False
                    )
                ]
            }

        if display_mode == "rfid":

            return {
                "commands": [
                    self.row(
                        vehicle_number or "",
                        color="white",
                        bold=True,
                        scroll=False
                    ),
                    self.row(
                        rfid_status or "VISITOR",
                        color=(
                            "green"
                            if rfid_status in ("REGISTERED", "OWNER")
                            else "yellow"
                        ),
                        bold=True,
                        scroll=False
                    )
                ]
            }

        total_capacity = sum(
            c["capacity"]
            for c in companies
        )

        total_filled = sum(
            c["occupancy"]
            for c in companies
        )

        total_available = max(
            0,
            total_capacity - total_filled
        )

        rows = []

        # Line 1: mall/parking name — scrolls (matches the
        # existing behavior of the header row scrolling while
        # everything else stays static).
        rows.append(
            self.row(
                parking_name,
                color="yellow",
                bold=True,
                scroll=True
            )
        )

        # Lines 2-4: mall-wide summary, static, in the exact
        # "Total / Available / Occupied" format requested.
        rows.append(
            self.row(
                "Total: {}".format(total_capacity),
                color="white",
                bold=True,
                scroll=False
            )
        )

        rows.append(
            self.row(
                "Available: {}".format(total_available),
                color="green",
                bold=True,
                scroll=False
            )
        )

        rows.append(
            self.row(
                "Occupied: {}".format(total_filled),
                color="red",
                bold=True,
                scroll=False
            )
        )

        # Only these 4 rows are shown — mall name + Total/
        # Available/Occupied. No per-company breakdown.

        return {
            "commands": rows
        }

    # ---------------------------------------------------------
    # SEND
    # ---------------------------------------------------------

    def send(self, payload):

        message = (
            json.dumps(
                payload,
                separators=(",", ":")
            ) + "\n"
        )

        data = message.encode("utf-8")

        with self.lock:

            for attempt in range(
                1,
                self.retries + 2
            ):

                try:

                    with socket.create_connection(
                        (self.ip, self.port),
                        timeout=self.connect_timeout
                    ) as sock:

                        sock.settimeout(
                            self.response_timeout
                        )

                        sock.sendall(data)

                        try:

                            response = sock.recv(128)

                            response_text = (
                                response
                                .decode(
                                    "utf-8",
                                    errors="ignore"
                                )
                                .strip()
                                .upper()
                            )

                            if response_text:

                                self.logger.info(
                                    "LED RESPONSE | %s",
                                    response_text
                                )

                        except socket.timeout:

                            self.logger.warning(
                                "LED NO ACK | display may still have accepted JSON"
                            )

                        self.logger.info(
                            "LED DATA SENT | attempt=%s | bytes=%s",
                            attempt,
                            len(data)
                        )

                        return True

                except Exception as exc:

                    self.logger.error(
                        "LED ERROR | attempt=%s | %s",
                        attempt,
                        exc
                    )

            return False

    # ---------------------------------------------------------
    # ASYNC UPDATE
    # ---------------------------------------------------------

    def update_async(
        self,
        parking_name,
        companies,
        reason="update",
        vehicle_number="",
        rfid_status="",
        display_mode="parking"
    ):

        payload = self.build_payload(
            parking_name,
            companies,
            vehicle_number=vehicle_number,
            rfid_status=rfid_status,
            display_mode=display_mode
        )

        payload_key = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True
        )

        with self._payload_lock:

            if payload_key == self._last_payload:
                return False

            self._last_payload = payload_key

        thread = threading.Thread(
            target=self._update_worker,
            args=(payload, reason),
            daemon=True
        )

        thread.start()

        return True

    def _update_worker(
        self,
        payload,
        reason
    ):

        if self.send(payload):

            return

        with self._payload_lock:

            payload_key = json.dumps(
                payload,
                separators=(",", ":"),
                sort_keys=True
            )

            if self._last_payload == payload_key:
                self._last_payload = None

        self.logger.error(
            "LED UPDATE FAILED | reason=%s",
            reason
        )