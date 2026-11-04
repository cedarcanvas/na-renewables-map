"""Publish the public copy of the map at maps.ridgelinemaps.com/renewables/.

The page sits in the Ridgeline Maps gallery repo (cedarcanvas/brokenarrow, in
tools/maps_site/renewables/), which GitHub Pages serves at maps.ridgelinemaps.com.
Its data changes every hour, so it lives in a repo of its own instead,
cedarcanvas/renewables-data, served at DATA_HOST. Each run replaces that repo's one
commit, so its history doesn't grow by megabytes an hour, and the gallery repo only
changes when the page does.

The public generation.json is the one built without PJM's Data Miner feed (see
live.py); everything else is the same as web/data.

    python -m pipeline.publish                     # push this hour's data (scripts/hourly.sh)
    python -m pipeline.publish --setup             # once: create the data repo and turn on Pages
    python -m pipeline.publish --site <checkout>   # copy the page into a gallery checkout
"""

import filecmp
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .common import PUBLIC_GENERATION_JSON, ROOT, WEB_DATA

DATA_REPO = "cedarcanvas/renewables-data"
DATA_HOST = "https://cedarcanvas.github.io/na-renewables-map/"  # GitHub Actions build (was renewables-data)
CHECKOUT = ROOT / "data" / "publish"
# web/data files the page reads; generation.json comes from PUBLIC_GENERATION_JSON.
FILES = ["plants.geojson", "regions.geojson", "transmission.pmtiles", "weather"]

PAGE_DIR = "tools/maps_site/renewables"
PAGE_FILES = ["index.html", "style.css", "app.js", "weather.js", "sun.js", "transmission.js"]
PAGE_URL = "https://maps.ridgelinemaps.com/renewables/"
TITLE = "North American Renewables Map · Ridgeline Maps"
DESCRIPTION = ("Live map of wind, solar, hydro and geothermal power across the US and Canada: about 11,500 plants, "
               "their output estimated hour by hour from the grid operators' reports and local weather.")

README = f"""# Renewables map data

The hourly data behind the North American Renewables Map at {PAGE_URL}.
It is rebuilt every hour and replaces this repo's only commit, so there is no history.

Sources: EIA-930 and EIA-860M (US Energy Information Administration); the US grid
operators' fuel mix via gridstatus; IESO, AESO, Hydro-Québec, NS Power, NB Power and
SaskPower; Natural Resources Canada (Open Government Licence – Canada); HIFLD; weather
from Open-Meteo (CC BY 4.0) and NOAA GFS; Alberta solar farms and Canadian transmission
lines © OpenStreetMap contributors (ODbL).
"""


def git(*args):
    return subprocess.run(["git", "-C", str(CHECKOUT), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def sync():
    """Make CHECKOUT match web/data, with the public generation.json."""
    for name in FILES:
        src, dst = WEB_DATA / name, CHECKOUT / name
        sources = [p for p in src.rglob("*") if p.is_file()] if src.is_dir() else [src]
        for f in sources:
            target = dst / f.relative_to(src) if src.is_dir() else dst
            if not target.exists() or not filecmp.cmp(f, target):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
        if src.is_dir():  # drop files the pipeline no longer writes (old cloud frames)
            for f in [p for p in dst.rglob("*") if p.is_file()]:
                if not (src / f.relative_to(dst)).exists():
                    f.unlink()
    shutil.copy2(PUBLIC_GENERATION_JSON, CHECKOUT / "generation.json")


def push():
    if not (CHECKOUT / ".git").exists():
        print("Publishing isn't set up (python -m pipeline.publish --setup); nothing pushed")
        return
    sync()
    git("add", "-A")
    tree = git("write-tree")
    try:
        if tree == git("rev-parse", "HEAD^{tree}"):
            print("Public data unchanged; nothing pushed")
            return
    except subprocess.CalledProcessError:
        pass  # first commit
    hours = json.loads(PUBLIC_GENERATION_JSON.read_text())["hours"]
    commit = git("commit-tree", tree, "-m", f"Map data, {hours[0]} to {hours[-1]}")
    git("update-ref", "refs/heads/main", commit)
    git("push", "--force", "origin", "main")
    git("prune", "--expire=now")  # the replaced commit's files, so the checkout doesn't grow
    print(f"Pushed the public data to {DATA_REPO} ({commit[:7]})")


def setup():
    """Create the data repo (public), its local checkout, and its Pages site."""
    gh = shutil.which("gh")
    if subprocess.run([gh, "repo", "view", DATA_REPO], capture_output=True).returncode:
        subprocess.run([gh, "repo", "create", DATA_REPO, "--public", "--description",
                        f"Hourly data for {PAGE_URL} (replaced every hour)"], check=True)
    CHECKOUT.mkdir(parents=True, exist_ok=True)
    if not (CHECKOUT / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main", str(CHECKOUT)], check=True)
        user_id = subprocess.run([gh, "api", "user", "-q", ".id"], capture_output=True, text=True,
                                 check=True).stdout.strip()
        for key, value in {
            "user.name": "Renewables map pipeline",
            # GitHub's private address for the account, so commits carry no real email.
            "user.email": f"{user_id}+{DATA_REPO.split('/')[0]}@users.noreply.github.com",
            "credential.https://github.com.helper": f"!{gh} auth git-credential",
            "core.logAllRefUpdates": "false",  # no reflog keeping replaced commits alive
            "gc.auto": "0",                    # objects stay loose, so prune can drop them
        }.items():
            git("config", key, value)
        git("remote", "add", "origin", f"https://github.com/{DATA_REPO}.git")
    (CHECKOUT / "README.md").write_text(README)
    (CHECKOUT / ".nojekyll").write_text("")  # serve the files as they are
    push()
    pages = subprocess.run([gh, "api", f"repos/{DATA_REPO}/pages"], capture_output=True)
    if pages.returncode:
        subprocess.run([gh, "api", "-X", "POST", f"repos/{DATA_REPO}/pages",
                        "-f", "source[branch]=main", "-f", "source[path]=/"], check=True, capture_output=True)
    print(f"Data repo ready; Pages serves it at {DATA_HOST} within a few minutes")


def site(gallery):
    """Copy the page into a gallery checkout, pointed at DATA_HOST."""
    out = gallery / PAGE_DIR
    out.mkdir(parents=True, exist_ok=True)
    for name in PAGE_FILES:
        shutil.copy2(ROOT / "web" / name, out / name)
    html = (out / "index.html").read_text()
    html = html.replace('<meta name="data-url" content="data/">', f'<meta name="data-url" content="{DATA_HOST}">')
    head = f"""<title>{TITLE}</title>
  <meta name="description" content="{DESCRIPTION}">
  <link rel="icon" href="../favicon.svg" type="image/svg+xml">
  <meta property="og:title" content="{TITLE}">
  <meta property="og:description" content="{DESCRIPTION}">
  <meta property="og:image" content="https://maps.ridgelinemaps.com/previews/renewables.jpg">
  <meta property="og:url" content="{PAGE_URL}">
  <meta name="twitter:card" content="summary_large_image">"""
    html = re.sub(r"<title>.*?</title>", head, html, count=1)
    (out / "index.html").write_text(html)
    print(f"Copied the page to {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--setup" in args:
        setup()
    elif "--site" in args:
        site(Path(args[args.index("--site") + 1]).expanduser())
    else:
        push()
