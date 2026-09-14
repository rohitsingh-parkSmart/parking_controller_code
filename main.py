#!/usr/bin/env python3

import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import inspect
from pathlib import Path

from devices.uhf_reader import UHFReader
from devices.led_display import LEDDisplay
from devices.modbus_relay import ModbusRelay
from devices.mqtt_publisher import MQTTPublisher


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

CONFIG_DIR = BASE_DIR / "config"
DATA_DIR = BASE_DIR / "data"
OCCUPANCY_DIR = DATA_DIR / "occupancy"
LOG_DIR = DATA_DIR / "logs"

CONFIG_DIR.mkdir(
    parents=True,
    exist_ok=True
)

OCCUPANCY_DIR.mkdir(
    parents=True,
    exist_ok=True
)

LOG_DIR.mkdir(
    parents=True,
    exist_ok=True
)


CONFIG_FILE = CONFIG_DIR / "config.json"
TAGS_FILE = CONFIG_DIR / "tags.json"
VEHICLE_SESSIONS_FILE = DATA_DIR / "vehicle_sessions.json"


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger(
    "parksmart"
)

logger.setLevel(
    logging.INFO
)

if not logger.handlers:

    formatter = logging.Formatter(
        "%(asctime)s | "
        "%(levelname)s | "
        "%(name)s | "
        "%(message)s"
    )

    console = logging.StreamHandler(
        sys.stdout
    )

    console.setFormatter(
        formatter
    )

    logger.addHandler(
        console
    )

    file_handler = logging.FileHandler(
        LOG_DIR / "parksmart.log",
        encoding="utf-8"
    )

    file_handler.setFormatter(
        formatter
    )

    logger.addHandler(
        file_handler
    )


# ============================================================
# STOP
# ============================================================

STOP_EVENT = threading.Event()


# ============================================================
# DEFAULT CONFIG
# ============================================================

DEFAULT_CONFIG = {

    "entry_reader": {
        "host": "192.168.100.100",
        "port": 60000,
        "connect_timeout": 3,
        "read_timeout": 1,
        "reconnect_delay": 2
    },

    "exit_reader": {
        "host": "192.168.100.101",
        "port": 60000,
        "connect_timeout": 3,
        "read_timeout": 1,
        "reconnect_delay": 2
    },

    "modbus": {
        "host": "192.168.100.7",
        "port": 502,
        "unit_id": 1,

        "entry_relay_address": 0,
        "exit_relay_address": 1,

        "entry_light_relay_address": 2,
        "exit_light_relay_address": 3,

        "pulse_seconds": 0.5,
        "light_pulse_seconds": 2.0,

        "timeout": 1.0,
        "connect_timeout": 1.0,
        "retries": 2,

        "relay_on_value": True,
        "relay_off_value": False
    },

    "led": {
        "enabled": True,

        "ip": "192.168.100.73",
        "port": 8000,

        "mall_name": "DYP City Mall",

        "module_width": 4,
        "module_height": 3,

        "width": 128,
        "height": 48,

        "font_size": 16,

        "retries": 2,
        "connect_timeout": 2,
        "response_timeout": 1
    },

    "led2": {
        "enabled": True,

        "ip": "192.168.100.10",
        "port": 8000,

        "mall_name": "DYP City Mall",

        "width": 128,
        "height": 48,

        "font_size": 16,

        "retries": 2,
        "connect_timeout": 2,
        "response_timeout": 1
    },

    "mqtt": {
        "enabled": False,

        "host": "192.168.100.50",
        "port": 1883,

        "username": None,
        "password": None,

        "client_id": "parksmart",
        "base_topic": "parksmart",

        "keepalive": 30,
        "qos": 0
    },

    "controller": {

        "duplicate_seconds": 30,

        "led_enabled": True,

        "led_queue_delay": 0.05,

        "led_refresh_seconds": 3
    },

    "companies": [

        {
            "id": "COMP001",
            "name": "PARK LOGI",
            "capacity": 20,
            "occupancy_file":
                "data/occupancy/comp001.txt"
        },

        {
            "id": "COMP002",
            "name": "PARK RET",
            "capacity": 20,
            "occupancy_file":
                "data/occupancy/comp002.txt"
        },

        {
            "id": "COMP003",
            "name": "ACME LABS",
            "capacity": 20,
            "occupancy_file":
                "data/occupancy/comp003.txt"
        },

        {
            "id": "COMP004",
            "name": "ACME TaTa",
            "capacity": 20,
            "occupancy_file":
                "data/occupancy/comp004.txt"
        },

        {
            "id": "COMP005",
            "name": "ACME TCS",
            "capacity": 20,
            "occupancy_file":
                "data/occupancy/comp005.txt"
        },

        {
            "id": "COMP006",
            "name": "ACME TCS 2",
            "capacity": 20,
            "occupancy_file":
                "data/occupancy/comp006.txt"
        }
    ]
}


# ============================================================
# JSON HELPERS
# ============================================================

def load_json(path, default):

    try:

        if not path.exists():
            return default

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as exc:

        logger.error(
            "JSON LOAD ERROR | %s | %s",
            path,
            exc
        )

        return default


def save_json(path, data):

    temp = Path(
        str(path) + ".tmp"
    )

    try:

        with open(
            temp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=4,
                ensure_ascii=False
            )

            f.flush()
            os.fsync(
                f.fileno()
            )

        os.replace(
            temp,
            path
        )

        return True

    except Exception as exc:

        logger.error(
            "JSON SAVE ERROR | %s | %s",
            path,
            exc
        )

        try:
            temp.unlink(
                missing_ok=True
            )
        except Exception:
            pass

        return False


