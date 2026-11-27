"""Download only the pinned model archive; keep API credentials out of redirects/logs."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / "dependency-lock.json").read_text())["model"]
MAX_ARCHIVE = 64 * 1024 * 1024
MAX_EXTRACTED = 128 * 1024 * 1024

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def download(token_path, destination):
    from urllib.error import HTTPError
    from urllib.parse import urlsplit
    token = Path(token_path).read_text().strip()
    if not token or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("invalid GitHub build credential")
    url = f"https://api.github.com/repos/Sllay/Amora-Chibi/actions/artifacts/{LOCK['artifact']}/zip"
    request = urllib.request.Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
    try:
        response = urllib.request.build_opener(NoRedirect()).open(request, timeout=60)
    except HTTPError as exc:
        if exc.code not in {301,302,303,307,308}:
            raise RuntimeError("pinned rig artifact unavailable; regenerate from approved exporter") from None
        location = exc.headers.get("Location", "")
        exc.close()
        parts = urlsplit(location)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError("invalid artifact redirect")
        # The GitHub API credential is deliberately not forwarded to object storage.
        response = urllib.request.urlopen(location, timeout=90)
    with response, open(destination,"wb") as out:
        total = 0
        while block := response.read(1024*1024):
            total += len(block)
            if total > MAX_ARCHIVE:
                raise ValueError("rig archive too large")
            out.write(block)
    verify_extract(destination, Path(destination).with_suffix(""))

def verify_extract(archive, out):
    archive=Path(archive)
    if archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError("rig archive too large")
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream,"sha256").hexdigest() != LOCK["artifactSha256"]:
            raise ValueError("pinned rig archive digest mismatch")
    out=Path(out)
    out.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as z:
        entries=z.infolist()
        if len(entries)>4096 or sum(x.file_size for x in entries)>MAX_EXTRACTED:
            raise ValueError("rig extraction limit")
        seen=set()
        for info in entries:
            p=PurePosixPath(info.filename)
            if p.is_absolute() or ".." in p.parts or not p.parts or "\\" in info.filename or stat.S_ISLNK(info.external_attr >> 16) or str(p) in seen:
                raise ValueError("invalid rig archive entry")
            seen.add(str(p))
            target=out.joinpath(*p.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open("xb") as dst:
                    shutil.copyfileobj(src,dst)

if __name__=="__main__":
    try:
        if len(sys.argv)==4 and sys.argv[1]=="extract":
            verify_extract(sys.argv[2],sys.argv[3])
        elif len(sys.argv)==4 and sys.argv[1]=="download":
            download(sys.argv[2],sys.argv[3])
        else:
            raise ValueError("usage: fetch-rig.py extract ARCHIVE OUT | download TOKEN_FILE ARCHIVE")
    except Exception:
        # Exceptions from object storage can contain signed URLs; do not print them.
        raise SystemExit("V5 pinned rig download/extraction failed")
