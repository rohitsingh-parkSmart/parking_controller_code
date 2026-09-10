import logging
import os
import tempfile
import threading


class OccupancyManager:

    def __init__(self, companies):

        self.logger = logging.getLogger("parksmart")

        self.lock = threading.Lock()

        self.companies = {}

        for company in companies:

            company = dict(company)

            company["capacity"] = int(
                company["capacity"]
            )

            company["occupancy"] = self.read(
                company["occupancy_file"]
            )

            self.companies[
                company["id"]
            ] = company

            self.logger.info(
                "COMPANY LOADED | %s | %s/%s",
                company["name"],
                company["occupancy"],
                company["capacity"]
            )

    # ---------------------------------------------------------
    # READ
    # ---------------------------------------------------------

    @staticmethod
    def read(filename):

        try:

            with open(
                filename,
                "r",
                encoding="utf-8"
            ) as file:

                value = int(
                    file.read().strip()
                )

                return max(
                    0,
                    value
                )

        except Exception:

            return 0

    # ---------------------------------------------------------
    # SAVE
    # ---------------------------------------------------------

    def save(
        self,
        company
    ):

        filename = company[
            "occupancy_file"
        ]

        directory = os.path.dirname(
            filename
        )

        os.makedirs(
            directory,
            exist_ok=True
        )

        fd, temp = tempfile.mkstemp(
            dir=directory
        )

        try:

            with os.fdopen(
                fd,
                "w",
                encoding="utf-8"
            ) as file:

                file.write(
                    str(company["occupancy"])
                )

                file.flush()

                os.fsync(
                    file.fileno()
                )

            os.replace(
                temp,
                filename
            )

        except Exception:

            try:
                os.unlink(temp)
            except Exception:
                pass

            raise

    # ---------------------------------------------------------
    # ENTRY
    # ---------------------------------------------------------

    def entry(
        self,
        company_id
    ):

        with self.lock:

            company = self.companies.get(
                company_id
            )

            if not company:
                return False

            if company["occupancy"] >= company["capacity"]:

                self.logger.warning(
                    "PARKING FULL | %s",
                    company["name"]
                )

                return False

            old = company[
                "occupancy"
            ]

            company["occupancy"] += 1

            self.save(company)

            self.logger.info(
                "OCCUPANCY ENTRY | %s | %s -> %s",
                company["name"],
                old,
                company["occupancy"]
            )

            return True

    # ---------------------------------------------------------
    # EXIT
    # ---------------------------------------------------------

    def exit(
        self,
        company_id
    ):

        with self.lock:

            company = self.companies.get(
                company_id
            )

            if not company:
                return False

            old = company[
                "occupancy"
            ]

            company["occupancy"] = max(
                0,
                old - 1
            )

            self.save(company)

            self.logger.info(
                "OCCUPANCY EXIT | %s | %s -> %s",
                company["name"],
                old,
                company["occupancy"]
            )

            return True

    # ---------------------------------------------------------
    # SNAPSHOT
    # ---------------------------------------------------------

    def snapshot(self):

        with self.lock:

            return [
                dict(company)
                for company in self.companies.values()
            ]

    def total(self):

        companies = self.snapshot()

        return (
            sum(
                c["occupancy"]
                for c in companies
            ),
            sum(
                c["capacity"]
                for c in companies
            )
        )