import logging
import sys


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
        force=True,
    )
    # httpx logs full request URLs at INFO; keep them out of the default log level.
    logging.getLogger("httpx").setLevel(logging.WARNING)
