# -*- coding: utf-8 -*-
"""Allow running HubOS via ``python -m hubos``."""
from .cli.main import cli

if __name__ == "__main__":
    cli()  # pylint: disable=no-value-for-parameter