def deep_merge(original, override):

    result = dict(
        original
    )

    for key, value in override.items():

        if (
            key in result
            and isinstance(
                result[key],
                dict
            )
            and isinstance(
                value,
                dict
            )
        ):

            result[key] = deep_merge(
                result[key],
                value
            )

        else:

            result[key] = value

    return result


# ============================================================
# CONFIG
# ============================================================

def load_config():

    user_config = load_json(
        CONFIG_FILE,
        {}
    )

    config = deep_merge(
        DEFAULT_CONFIG,
        user_config
    )

    save_json(
        CONFIG_FILE,
        config
    )

    return config


# ============================================================
# TAG NORMALIZATION
# ============================================================

def normalize_tag(tag):

    if tag is None:
        return ""

    text = str(
        tag
    ).strip().upper()

    # Remove common separators
    text = (
        text
        .replace(" ", "")
        .replace(":", "")
        .replace("-", "")
        .replace("\r", "")
        .replace("\n", "")
    )

    # UHF tag must be exactly 16 hex characters
    # for this Syrotech 4-byte EPC format.
    if len(text) == 16:

        try:

            int(text, 16)

            return text

        except ValueError:
            return ""

    # Accept 8 hex chars and pad to 16
    if len(text) == 8:

        try:

            int(text, 16)

            return text.zfill(16)

        except ValueError:
            return ""

    return text


# ============================================================
# OCCUPANCY
# ============================================================

def load_occupancy(path):

    try:

        path = Path(
            path
        )

        if not path.exists():
            return 0

        value = int(
            path.read_text(
                encoding="utf-8"
            ).strip()
        )

        return max(
            0,
            value
        )

    except Exception:

        return 0


