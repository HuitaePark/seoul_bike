# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: `python -m evaluation` entry point.

import asyncio
import sys

from .runner import main

sys.exit(asyncio.run(main()))
