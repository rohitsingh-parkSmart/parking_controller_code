import logging


class RelayManager:

    def __init__(self, modbus):

        self.logger = logging.getLogger(
            "parksmart"
        )

        self.modbus = modbus

    def trigger(self, direction):

        direction = direction.upper()

        self.logger.info(
            "RELAY REQUEST | %s",
            direction
        )

        result = self.modbus.trigger(
            direction
        )

        if result:

            self.logger.info(
                "RELAY SUCCESS | %s",
                direction
            )

        else:

            self.logger.error(
                "RELAY FAILED | %s",
                direction
            )

        return result