def save_occupancy(path, value):

    path = Path(
        path
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temp = Path(
        str(path) + ".tmp"
    )

    try:

        with open(
            temp,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                str(
                    int(value)
                )
            )

            f.flush()

            os.fsync(
                f.fileno()
            )

        os.replace(
            temp,
            path
        )

        return True

    except Exception as exc:

        logger.error(
            "OCCUPANCY SAVE ERROR | %s | %s",
            path,
            exc
        )

        try:
            temp.unlink(
                missing_ok=True
            )
        except Exception:
            pass

        return False


# ============================================================
# COMPANY
# ============================================================

class Company:

    def __init__(
        self,
        company_id,
        name,
        capacity,
        occupancy_file
    ):

        self.id = str(
            company_id
        )

        self.name = str(
            name
        )

        self.capacity = int(
            capacity
        )

        self.occupancy_file = Path(
            occupancy_file
        )

        if not self.occupancy_file.is_absolute():

            self.occupancy_file = (
                BASE_DIR /
                self.occupancy_file
            )

        self.filled = min(
            self.capacity,
            load_occupancy(
                self.occupancy_file
            )
        )

        self.lock = threading.RLock()

    def entry(self):

        with self.lock:

            if self.filled >= self.capacity:
                return False

            self.filled += 1

            save_occupancy(
                self.occupancy_file,
                self.filled
            )

            return True

    def exit(self):

        with self.lock:

            if self.filled <= 0:
                return False

            self.filled -= 1

            save_occupancy(
                self.occupancy_file,
                self.filled
            )

            return True


# ============================================================
# CONTROLLER
# ============================================================

class ParkSmartController:

    def __init__(
        self,
        config
    ):

        self.config = config

        self.running = True

        self.lock = threading.RLock()

        self.companies = []

        self.tag_map = {}

        self.last_tag_time = {}

        self.led_lock = threading.Lock()

        self.transaction_lock = threading.Lock()

        self.led_thread = None

        self.entry_reader = None

        self.exit_reader = None

        self.relay = None

        self.led = None
        self.led2 = None

        self.mqtt = None

        self.vehicle_sessions = {}
        self.vehicle_sessions_lock = threading.RLock()

        self._tags_mtime = None
        self._tags_sync_stop = threading.Event()
        self._applying_remote_tags = False
        self._status_stop = threading.Event()
        self._device_status = {}
        self._device_status_lock = threading.Lock()
        self._led_refresh_stop = threading.Event()
        self._last_led_event = (
            "UPDATE",
            "",
            None,
            False,
            None
        )
        self._last_led_mode = "normal"

        self.load_vehicle_sessions()

        self.load_companies()

        self.load_tags()

        self.setup_devices()

        self.start_tag_sync()

        threading.Thread(
            target=self._device_status_loop,
            name="DeviceStatus",
            daemon=True
        ).start()

        threading.Thread(
            target=self._led_refresh_loop,
            name="LEDRefresh",
            daemon=True
        ).start()

    # ========================================================
    # COMPANIES
    # ========================================================

    def load_companies(self):

        for item in self.config.get(
            "companies",
            []
        ):

            company_id = item.get(
                "id"
            )

            name = item.get(
                "name",
                company_id
            )

            capacity = int(
                item.get(
                    "capacity",
                    0
                )
            )

            occupancy_file = item.get(
                "occupancy_file",
                f"data/occupancy/{company_id}.txt"
            )

            company = Company(
                company_id,
                name,
                capacity,
                occupancy_file
            )

            self.companies.append(
                company
            )

            logger.info(
                "COMPANY LOADED | %s | %s/%s",
                name,
                company.filled,
                company.capacity
            )

    # ========================================================
    # TAG DATABASE
    # ========================================================

    def load_tags(self):

        data = load_json(
            TAGS_FILE,
            []
        )

        new_tag_map = {}

        if isinstance(
            data,
            dict
        ):

            items = []

            for tag_id, value in data.items():

                if isinstance(
                    value,
                    dict
                ):

                    item = dict(
                        value
                    )

                    item[
                        "tag_id"
                    ] = tag_id

                else:

                    item = {
                        "tag_id": tag_id,
                        "company_id": str(
                            value
                        )
                    }

                items.append(
                    item
                )

        elif isinstance(
            data,
            list
        ):

            items = data

        else:

            items = []

        for item in items:

            if not isinstance(
                item,
                dict
            ):
                continue

            raw_tag = item.get(
                "tag_id",
                item.get(
                    "tag"
                )
            )

            tag_id = normalize_tag(
                raw_tag
            )

            company_id = str(
                item.get(
                    "company_id",
                    item.get(
                        "company",
                        ""
                    )
                )
            )

            if not tag_id:
                continue

            if not company_id:
                continue

            new_tag_map[
                tag_id
            ] = item

        # Atomic swap of the whole dict reference (safe under
        # the GIL) instead of clearing/mutating self.tag_map in
        # place — the entry/exit reader threads call
        # find_company() concurrently and must never see a
        # half-rebuilt map.
        self.tag_map = new_tag_map

        try:

            self._tags_mtime = TAGS_FILE.stat().st_mtime

        except Exception:

            self._tags_mtime = None

        logger.info(
            "TAG DATABASE | %d authorized tags",
            len(self.tag_map)
        )

    # ========================================================
    # TAG DATABASE — HOT RELOAD & CROSS-CONTROLLER SYNC
    #
    # tags.json can be edited by hand, by an admin tool, or by
    # an inbound MQTT sync from another controller. Whichever
    # happens, every running controller ends up with the same
    # tag_map:
    #
    #  1. A background thread polls this controller's local
    #     tags.json for mtime changes (covers hand-edits and
    #     any admin tool that writes the file directly).
    #  2. Whenever THIS controller detects a local change, it
    #     reloads tag_map and publishes the full tag list to a
    #     retained MQTT topic, so every other controller (and
    #     any late-joining one) picks it up immediately.
    #  3. Whenever a tag-list message arrives FROM MQTT (i.e.
    #     from another controller), this controller writes it
    #     to its own local tags.json and reloads — without
    #     re-publishing what it just received, which would
    #     otherwise create an infinite echo between controllers.
    # ========================================================

    def start_tag_sync(self):

        if self.mqtt is not None:

            self.mqtt.subscribe(
                "tags/list",
                self._on_remote_tags_update
            )

        threading.Thread(
            target=self._tag_sync_poll_loop,
            name="TagSync",
            daemon=True
        ).start()

    def _tag_sync_poll_loop(self):

        poll_seconds = float(
            self.config.get(
                "controller",
                {}
            ).get(
                "tags_poll_seconds",
                5
            )
        )

        while not self._tags_sync_stop.is_set():

            time.sleep(
                poll_seconds
            )

            try:

                current_mtime = TAGS_FILE.stat().st_mtime

            except Exception:

                continue

            if current_mtime != self._tags_mtime:

                logger.info(
                    "TAG DATABASE | tags.json changed on disk, reloading"
                )

                self.load_tags()

                # Only publish if THIS controller is the one that
                # detected the change locally — not when we just
                # wrote the file ourselves in response to a remote
                # update (that flag is set/cleared around that
                # write in _on_remote_tags_update).
                if not self._applying_remote_tags:

                    self.publish_tag_list()

    def publish_tag_list(self):

        if self.mqtt is None:
            return

        tags_payload = list(
            self.tag_map.values()
        )

        self.mqtt.publish(
            "tags/list",
            {
                "tags": tags_payload,
                "updated_at": time.time()
            },
            retain=True
        )

    def _on_remote_tags_update(self, topic, payload):

        if not payload or "tags" not in payload:
            return

        try:

            self._applying_remote_tags = True

            ok = save_json(
                TAGS_FILE,
                payload["tags"]
            )

            if ok:

                self.load_tags()

                logger.info(
                    "TAG DATABASE | synced from MQTT | %d tags",
                    len(self.tag_map)
                )

        except Exception as exc:

            logger.error(
                "TAG SYNC ERROR | %s",
                exc
            )

        finally:

            self._applying_remote_tags = False

    # ========================================================
    # VEHICLE SESSIONS (car number, entry time, exit time,
    # authorized status per vehicle — for the web dashboard)
    # ========================================================

    def load_vehicle_sessions(self):

        data = load_json(
            VEHICLE_SESSIONS_FILE,
            {}
        )

        if not isinstance(data, dict):
            data = {}

        with self.vehicle_sessions_lock:

            self.vehicle_sessions = data

        logger.info(
            "VEHICLE SESSIONS | %d records loaded",
            len(data)
        )

    def save_vehicle_sessions(self):

        with self.vehicle_sessions_lock:

            snapshot = dict(
                self.vehicle_sessions
            )

        save_json(
            VEHICLE_SESSIONS_FILE,
            snapshot
        )

    def record_vehicle_event(
        self,
        direction,
        tag_id,
        tag_item,
        company,
        authorized
    ):
        """
        Update this vehicle's session record (car number, entry
        time, exit time, authorized status) and publish it to
        MQTT (retained) so the web dashboard reflects it
        immediately and a page that (re)connects later still
        sees each vehicle's latest known status.
        """

        now = time.time()

        car_number = (
            tag_item.get("car_number", "")
            if tag_item
            else ""
        )

        with self.vehicle_sessions_lock:

            record = self.vehicle_sessions.get(
                tag_id,
                {}
            )

            record["tag_id"] = tag_id
            record["car_number"] = car_number
            record["company_id"] = (
                company.id if company else None
            )
            record["company_name"] = (
                company.name if company else None
            )
            record["authorized"] = bool(
                authorized
            )

            if direction == "ENTRY":

                record["entry_time"] = now
                # A fresh entry starts a new session — clear any
                # previous exit_time so the vehicle shows as
                # currently parked, not still showing an old
                # exit from a prior visit.
                record["exit_time"] = None

            else:

                record["exit_time"] = now

                if "entry_time" not in record:
                    record["entry_time"] = None

            self.vehicle_sessions[tag_id] = record

            record_copy = dict(
                record
            )

        self.save_vehicle_sessions()

        if self.mqtt is not None:

            self.mqtt.publish(
                "vehicle/{}".format(tag_id),
                record_copy,
                retain=True
            )

    # ========================================================
    # FIND COMPANY
    # ========================================================

    def find_company(
        self,
        tag_id
    ):

        tag_id = normalize_tag(
            tag_id
        )

        data = self.tag_map.get(
            tag_id
        )

        if not data:
            return None

        company_id = str(
            data.get(
                "company_id",
                data.get(
                    "company",
                    ""
                )
            )
        )

        for company in self.companies:

            if company.id == company_id:
                return company

        return None

    # ========================================================
    # DEVICES
    # ========================================================

    def setup_devices(self):

        modbus_cfg = self.config[
            "modbus"
        ]

        # We don't reliably know the exact keyword-argument
        # names ModbusRelay.__init__ uses on this deployment
        # (it has drifted between "host"/"ip", "unit_id"/
        # "slave_id", "entry_relay_address"/"entry_address",
        # etc. across versions of this file). Instead of
        # hardcoding a guess that breaks again if the class
        # changes, inspect the real constructor at runtime and
        # only pass the argument names it actually accepts.

        candidate_kwargs = {

            "host": modbus_cfg["host"],
            "ip": modbus_cfg["host"],

            "port": int(
                modbus_cfg.get("port", 502)
            ),

            "unit_id": int(
                modbus_cfg.get("unit_id", 1)
            ),
            "slave_id": int(
                modbus_cfg.get("unit_id", 1)
            ),

            "entry_relay_address": int(
                modbus_cfg.get("entry_relay_address", 0)
            ),
            "entry_address": int(
                modbus_cfg.get("entry_relay_address", 0)
            ),

            "exit_relay_address": int(
                modbus_cfg.get("exit_relay_address", 1)
            ),
            "exit_address": int(
                modbus_cfg.get("exit_relay_address", 1)
            ),

            "pulse_seconds": float(
                modbus_cfg.get("pulse_seconds", 0.5)
            ),

            "timeout": float(
                modbus_cfg.get("timeout", 1.0)
            ),
            "connect_timeout": float(
                modbus_cfg.get("connect_timeout", 1.0)
            ),

            "retries": int(
                modbus_cfg.get("retries", 2)
            ),

            "relay_on_value": bool(
                modbus_cfg.get("relay_on_value", True)
            ),
            "relay_off_value": bool(
                modbus_cfg.get("relay_off_value", False)
            ),

            # Traffic light coils — pulsed green whenever a
            # matched tag opens the entry/exit gate.
            "entry_light_address": int(
                modbus_cfg.get("entry_light_relay_address", 2)
            ),
            "entry_light_relay_address": int(
                modbus_cfg.get("entry_light_relay_address", 2)
            ),
            "exit_light_address": int(
                modbus_cfg.get("exit_light_relay_address", 3)
            ),
            "exit_light_relay_address": int(
                modbus_cfg.get("exit_light_relay_address", 3)
            ),
            "light_pulse_seconds": float(
                modbus_cfg.get("light_pulse_seconds", 2.0)
            ),
        }

        sig = inspect.signature(
            ModbusRelay.__init__
        )

        accepted_names = set(
            sig.parameters.keys()
        ) - {"self"}

        relay_kwargs = {
            name: value
            for name, value
            in candidate_kwargs.items()
            if name in accepted_names
        }

        # Any required (no-default) constructor parameter we
        # weren't able to map is a real problem worth failing
        # loudly on, rather than silently omitting it.
        missing_required = [
            p.name
            for p in sig.parameters.values()
            if p.name != "self"
            and p.default is inspect.Parameter.empty
            and p.name not in relay_kwargs
        ]

        if missing_required:

            logger.error(
                "MODBUS INIT ERROR | ModbusRelay.__init__ "
                "requires parameter(s) %s that could not be "
                "matched from config.json's \"modbus\" section",
                missing_required
            )

        self.relay = ModbusRelay(
            **relay_kwargs
        )

        logger.info(
            "MODBUS INITIALIZED | %s:%s | device_id=%s | ENTRY=%s | EXIT=%s",
            modbus_cfg["host"],
            modbus_cfg["port"],
            modbus_cfg.get(
                "unit_id",
                1
            ),
            modbus_cfg.get(
                "entry_relay_address",
                0
            ),
            modbus_cfg.get(
                "exit_relay_address",
                1
            )
        )

        led_cfg = self.config[
            "led"
        ]

        if led_cfg.get(
            "enabled",
            True
        ):

            self.led = LEDDisplay(
                ip=led_cfg.get(
                    "ip",
                    "192.168.100.73"
                ),
                port=int(
                    led_cfg.get(
                        "port",
                        8000
                    )
                ),
                width=int(
                    led_cfg.get(
                        "width",
                        96
                    )
                ),
                height=int(
                    led_cfg.get(
                        "height",
                        64
                    )
                ),
                retries=int(
                    led_cfg.get(
                        "retries",
                        2
                    )
                ),
                connect_timeout=float(
                    led_cfg.get(
                        "connect_timeout",
                        2
                    )
                ),
                response_timeout=float(
                    led_cfg.get(
                        "response_timeout",
                        1
                    )
                ),
                font_size=int(
                    led_cfg.get(
                        "font_size",
                        16
                    )
                )
            )

            logger.info(
                "LED | %s:%s",
                led_cfg.get(
                    "ip"
                ),
                led_cfg.get(
                    "port"
                )
            )

        led2_cfg = self.config.get(
            "led2",
            {}
        )

        if led2_cfg.get(
            "enabled",
            False
        ):

            self.led2 = LEDDisplay(
                ip=led2_cfg.get(
                    "ip",
                    "192.168.100.10"
                ),
                port=int(
                    led2_cfg.get(
                        "port",
                        8000
                    )
                ),
                width=int(
                    led2_cfg.get(
                        "width",
                        128
                    )
                ),
                height=int(
                    led2_cfg.get(
                        "height",
                        48
                    )
                ),
                retries=int(
                    led2_cfg.get(
                        "retries",
                        2
                    )
                ),
                connect_timeout=float(
                    led2_cfg.get(
                        "connect_timeout",
                        2
                    )
                ),
                response_timeout=float(
                    led2_cfg.get(
                        "response_timeout",
                        1
                    )
                ),
                font_size=int(
                    led2_cfg.get(
                        "font_size",
                        16
                    )
                )
            )

            logger.info(
                "LED2 | %s:%s",
                led2_cfg.get(
                    "ip"
                ),
                led2_cfg.get(
                    "port"
                )
            )

        mqtt_cfg = self.config.get(
            "mqtt",
            {}
        )

        if mqtt_cfg.get(
            "enabled",
            False
        ):

            self.mqtt = MQTTPublisher(
                host=mqtt_cfg["host"],
                port=int(
                    mqtt_cfg.get("port", 1883)
                ),
                client_id=mqtt_cfg.get(
                    "client_id",
                    "parksmart"
                ),
                username=mqtt_cfg.get(
                    "username"
                ),
                password=mqtt_cfg.get(
                    "password"
                ),
                base_topic=mqtt_cfg.get(
                    "base_topic",
                    "parksmart"
                ),
                keepalive=int(
                    mqtt_cfg.get("keepalive", 30)
                ),
                qos=int(
                    mqtt_cfg.get("qos", 0)
                )
            )

            # start() is non-blocking — it kicks off a
            # background connect + paho's own network thread
            # and returns immediately, so a slow/unreachable
            # broker never delays startup or tag processing.
            self.mqtt.start()

            logger.info(
                "MQTT | %s:%s",
                mqtt_cfg["host"],
                mqtt_cfg.get("port", 1883)
            )

    # ========================================================
    # START READERS
    # ========================================================

    def start_readers(self):

        entry_cfg = self.config[
            "entry_reader"
        ]

        exit_cfg = self.config[
            "exit_reader"
        ]

        self.entry_reader = UHFReader(
            entry_cfg,
            logger,
            self.handle_tag,
            direction="ENTRY"
        )

        self.exit_reader = UHFReader(
            exit_cfg,
            logger,
            self.handle_tag,
            direction="EXIT"
        )

        threading.Thread(
            target=self.entry_reader.run,
            name="EntryUHF",
            daemon=True
        ).start()

        threading.Thread(
            target=self.exit_reader.run,
            name="ExitUHF",
            daemon=True
        ).start()

        logger.info(
            "ENTRY UHF READER THREAD STARTED"
        )

        logger.info(
            "EXIT UHF READER THREAD STARTED"
        )

    # ========================================================
    # DUPLICATE FILTER
    # ========================================================

    def is_duplicate(
        self,
        tag_id,
        direction
    ):

        key = (
            direction,
            tag_id
        )

        now = time.monotonic()

        duplicate_seconds = float(
            self.config[
                "controller"
            ].get(
                "duplicate_seconds",
                30
            )
        )

        with self.lock:

            old_time = self.last_tag_time.get(
                key
            )

            if (
                old_time is not None
                and
                (now - old_time)
                < duplicate_seconds
            ):

                return True

            self.last_tag_time[
                key
            ] = now

            # Cleanup old entries
            if len(
                self.last_tag_time
            ) > 500:

                cutoff = (
                    now -
                    duplicate_seconds
                )

                self.last_tag_time = {
                    k: v
                    for k, v
                    in self.last_tag_time.items()
                    if v >= cutoff
                }

            return False

    # ========================================================
    # TAG HANDLER
    # ========================================================

    def handle_tag(
        self,
        tag_id,
        direction="ENTRY"
    ):

        with self.transaction_lock:
            self._handle_tag_transaction(
                tag_id,
                direction
            )

    def _handle_tag_transaction(
        self,
        tag_id,
        direction="ENTRY"
    ):

        direction = str(
            direction
        ).upper()

        tag_id = normalize_tag(
            tag_id
        )

        if not tag_id:

            logger.warning(
                "%s INVALID TAG",
                direction
            )

            return

        logger.info(
            "%s TAG READ | %s",
            direction,
            tag_id
        )

        # ----------------------------------------------------
        # Duplicate
        # ----------------------------------------------------

        if self.is_duplicate(
            tag_id,
            direction
        ):

            logger.info(
                "DUPLICATE IGNORED | %s | %s",
                direction,
                tag_id
            )

            return

        # ----------------------------------------------------
        # Find authorized tag
        # ----------------------------------------------------

        tag_item = self.tag_map.get(tag_id)

        company = self.find_company(
            tag_id
        )

        vehicle_number = str(
            tag_item.get(
                "car_number",
                tag_item.get(
                    "vehicle_number",
                    tag_item.get("owner_car_number", "")
                )
            )
            or ""
        ).strip().upper() if tag_item else ""

        role = str(
            tag_item.get(
                "status",
                tag_item.get(
                    "rfid_status",
                    tag_item.get("role", "")
                )
            )
            or ""
        ).strip().upper() if tag_item else ""

        owner_value = (
            tag_item.get(
                "owner",
                tag_item.get("is_owner", False)
            )
            if tag_item
            else False
        )

        rfid_status = (
            "OWNER"
            if owner_value or role == "OWNER"
            else "REGISTERED"
            if tag_item
            else "VISITOR"
        )

        logger.info(
            "%s TAG RESOLUTION | TAG=%s | CAR=%s | STATUS=%s",
            direction,
            tag_id,
            vehicle_number or "-",
            rfid_status
        )

        if direction == "ENTRY":

            filled, capacity = self.total_occupancy()

            if filled >= capacity:

                logger.warning(
                    "ENTRY BLOCKED | PARKING FULL | TAG=%s | %s/%s",
                    tag_id,
                    filled,
                    capacity
                )

                self.show_parking_full(
                    direction,
                    tag_id
                )

                return

        if company is None:

            logger.warning(
                "%s UNAUTHORIZED TAG | %s",
                direction,
                tag_id
            )

            # Show unauthorized vehicle on LED
            self.update_led_event(
                direction,
                tag_id,
                None,
                False,
                self.tag_map.get(tag_id)
            )

            self.publish_mqtt_event(
                direction,
                tag_id,
                None,
                authorized=False
            )

            self.record_vehicle_event(
                direction,
                tag_id,
                self.tag_map.get(tag_id),
                None,
                authorized=False
            )

            return

        logger.info(
            "%s COMPANY | %s | %s/%s",
            direction,
            company.name,
            company.filled,
            company.capacity
        )

        # ----------------------------------------------------
        # Capacity
        # ----------------------------------------------------

        if direction == "ENTRY":

            if company.filled >= company.capacity:

                logger.warning(
                    "ENTRY PARKING FULL | %s",
                    company.name
                )

                self.update_led_event(
                    direction,
                    tag_id,
                    company,
                    False,
                    self.tag_map.get(tag_id)
                )

                self.publish_mqtt_event(
                    direction,
                    tag_id,
                    company,
                    authorized=False
                )

                self.record_vehicle_event(
                    direction,
                    tag_id,
                    self.tag_map.get(tag_id),
                    company,
                    authorized=False
                )

                return

        # ----------------------------------------------------
        # Relay
        # ----------------------------------------------------

        if not self.trigger_relay(
            direction,
            tag_id
        ):

            logger.error(
                "%s TRANSACTION ABORTED | relay failed | TAG=%s",
                direction,
                tag_id
            )

            return

        if direction == "ENTRY":
            updated = company.entry()
        else:
            updated = company.exit()

        if not updated:

            logger.error(
                "%s TRANSACTION ABORTED | occupancy update failed | TAG=%s",
                direction,
                tag_id
            )

            return

        logger.info(
            "%s OCCUPANCY | %s | %s/%s",
            direction,
            company.name,
            company.filled,
            company.capacity
        )

        # ----------------------------------------------------
        # LED and event publication
        # ----------------------------------------------------

        self.update_led_event(
            direction,
            tag_id,
            company,
            True,
            self.tag_map.get(tag_id)
        )

        self.publish_mqtt_event(
            direction,
            tag_id,
            company,
            authorized=True
        )

        self.record_vehicle_event(
            direction,
            tag_id,
            self.tag_map.get(tag_id),
            company,
            authorized=True
        )

    # ========================================================
    # MQTT
    # ========================================================

    def publish_mqtt_event(
        self,
        direction,
        tag_id,
        company,
        authorized
    ):

        if self.mqtt is None:
            return

        # Fire-and-forget on a background thread — MQTTPublisher
        # itself doesn't block, but keeping this off the calling
        # thread (which may be the UHF reader's own read loop)
        # means even a slow json.dumps/logging hiccup can never
        # delay the next tag read or the relay/LED response.

        threading.Thread(
            target=self._publish_mqtt_event_worker,
            args=(direction, tag_id, company, authorized),
            daemon=True
        ).start()

    def _publish_mqtt_event_worker(
        self,
        direction,
        tag_id,
        company,
        authorized
    ):

        try:

            self.mqtt.publish_tag_event(
                direction=direction,
                tag_id=tag_id,
                company_id=(
                    company.id if company else None
                ),
                company_name=(
                    company.name if company else None
                ),
                authorized=authorized,
                filled=(
                    company.filled if company else None
                ),
                capacity=(
                    company.capacity if company else None
                )
            )

            if company:

                self.mqtt.publish_occupancy(
                    company.id,
                    company.name,
                    company.filled,
                    company.capacity
                )

            filled, capacity = self.total_occupancy()

            self.mqtt.publish_total_occupancy(
                filled,
                capacity
            )

        except Exception as exc:

            logger.error(
                "MQTT PUBLISH ERROR | %s | TAG=%s | %s",
                direction,
                tag_id,
                exc
            )

    # ========================================================
    # LED
    # ========================================================

    def show_parking_full(
        self,
        direction,
        tag_id,
        force=False
    ):

        self._last_led_event = (
            direction,
            tag_id,
            None,
            False,
            None
        )
        self._last_led_mode = "full"

        companies_payload = [

            {
                "name": c.name,
                "capacity": c.capacity,
                "occupancy": c.filled
            }

            for c in self.companies
        ]

        reason = "{}_parking_full".format(
            direction.lower()
        )

        logger.info(
            "LED PARKING FULL | direction=%s | TAG=%s",
            direction,
            tag_id
        )

        if self.led is not None:

            self.led.update_async(
                parking_name=self.config.get(
                    "led", {}
                ).get(
                    "mall_name",
                    "PARK SMART"
                ),
                companies=companies_payload,
                reason=reason,
                display_mode="full",
                force=force
            )

        if self.led2 is not None:

            self.led2.update_async(
                parking_name=self.config.get(
                    "led2", {}
                ).get(
                    "mall_name",
                    "PARK SMART"
                ),
                companies=companies_payload,
                reason=reason,
                display_mode="full",
                force=force
            )

    def update_led_event(
        self,
        direction,
        tag_id,
        company,
        authorized,
        tag_item=None,
        force=False
    ):

        # Only bail out entirely if NEITHER panel is configured.
        # If just one of the two is set up, we still want to
        # update that one.
        if self.led is None and self.led2 is None:
            return

        self._last_led_event = (
            direction,
            tag_id,
            company,
            authorized,
            tag_item
        )
        self._last_led_mode = "normal"

        # Important:
        # LED network failure must NOT stop UHF.
        #
        # LEDDisplay.update_async() builds the payload via
        # build_payload() (the "commands" row format the
        # display firmware actually parses) and sends it on
        # its own background thread, so we just hand it the
        # data it needs.

        companies_payload = [

            {
                "name": c.name,
                "capacity": c.capacity,
                "occupancy": c.filled
            }

            for c in self.companies
        ]

        reason = "{}_{}".format(
            direction.lower(),
            "vehicle" if authorized else "denied"
        )

        vehicle_number = ""
        rfid_status = "VISITOR"

        if tag_item:

            vehicle_number = str(
                tag_item.get(
                    "car_number",
                    tag_item.get(
                        "vehicle_number",
                        tag_item.get("owner_car_number", "")
                    )
                )
                or ""
            ).strip().upper()

            owner_value = tag_item.get(
                "owner",
                tag_item.get("is_owner", False)
            )

            role = str(
                tag_item.get(
                    "status",
                    tag_item.get(
                        "rfid_status",
                        tag_item.get("role", "")
                    )
                )
                or ""
            ).strip().upper()

            rfid_status = (
                "OWNER"
                if owner_value or role == "OWNER"
                else "REGISTERED"
            )

        if self.led is not None:

            self.led.update_async(
                parking_name=self.config.get(
                    "led", {}
                ).get(
                    "mall_name",
                    "PARK SMART"
                ),
                companies=companies_payload,
                reason=reason,
                display_mode="parking",
                force=force
            )

        if self.led2 is not None:

            self.led2.update_async(
                parking_name=self.config.get(
                    "led2", {}
                ).get(
                    "mall_name",
                    "PARK SMART"
                ),
                companies=companies_payload,
                reason=reason,
                vehicle_number=vehicle_number,
                rfid_status=rfid_status,
                display_mode="rfid",
                force=force
            )

    # ========================================================
    # RELAY
    # ========================================================

    def trigger_relay(
        self,
        direction,
        tag_id
    ):

        logger.info(
            "RELAY REQUEST | %s",
            direction
        )

        try:

            if direction == "ENTRY":

                result = self.relay.trigger(
                    "ENTRY"
                )

            else:

                result = self.relay.trigger(
                    "EXIT"
                )

            if result:

                logger.info(
                    "RELAY SUCCESS | %s | TAG=%s",
                    direction,
                    tag_id
                )

            else:

                logger.error(
                    "RELAY FAILED | %s | TAG=%s",
                    direction,
                    tag_id
                )

        except Exception as exc:

            logger.error(
                "RELAY ERROR | %s | TAG=%s | %s",
                direction,
                tag_id,
                exc
            )

            return False

        if not result:
            return False

        # ----------------------------------------------------
        # Traffic light — pulse GREEN whenever a tag is
        # matched (trigger_relay is only ever called from
        # handle_tag after the tag matched an authorized
        # company and occupancy updated successfully).
        # ----------------------------------------------------

        if not hasattr(self.relay, "trigger_light"):

            # Deployed ModbusRelay hasn't been updated with
            # the trigger_light() method yet — skip quietly
            # rather than crash the whole tag event.
            return True

        try:

            light_result = self.relay.trigger_light(
                direction
            )

            if light_result:

                logger.info(
                    "TRAFFIC LIGHT GREEN | %s | TAG=%s",
                    direction,
                    tag_id
                )

            else:

                logger.warning(
                    "TRAFFIC LIGHT NOT TRIGGERED | %s | TAG=%s "
                    "(no light address configured, or relay busy)",
                    direction,
                    tag_id
                )

        except Exception as exc:

            logger.error(
                "TRAFFIC LIGHT ERROR | %s | TAG=%s | %s",
                direction,
                tag_id,
                exc
            )

        return True

    # ========================================================
    # TOTAL OCCUPANCY
    # ========================================================

    def total_occupancy(self):

        filled = sum(
            company.filled
            for company
            in self.companies
        )

        capacity = sum(
            company.capacity
            for company
            in self.companies
        )

        return filled, capacity

    # ========================================================
    # DEVICE STATUS
    # ========================================================

    def _report_device_status(self, name, online):

        now = time.monotonic()
        state = "ONLINE" if online else "OFFLINE"

        with self._device_status_lock:

            previous = self._device_status.get(name)

            if (
                previous
                and previous[0] == state
                and now - previous[1] < 10
            ):
                return

            self._device_status[name] = (state, now)

        logger.info(
            "DEVICE STATUS | %s | %s",
            name,
            state
        )

    def _tcp_online(self, host, port):

        try:

            with socket.create_connection(
                (host, int(port)),
                timeout=1
            ):
                return True

        except OSError:
            return False

    def _device_status_loop(self):

        while not self._status_stop.wait(10):

            for name, reader in (
                ("ENTRY READER", self.entry_reader),
                ("EXIT READER", self.exit_reader)
            ):

                self._report_device_status(
                    name,
                    reader is not None and reader.sock is not None
                )

            if self.led is not None:

                self._report_device_status(
                    "LED",
                    self._tcp_online(
                        self.led.ip,
                        self.led.port
                    )
                )

            if self.led2 is not None:

                self._report_device_status(
                    "LED2",
                    self._tcp_online(
                        self.led2.ip,
                        self.led2.port
                    )
                )

            relay_client = (
                self.relay.client
                if self.relay is not None
                else None
            )

            relay_online = False

            if relay_client is not None:

                try:
                    relay_online = relay_client.is_socket_open()
                except Exception:
                    relay_online = False

            self._report_device_status(
                "MODBUS",
                relay_online
            )

    def _led_refresh_loop(self):

        interval = float(
            self.config.get(
                "controller",
                {}
            ).get(
                "led_refresh_seconds",
                3
            )
        )

        while not self._led_refresh_stop.wait(interval):

            try:

                if self._last_led_mode == "full":

                    direction, tag_id, _, _, _ = self._last_led_event

                    self.show_parking_full(
                        direction,
                        tag_id,
                        force=True
                    )

                else:

                    direction, tag_id, company, authorized, tag_item = (
                        self._last_led_event
                    )

                    self.update_led_event(
                        direction,
                        tag_id,
                        company,
                        authorized,
                        tag_item,
                        force=True
                    )

            except Exception:

                logger.exception(
                    "LED PERIODIC REFRESH ERROR"
                )

    # ========================================================
    # STOP
    # ========================================================

    def stop(self):

        if not self.running:
            return

        self.running = False

        STOP_EVENT.set()

        self._tags_sync_stop.set()
        self._status_stop.set()
        self._led_refresh_stop.set()

        logger.info(
            "PARKSMART STOPPING"
        )

        for reader in (
            self.entry_reader,
            self.exit_reader
        ):

            if reader:

                try:
                    reader.close()
                except Exception:
                    pass

        if self.relay:

            try:
                self.relay.close()
            except Exception:
                pass

        if self.mqtt:

            try:
                self.mqtt.close()
            except Exception:
                pass


# ============================================================
# SIGNAL
# ============================================================

controller_instance = None


def signal_handler(
    signum,
    frame
):

    logger.info(
        "SHUTDOWN SIGNAL | %s",
        signum
    )

    STOP_EVENT.set()

    if controller_instance:

        controller_instance.stop()


# ============================================================
# MAIN
# ============================================================

def main():

    global controller_instance

    logger.info(
        "=========================================="
    )

    logger.info(
        "PARKSMART CONTROLLER STARTING"
    )

    logger.info(
        "=========================================="
    )

    config = load_config()

    controller = ParkSmartController(
        config
    )

    controller_instance = controller

    signal.signal(
        signal.SIGINT,
        signal_handler
    )

    signal.signal(
        signal.SIGTERM,
        signal_handler
    )

    logger.info(
        "ENTRY UHF | %s:%s",
        config["entry_reader"]["host"],
        config["entry_reader"]["port"]
    )

    logger.info(
        "EXIT UHF | %s:%s",
        config["exit_reader"]["host"],
        config["exit_reader"]["port"]
    )

    logger.info(
        "MODBUS | %s:%s",
        config["modbus"]["host"],
        config["modbus"]["port"]
    )

    logger.info(
        "LED | %s:%s",
        config["led"]["ip"],
        config["led"]["port"]
    )

    filled, capacity = (
        controller.total_occupancy()
    )

    logger.info(
        "TOTAL OCCUPANCY | %s/%s",
        filled,
        capacity
    )

    controller.start_readers()

    logger.info(
        "PARKSMART CONTROLLER RUNNING"
    )

    try:

        while not STOP_EVENT.is_set():

            time.sleep(
                1
            )

    finally:

        controller.stop()

        logger.info(
            "PARKSMART CONTROLLER STOPPED"
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        logger.exception(
            "FATAL APPLICATION ERROR | %s",
            exc
        )

        sys.exit(1)