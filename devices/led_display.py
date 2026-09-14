import json
import logging
import socket
import threading
import time


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

        # Result of the most recent real send, used for status
        # reporting so the status thread does not have to open its
        # own competing connection to the panel.
        self._last_result = None
        self._last_attempt = 0.0

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
        bold=True,
        scroll=None,
        size=None,
        top_padding=0,
        bottom_padding=2
    ):

        # scroll=True forces sliding text, scroll=False forces
        # static text, scroll=None falls back to auto-detecting
        # whether the text is too wide for the display.

        sliding = (
            len(text) * (size or self.font_size) * 0.75 > self.width
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
            "size": size or self.font_size,
            "bold": bold,
            "weight": weight_label,
            "font_weight": weight_label,
            "style": style_label,
            "sliding": sliding,
            "left_padding": 2,
            "right_padding": 2,
            "top_padding": top_padding,
            "bottom_padding": bottom_padding,
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
        display_mode="parking",
        registered=False
    ):

        if display_mode == "full":

            return {
                "commands": [
                    self.row(
                        "PARKING FULL",
                        color="red",
                        bold=True,
                        scroll=True
                    )
                ]
            }

        if display_mode == "rfid":

            # Upper panel  = car number (matched vehicles only).
            # Lower panel  = owner name / REGISTERED, or VISITOR.
            #
            # The tag id is deliberately never part of either row —
            # it is an internal identifier and must not reach the
            # panel.

            status = rfid_status or "VISITOR"

            # Trust the caller's match result rather than comparing
            # the status text, so an owner literally named e.g.
            # "Visitor Account" is still rendered as registered.
            is_visitor = not registered

            rows = []

            if vehicle_number:

                rows.append(
                    self.row(
                        vehicle_number,
                        color="white",
                        bold=True,
                        # scroll=None => slide only if the plate is
                        # genuinely wider than the panel, so a long
                        # number is never silently clipped.
                        scroll=None,
                        size=self.font_size,
                        bottom_padding=2
                    )
                )

            # Status always occupies the last row of the panel.
            rows.append(
                self.row(
                    status,
                    color=(
                        "yellow"
                        if is_visitor
                        else "green"
                    ),
                    # VISITOR is emphasised; a registered owner name
                    # stays normal weight at size 8.
                    bold=is_visitor,
                    scroll=None,
                    size=8,
                    bottom_padding=2
                )
            )

            return {
                "commands": rows
            }

        if display_mode == "parking_summary":

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

            return {
                "commands": [
                    self.row(
                        parking_name,
                        color="yellow",
                        bold=True,
                        scroll=True,
                        size=16,
                        bottom_padding=2
                    ),
                    self.row(
                        "Total: {}".format(total_capacity),
                        color="white",
                        bold=True,
                        scroll=None,
                        size=16,
                        bottom_padding=2
                    ),
                    self.row(
                        "Available: {}".format(total_available),
                        color="green",
                        bold=True,
                        scroll=None,
                        size=16,
                        bottom_padding=2
                    ),
                    self.row(
                        "Occupied: {}".format(total_filled),
                        color="red",
                        bold=True,
                        scroll=None,
                        size=16,
                        bottom_padding=2
                    )
                ]
            }

        # Idle / no tag detected: parking name only — large and
        # bold, nothing else. No occupancy or tag information.
        return {
            "commands": [
                self.row(
                    parking_name,
                    color="yellow",
                    bold=True,
                    # Slide only when the name genuinely overflows
                    # the panel width.
                    scroll=None,
                    size=self.font_size,
                    bottom_padding=2
                )
            ]
        }

    # ---------------------------------------------------------
    # SEND
    # ---------------------------------------------------------

    def _record_result(self, ok):

        self._last_result = bool(ok)
        self._last_attempt = time.monotonic()

    def status(self, max_age=30.0):
        """
        Online state inferred from the last real send, or None when
        that information is too old to trust.

        Callers (the device-status thread) use this instead of
        opening their own probe connection: these panels accept one
        TCP client at a time, so a probe running alongside a
        tag-triggered send can make that send fail.
        """

        if self._last_result is None:
            return None

        if (
            time.monotonic() - self._last_attempt
        ) > max_age:
            return None

        return self._last_result

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
                                    "LED RESPONSE | %s:%s | %s",
                                    self.ip,
                                    self.port,
                                    response_text
                                )

                        except socket.timeout:

                            self.logger.warning(
                                "LED NO ACK | %s:%s | display may still have accepted JSON",
                                self.ip,
                                self.port
                            )

                        self.logger.info(
                            "LED DATA SENT | %s:%s | attempt=%s | bytes=%s",
                            self.ip,
                            self.port,
                            attempt,
                            len(data)
                        )

                        self._record_result(True)

                        return True

                except Exception as exc:

                    self.logger.error(
                        "LED ERROR | %s:%s | attempt=%s | %s",
                        self.ip,
                        self.port,
                        attempt,
                        exc
                    )

            self._record_result(False)

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
        display_mode="parking",
        registered=False,
        force=False
    ):

        payload = self.build_payload(
            parking_name,
            companies,
            vehicle_number=vehicle_number,
            rfid_status=rfid_status,
            display_mode=display_mode,
            registered=registered
        )

        payload_key = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True
        )

        with self._payload_lock:

            if not force and payload_key == self._last_payload:
                return False

            self._last_payload = payload_key

        thread = threading.Thread(
            target=self._update_worker,
            args=(payload, reason),
            name="LED-{}".format(self.ip),
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
            "LED UPDATE FAILED | %s:%s | reason=%s",
            self.ip,
            self.port,
            reason
        )