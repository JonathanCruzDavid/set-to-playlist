"""Entry point: python cli.py <youtube-url> [options]"""

import sys

from djset2spotify.cli import main

if __name__ == "__main__":
    sys.exit(main())
