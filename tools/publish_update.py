"""
Tell everyone's Kairokee that a new version is out (they see a notice on the home page).

    python tools/publish_update.py "What changed 1" "What changed 2" ...

It writes the version from app/kairokee.py (APP_VERSION) and your notes into the secret gist that the
app reads (UPDATE_URL). Run it AFTER you have given people a way to get the new zip.
Needs the GitHub command line tool (gh), logged in to the account that owns the gist.
"""
import datetime
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOW_TO_GET = "Ask Mido for the new Kairokee zip."


def main():
    notes = [n.strip() for n in sys.argv[1:] if n.strip()]
    if not notes:
        raise SystemExit('Write what changed, for example:\n  python tools/publish_update.py "Faster lyrics" "Bug fixes"')
    code = (ROOT / "app" / "kairokee.py").read_text(encoding="utf-8")
    version = re.search(r'^APP_VERSION = "([^"]+)"', code, re.M).group(1)
    url = re.search(r'^UPDATE_URL = "([^"]+)"', code, re.M).group(1)
    gist_id = re.search(r"gist\.githubusercontent\.com/[^/]+/([0-9a-f]+)/", url).group(1)
    data = {"version": version, "date": datetime.date.today().isoformat(), "notes": notes, "how_to_get": HOW_TO_GET}
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "kairokee-latest.json"
        f.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        subprocess.run(["gh", "gist", "edit", gist_id, "--filename", "kairokee-latest.json", str(f)], check=True)
    print(f"Published: Kairokee {version} is now the newest version.")
    print("Older copies show the notice the next time they open the home page (it is checked every few hours).")


if __name__ == "__main__":
    main()
