import inspect
import logging
import threading
import time

try:
    from pymodbus.client import ModbusTcpClient
except ImportError:
    ModbusTcpClient = None


class ModbusRelay:

    def __init__(
        self,
        ip,
        port,
        slave_id,
        entry_address,
        exit_address,
        pulse_seconds=1.0,
        timeout=2,
        retries=2,
        entry_light_address=None,
        exit_light_address=None,
        light_pulse_seconds=None
    ):

        self.ip = ip
        self.port = port
        self.slave_id = slave_id

        self.entry_address = entry_address
        self.exit_address = exit_address

        # Optional traffic-light coils. Leave unset (None) if
        # no light is wired for that direction — trigger_light()
        # will then simply no-op for that direction.
        self.entry_light_address = entry_light_address
        self.exit_light_address = exit_light_address

        self.pulse_seconds = pulse_seconds
        self.light_pulse_seconds = (
            light_pulse_seconds
            if light_pulse_seconds is not None
            else pulse_seconds
        )

        self.timeout = timeout
        self.retries = retries

        self.logger = logging.getLogger("parksmart")

        self.lock = threading.Lock()

        self.client = None

    # ---------------------------------------------------------
    # CONNECT
    # ---------------------------------------------------------

    def connect(self):

        if ModbusTcpClient is None:

            self.logger.error(
                "PYMODBUS NOT INSTALLED"
            )

            return False

        try:

            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass

            self.client = ModbusTcpClient(
                host=self.ip,
                port=self.port,
                timeout=self.timeout
            )

            if self.client.connect():

                self.logger.info(
                    "MODBUS ONLINE | %s:%s",
                    self.ip,
                    self.port
                )

                return True

            self.logger.error(
                "MODBUS CONNECTION FAILED | %s:%s",
                self.ip,
                self.port
            )

        except Exception as exc:

            self.logger.error(
                "MODBUS CONNECT ERROR | %s",
                exc
            )

        return False

    # ---------------------------------------------------------
    # SHARED PULSE LOGIC
    #
    # Both the gate relay and the traffic-light relay just
    # write a coil True, hold for a duration, then write it
    # False again, with the same connect/retry/reconnect
    # handling. Previously trigger() had this logic inline;
    # now both trigger() and trigger_light() call this shared
    # helper so a fix to the retry/reconnect behavior applies
    # to both instead of having to be duplicated.
    # ---------------------------------------------------------

    # ---------------------------------------------------------
    # WRITE COIL (version-agnostic)
    #
    # pymodbus has renamed the unit-identifier keyword across
    # versions: "unit" (2.x), "slave" (3.0-3.6ish), and some
    # newer releases drop it from write_coil() entirely. Rather
    # than hardcode one name that breaks the moment pymodbus is
    # upgraded, detect what the installed client's write_coil()
    # actually accepts and call it that way.
    # ---------------------------------------------------------

    def _write_coil(self, address, value):

        sig = inspect.signature(
            self.client.write_coil
        )

        params = sig.parameters

        kwargs = {
            "address": address,
            "value": value
        }

        for unit_kwarg in ("slave", "unit", "device_id"):

            if unit_kwarg in params:

                kwargs[unit_kwarg] = self.slave_id

                break

        return self.client.write_coil(
            **kwargs
        )

    def _pulse(self, address, duration, label):

        with self.lock:

            for attempt in range(1, self.retries + 2):

                try:

                    if not self.client:
                        self.connect()

                    if not self.client.is_socket_open():

                        if not self.connect():
                            continue

                    self.logger.info(
                        "%s TRIGGER | address=%s | attempt=%s",
                        label,
                        address,
                        attempt
                    )

                    result = self._write_coil(
                        address,
                        True
                    )

                    if result.isError():

                        raise RuntimeError(
                            str(result)
                        )

                    time.sleep(duration)

                    result = self._write_coil(
                        address,
                        False
                    )

                    if result.isError():

                        raise RuntimeError(
                            str(result)
                        )

                    self.logger.info(
                        "%s SUCCESS | address=%s",
                        label,
                        address
                    )

                    return True

                except Exception as exc:

                    self.logger.error(
                        "%s ERROR | address=%s | attempt=%s | %s",
                        label,
                        address,
                        attempt,
                        exc
                    )

                    try:
                        self.client.close()
                    except Exception:
                        pass

                    time.sleep(0.2)

        return False

    # ---------------------------------------------------------
    # GATE RELAY
    # ---------------------------------------------------------

    def trigger(self, direction):

        direction = direction.upper()

        if direction == "ENTRY":

            address = self.entry_address

        elif direction == "EXIT":

            address = self.exit_address

        else:

            self.logger.error(
                "INVALID RELAY DIRECTION | %s",
                direction
            )

            return False

        return self._pulse(
            address,
            self.pulse_seconds,
            "RELAY {}".format(direction)
        )

    # ---------------------------------------------------------
    # TRAFFIC LIGHT
    # ---------------------------------------------------------

    def trigger_light(self, direction):
        """
        Pulse the green traffic light for the given direction.

        Returns True on success, False on failure OR when no
        light address is configured for that direction (this
        is intentionally not an error — a site may only have
        one physical light, or none yet).
        """

        direction = direction.upper()

        if direction == "ENTRY":

            address = self.entry_light_address

        elif direction == "EXIT":

            address = self.exit_light_address

        else:

            self.logger.error(
                "INVALID LIGHT DIRECTION | %s",
                direction
            )

            return False

        if address is None:

            self.logger.warning(
                "LIGHT NOT CONFIGURED | direction=%s",
                direction
            )

            return False

        return self._pulse(
            address,
            self.light_pulse_seconds,
            "LIGHT {}".format(direction)
        )

    # ---------------------------------------------------------
    # CLOSE
    # ---------------------------------------------------------

    def close(self):

        try:

            if self.client:
                self.client.close()

        except Exception:
            pass