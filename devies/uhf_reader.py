#!/usr/bin/env python3

import socket
import threading
import time
import logging


class UHFReader:
    """
    Syrotech UHF TCP Reader

    Expected packet example:

    43 54 00 1C 01 45 01 FF 8B 26
    04 20 25 5D 01 0F 01 01 E2 00
    22 08 00 00 00 00 0C C3 13 08
    28 80

    Correct EPC/tag bytes:

        0C C3 13 08

    Correct normalized tag:

        000000000CC31308
    """

    FRAME_LENGTH = 32

    # Tag is exactly 4 bytes:
    # byte index 26,27,28,29
    TAG_START = 26
    TAG_END = 30

    def __init__(self, config, logger=None, callback=None, direction="ENTRY"):

        self.config = config
        self.logger = logger or logging.getLogger("parksmart")
        self.callback = callback

        # Each reader instance (entry gate vs exit gate) must know
        # which direction it represents. This used to be hardcoded
        # to "ENTRY" everywhere below, which meant the exit reader
        # also reported every tag as an ENTRY event.
        self.direction = str(
            config.get("direction", direction)
        ).upper()

        self.host = str(
            config.get("host", "192.168.100.100")
        )

        self.port = int(
            config.get("port", 60000)
        )

        self.connect_timeout = float(
            config.get("connect_timeout", 3)
        )

        self.read_timeout = float(
            config.get("read_timeout", 1)
        )

        self.reconnect_delay = float(
            config.get("reconnect_delay", 2)
        )

        self.running = False
        self.sock = None

        self.thread = None

        self.buffer = bytearray()

        self.lock = threading.RLock()

    # =========================================================
    # LOG
    # =========================================================

    def log(self, level, message, *args):

        try:
            getattr(self.logger, level)(
                message,
                *args
            )
        except Exception:
            pass

    # =========================================================
    # CONNECT
    # =========================================================

    def connect(self):

        self.close_socket()

        try:

            self.log(
                "info",
                "%s CONNECTING | %s:%s",
                self.direction,
                self.host,
                self.port
            )

            sock = socket.socket(
                socket.AF_INET,
                socket.SOCK_STREAM
            )

            sock.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_KEEPALIVE,
                1
            )

            sock.settimeout(
                self.connect_timeout
            )

            sock.connect(
                (self.host, self.port)
            )

            sock.settimeout(
                self.read_timeout
            )

            with self.lock:
                self.sock = sock

            self.buffer.clear()

            self.log(
                "info",
                "%s ONLINE | %s:%s",
                self.direction,
                self.host,
                self.port
            )

            return True

        except Exception as exc:

            self.log(
                "error",
                "%s CONNECT ERROR | %s",
                self.direction,
                exc
            )

            self.close_socket()

            return False

    # =========================================================
    # CLOSE SOCKET
    # =========================================================

    def close_socket(self):

        with self.lock:

            sock = self.sock
            self.sock = None

        if sock:

            try:
                sock.shutdown(
                    socket.SHUT_RDWR
                )
            except Exception:
                pass

            try:
                sock.close()
            except Exception:
                pass

    # =========================================================
    # FRAME SEARCH
    # =========================================================

    def extract_frames(self):

        frames = []

        while True:

            # Need minimum frame length
            if len(self.buffer) < self.FRAME_LENGTH:
                break

            # Syrotech frame starts with:
            #
            # 43 54
            #
            if not (
                self.buffer[0] == 0x43
                and self.buffer[1] == 0x54
            ):

                # Remove garbage until CT
                pos = self.buffer.find(
                    b"\x43\x54"
                )

                if pos == -1:

                    # Keep last byte in case
                    # it is first byte of CT
                    self.buffer = self.buffer[-1:]

                    break

                del self.buffer[:pos]

                if len(self.buffer) < self.FRAME_LENGTH:
                    break

            frame = bytes(
                self.buffer[:self.FRAME_LENGTH]
            )

            del self.buffer[
                :self.FRAME_LENGTH
            ]

            frames.append(frame)

        return frames

    # =========================================================
    # TAG EXTRACTION
    # =========================================================

    @classmethod
    def extract_tag(cls, frame):

        if not frame:
            return None

        if len(frame) < cls.FRAME_LENGTH:
            return None

        # Verify Syrotech header
        if frame[0:2] != b"\x43\x54":
            return None

        tag_bytes = frame[
            cls.TAG_START:
            cls.TAG_END
        ]

        if len(tag_bytes) != 4:
            return None

        # Convert:
        #
        # 0C C3 13 08
        #
        # to:
        #
        # 000000000CC31308

        tag_id = tag_bytes.hex().upper()

        tag_id = tag_id.zfill(16)

        return tag_id

    # =========================================================
    # PROCESS FRAME
    # =========================================================

    def process_frame(self, frame):

        try:

            self.log(
                "info",
                "%s RAW HEX | %s",
                self.direction,
                frame.hex(" ").upper()
            )

            try:

                ascii_data = frame.decode(
                    "ascii",
                    errors="replace"
                )

            except Exception:

                ascii_data = ""

            self.log(
                "info",
                "%s RAW ASCII | %s",
                self.direction,
                ascii_data
            )

            tag_id = self.extract_tag(
                frame
            )

            if not tag_id:

                self.log(
                    "warning",
                    "%s TAG NOT FOUND",
                    self.direction
                )

                return

            self.log(
                "info",
                "%s TAG DETECTED | %s",
                self.direction,
                tag_id
            )

            # Note: main.py's handle_tag() also logs a
            # "TAG READ" line for this same tag, so we don't
            # duplicate that log entry here.

            if self.callback:

                try:

                    self.callback(
                        tag_id,
                        self.direction
                    )

                except TypeError:

                    # Backward compatibility
                    self.callback(
                        tag_id
                    )

                except Exception as exc:

                    self.log(
                        "error",
                        "%s CALLBACK ERROR | %s",
                        self.direction,
                        exc
                    )

        except Exception as exc:

            self.log(
                "error",
                "%s FRAME ERROR | %s",
                self.direction,
                exc
            )

    # =========================================================
    # READ LOOP
    # =========================================================

    def run(self):

        self.running = True

        self.log(
            "info",
            "%s UHF READER THREAD STARTED",
            self.direction
        )

        while self.running:

            if self.sock is None:

                if not self.connect():

                    if self.running:
                        time.sleep(
                            self.reconnect_delay
                        )

                    continue

            try:

                data = self.sock.recv(
                    4096
                )

                if not data:

                    raise ConnectionError(
                        "Reader connection closed"
                    )

                self.buffer.extend(
                    data
                )

                frames = self.extract_frames()

                for frame in frames:

                    if not self.running:
                        break

                    self.process_frame(
                        frame
                    )

            except socket.timeout:

                # Timeout is not a connection failure.
                # Continue waiting.
                continue

            except Exception as exc:

                self.log(
                    "error",
                    "%s UHF ERROR | %s",
                    self.direction,
                    exc
                )

                self.close_socket()

                if self.running:

                    time.sleep(
                        self.reconnect_delay
                    )

        self.close_socket()

        self.log(
            "info",
            "%s UHF READER STOPPED",
            self.direction
        )

    # =========================================================
    # START
    # =========================================================

    def start(self):

        if self.thread and self.thread.is_alive():
            return

        self.running = True

        self.thread = threading.Thread(
            target=self.run,
            name="UHFReader",
            daemon=True
        )

        self.thread.start()

    # =========================================================
    # CLOSE
    # =========================================================

    def close(self):

        self.running = False

        self.close_socket()

        if (
            self.thread
            and self.thread.is_alive()
            and threading.current_thread()
            is not self.thread
        ):

            self.thread.join(
                timeout=2
            )