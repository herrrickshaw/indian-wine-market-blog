"""Make the script directories importable (they are not packages) and keep tests offline."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for sub in ("consistency-check", "tools"):
    sys.path.insert(0, str(ROOT / sub))

# blogger_api reads these at import time; the value is never used for a request in tests.
os.environ.setdefault("WINE_BLOG_ID", "1")
os.environ.setdefault("WINE_BLOG_URL", "https://example.invalid")
