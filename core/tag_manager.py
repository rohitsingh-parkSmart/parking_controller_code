import json
import logging


class TagManager:

    def __init__(self, filename):

        self.logger = logging.getLogger("parksmart")

        self.tags = {}

        self.load(filename)

    def load(self, filename):

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(file)

        for item in data.get("tags", []):

            if not item.get("enabled", True):
                continue

            tag = str(
                item["tag_id"]
            ).strip().upper()

            self.tags[tag] = item

        self.logger.info(
            "TAG DATABASE | %s authorized tags",
            len(self.tags)
        )

    def get_company_id(self, tag):

        tag = str(
            tag
        ).strip().upper()

        item = self.tags.get(tag)

        if not item:
            return None

        return item.get(
            "company_id"
        )