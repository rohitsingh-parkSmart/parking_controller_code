import json
import logging
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None


class MQTTPublisher:
    """
    Lightweight MQTT publisher for pushing live tag/occupancy
    events to a web app in real time, instead of the web app
    having to poll a file or database.

    Publishes small JSON payloads on topics such as:

        parksmart/event/entry
        parksmart/event/exit
        parksmart/occupancy/<company_id>
        parksmart/occupancy/total

    Fully non-blocking from the caller's perspective: connect()
    runs the network loop on a background thread, and publish()
    never waits on the network — paho queues internally and the
    background thread flushes it. A broker outage will queue up
    to a bounded amount and then drop the oldest, rather than
    ever blocking gate/relay/LED response time.
    """

    def __init__(
        self,
        host,
        port=1883,
        client_id="parksmart",
        username=None,
        password=None,
        base_topic="parksmart",
        keepalive=30,
        reconnect_delay=2,
        qos=0
    ):

        self.host = host
        self.port = int(port)
        self.client_id = client_id
        self.username = username
        self.password = password
        self.base_topic = base_topic.rstrip("/")
        self.keepalive = int(keepalive)
        self.reconnect_delay = float(reconnect_delay)
        self.qos = int(qos)

        self.logger = logging.getLogger("parksmart")

        self.client = None
        self.connected = False

        self._lock = threading.Lock()

        # topic_filter -> callback(topic, payload_dict)
        # Re-applied on every (re)connect since a fresh MQTT
        # session doesn't remember prior subscriptions.
        self._subscriptions = {}

    # ---------------------------------------------------------
    # CONNECT
    # ---------------------------------------------------------

    def start(self):

        if mqtt is None:

            self.logger.error(
                "MQTT ERROR | paho-mqtt not installed "
                "(pip install paho-mqtt --break-system-packages)"
            )

            return False

        self.client = mqtt.Client(
            client_id=self.client_id,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2
        )

        if self.username:

            self.client.username_pw_set(
                self.username,
                self.password
            )

        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        # Wire up any subscriptions registered before start()
        # was called.
        for topic_filter, callback in self._subscriptions.items():

            self.client.message_callback_add(
                topic_filter,
                self._make_message_handler(callback)
            )

        # Built-in automatic reconnect with backoff — paho
        # handles the retry loop for us on its own thread.
        self.client.reconnect_delay_set(
            min_delay=1,
            max_delay=30
        )

        try:

            self.client.connect_async(
                self.host,
                self.port,
                keepalive=self.keepalive
            )

            # loop_start() spawns paho's own network thread —
            # this call returns immediately.
            self.client.loop_start()

            self.logger.info(
                "MQTT CONNECTING | %s:%s",
                self.host,
                self.port
            )

            return True

        except Exception as exc:

            self.logger.error(
                "MQTT CONNECT ERROR | %s",
                exc
            )

            return False

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):

        if reason_code == 0:

            self.connected = True

            self.logger.info(
                "MQTT ONLINE | %s:%s",
                self.host,
                self.port
            )

            # Re-subscribe to everything registered via subscribe()
            # — needed because reconnecting starts a fresh session
            # that doesn't remember previous subscriptions.
            for topic_filter in list(self._subscriptions.keys()):

                try:

                    self.client.subscribe(
                        topic_filter,
                        qos=self.qos
                    )

                except Exception as exc:

                    self.logger.error(
                        "MQTT RESUBSCRIBE ERROR | topic=%s | %s",
                        topic_filter,
                        exc
                    )

        else:

            self.connected = False

            self.logger.error(
                "MQTT CONNECT FAILED | reason=%s",
                reason_code
            )

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):

        self.connected = False

        self.logger.warning(
            "MQTT DISCONNECTED | reason=%s (auto-reconnecting)",
            reason_code
        )

    # ---------------------------------------------------------
    # PUBLISH (non-blocking)
    # ---------------------------------------------------------

    def publish(self, subtopic, payload, retain=False):
        """
        Fire-and-forget publish. Safe to call from any thread,
        including directly from a tag-detection callback — this
        never blocks on network I/O, so it cannot slow down the
        gate/relay/LED response path.
        """

        if self.client is None:
            return False

        try:

            message = json.dumps(
                payload,
                separators=(",", ":"),
                default=str
            )

            topic = "{}/{}".format(
                self.base_topic,
                subtopic.lstrip("/")
            )

            result = self.client.publish(
                topic,
                message,
                qos=self.qos,
                retain=retain
            )

            # publish() only queues the message locally and
            # returns immediately — rc here reflects whether it
            # was queued, not whether the broker received it.
            if result.rc != mqtt.MQTT_ERR_SUCCESS:

                self.logger.warning(
                    "MQTT PUBLISH QUEUE FAILED | topic=%s | rc=%s",
                    topic,
                    result.rc
                )

                return False

            return True

        except Exception as exc:

            self.logger.error(
                "MQTT PUBLISH ERROR | subtopic=%s | %s",
                subtopic,
                exc
            )

            return False

    # ---------------------------------------------------------
    # SUBSCRIBE (for cross-controller sync)
    # ---------------------------------------------------------

    def subscribe(self, subtopic, callback):
        """
        Subscribe to a topic under base_topic and invoke
        callback(topic, payload_dict) whenever a message
        arrives. payload_dict is None if the message body
        wasn't valid JSON. Can be called either before or
        after start() — registration only requires the paho
        client object to exist; the actual subscribe() call
        to the broker happens in start()/on_connect once
        connected (and again on every reconnect).
        """

        topic_filter = "{}/{}".format(
            self.base_topic,
            subtopic.lstrip("/")
        )

        self._subscriptions[topic_filter] = callback

        if self.client is not None:

            self.client.message_callback_add(
                topic_filter,
                self._make_message_handler(callback)
            )

            if self.connected:

                try:

                    self.client.subscribe(
                        topic_filter,
                        qos=self.qos
                    )

                except Exception as exc:

                    self.logger.error(
                        "MQTT SUBSCRIBE ERROR | topic=%s | %s",
                        topic_filter,
                        exc
                    )

                    return False

        return True

    def _make_message_handler(self, callback):

        def _handler(client, userdata, message):

            try:

                raw = message.payload.decode(
                    "utf-8",
                    errors="replace"
                )

                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = None

                callback(
                    message.topic,
                    payload
                )

            except Exception as exc:

                self.logger.error(
                    "MQTT MESSAGE HANDLER ERROR | topic=%s | %s",
                    message.topic,
                    exc
                )

        return _handler

    # ---------------------------------------------------------
    # CONVENIENCE HELPERS
    # ---------------------------------------------------------

    def publish_tag_event(
        self,
        direction,
        tag_id,
        company_id,
        company_name,
        authorized,
        filled=None,
        capacity=None
    ):

        payload = {
            "timestamp": time.time(),
            "direction": direction,
            "tag_id": tag_id,
            "company_id": company_id,
            "company_name": company_name,
            "authorized": bool(authorized),
            "filled": filled,
            "capacity": capacity
        }

        self.publish(
            "event/{}".format(direction.lower()),
            payload
        )

    def publish_occupancy(self, company_id, company_name, filled, capacity):

        payload = {
            "timestamp": time.time(),
            "company_id": company_id,
            "company_name": company_name,
            "filled": filled,
            "capacity": capacity
        }

        # retain=True so a web app that connects AFTER the
        # event still immediately gets the latest known state
        # for this company, instead of waiting for the next car.
        self.publish(
            "occupancy/{}".format(company_id),
            payload,
            retain=True
        )

    def publish_total_occupancy(self, filled, capacity):

        payload = {
            "timestamp": time.time(),
            "filled": filled,
            "capacity": capacity
        }

        self.publish(
            "occupancy/total",
            payload,
            retain=True
        )

    # ---------------------------------------------------------
    # CLOSE
    # ---------------------------------------------------------

    def close(self):

        try:

            if self.client:

                self.client.loop_stop()
                self.client.disconnect()

        except Exception:
            pass