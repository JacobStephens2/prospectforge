"""Print the FastAPI OpenAPI schema as JSON. Pipe to openapi.json for client gen."""

import json
import sys
from pathlib import Path

# Allow running as `python scripts/dump_openapi.py` from /app.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402


def main() -> None:
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
