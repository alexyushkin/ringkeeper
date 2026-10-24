"""Entrypoint: load the model (downloading it into the cache on first start), then serve."""

import contextlib
import logging
import sys

from ringkeeper_stt.config import ConfigError, load
from ringkeeper_stt.server import Service, serve
from ringkeeper_stt.whisper import WhisperEngine


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load()
    except ConfigError as error:
        print(f"ringkeeper-stt: {error}", file=sys.stderr)
        return 2
    logging.info("loading model %s (cpu, int8)", config.model)
    server = serve(Service(WhisperEngine(config), config))
    logging.info("listening on :%d", config.port)
    with contextlib.suppress(KeyboardInterrupt):
        server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
