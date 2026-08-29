import sys
from pathlib import Path

# Allow `import app...` when running pytest from the backend/ directory or
# from the repo root, without requiring the package to be installed.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
