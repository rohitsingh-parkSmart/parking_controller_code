import logging
import threading


class ParkSmartController:

    def __init__(
        self,
        occupancy,
        tags,
        relay,
        led,
        events,
        parking_name
    ):

        self.logger = logging.getLogger(
            "parksmart"
        )

        self.occupancy = occupancy
        self.tags = tags
        self.relay = relay
        self.led = led
        self.events = events

        self.parking_name = parking_name

        self.led_lock = threading.Lock()

    # ---------------------------------------------------------
    # TAG CALLBACK
    # ---------------------------------------------------------

    def process_tag(
        self,
        tag,
        direction=None
    ):

        try:

            if direction is None:

                self.logger.error(
                    "TAG CALLBACK ERROR | Direction missing | TAG=%s",
                    tag
                )

                return

            direction = str(
                direction
            ).upper().strip()

            if direction not in (
                "ENTRY",
                "EXIT"
            ):

                self.logger.error(
                    "INVALID DIRECTION | TAG=%s | direction=%s",
                    tag,
                    direction
                )

                return

            tag = str(
                tag
            ).strip().upper()

            self.logger.info(
                "%s TAG READ | %s",
                direction,
                tag
            )

            # -------------------------------------------------
            # DUPLICATE PROTECTION
            # -------------------------------------------------

            if self.events.is_duplicate(
                tag,
                direction
            ):

                self.logger.info(
                    "DUPLICATE IGNORED | %s | %s",
                    direction,
                    tag
                )

                return

            # -------------------------------------------------
            # TAG AUTHORIZATION
            # -------------------------------------------------

            company_id = self.tags.get_company_id(
                tag
            )

            if not company_id:

                self.logger.warning(
                    "%s UNAUTHORIZED TAG | %s",
                    direction,
                    tag
                )

                return

            company = self.occupancy.companies.get(
                company_id
            )

            if not company:

                self.logger.error(
                    "COMPANY NOT FOUND | %s",
                    company_id
                )

                return

            self.logger.info(
                "%s COMPANY | %s | %s/%s",
                direction,
                company["name"],
                company["occupancy"],
                company["capacity"]
            )

            # -------------------------------------------------
            # ENTRY
            # -------------------------------------------------

            if direction == "ENTRY":

                if (
                    company["occupancy"]
                    >=
                    company["capacity"]
                ):

                    self.logger.warning(
                        "ENTRY BLOCKED | PARKING FULL | %s",
                        company["name"]
                    )

                    return

                # Relay first
                relay_ok = self.relay.trigger(
                    "ENTRY"
                )

                if not relay_ok:

                    self.logger.error(
                        "ENTRY RELAY FAILED | TAG=%s",
                        tag
                    )

                    return

                # Update occupancy
                self.occupancy.entry(
                    company_id
                )

                self.refresh_led(
                    "entry"
                )

            # -------------------------------------------------
            # EXIT
            # -------------------------------------------------

            elif direction == "EXIT":

                # Relay first
                relay_ok = self.relay.trigger(
                    "EXIT"
                )

                if not relay_ok:

                    self.logger.error(
                        "EXIT RELAY FAILED | TAG=%s",
                        tag
                    )

                    return

                # Update occupancy
                self.occupancy.exit(
                    company_id
                )

                self.refresh_led(
                    "exit"
                )

        except Exception:

            self.logger.exception(
                "PROCESS TAG ERROR | TAG=%s | direction=%s",
                tag,
                direction
            )

    # ---------------------------------------------------------
    # LED
    # ---------------------------------------------------------

    def refresh_led(
        self,
        reason="update"
    ):

        companies = (
            self.occupancy.snapshot()
        )

        total_filled = sum(
            c["occupancy"]
            for c in companies
        )

        total_capacity = sum(
            c["capacity"]
            for c in companies
        )

        self.logger.info(
            "LED REFRESH | reason=%s | total=%s/%s",
            reason,
            total_filled,
            total_capacity
        )

        self.led.update_async(
            self.parking_name,
            companies,
            reason
        )