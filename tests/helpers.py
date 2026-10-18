import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pdsia import evaluation  # noqa: E402


def fresh_db(snapshots=None):
    return evaluation.fresh_db(snapshots) if snapshots else evaluation.fresh_db()
