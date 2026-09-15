"""Shared logging helpers.

All modules log through a single logger named ``iti_scraper``.  The console
handler is only attached when ``--verbose`` is passed; the file handler is
always present so every failure is recorded in ``scrape.log`` regardless of
verbosity.
"""

import logging

_LOGGER_NAME = "iti_scraper"


def setup_logging(log_file="scrape.log", verbose=False):
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if verbose:
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    # Never propagate duplicate records to the root logger.
    logger.propagate = False
    return logger


def get_logger():
    return logging.getLogger(_LOGGER_NAME)