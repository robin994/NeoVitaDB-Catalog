import os
import json
import requests
import fnmatch
import time
import zipfile
import struct
import io
import sys
from pathlib import Path

s = requests.Session()
s.headers = {"User-Agent": "Validate_TITLEID"}

TOKEN = os.environ.get("GITHUB_TOKEN", "")

ENTRIES_DIR = Path(__file__).resolve().parent.parent / "apps" / "vita"

def read_int32(f):
    return struct.unpack("I", f.read(4))[0]

def read_int16(f):
    return struct.unpack("H", f.read(2))[0]

def read_int8(f):
    return struct.unpack("B", f.read(1))[0]

def read_cstr(f):
    s = b''
    c = None
    while True:
        c = f.read(1)
        if len(c) == 1 and c != b'\x00':
            s += c
        else:
            break
    return s.decode("UTF-8")

def read_at(f, loc, func):
    opos = f.tell()
    f.seek(loc, os.SEEK_SET)
    res = func(f)
    f.seek(opos, os.SEEK_SET)
    return res

def parse_sfo(sf):
    sfoKeys = {}

    PSF_TYPE_BIN = 0
    PSF_TYPE_STR = 2
    PSF_TYPE_VAL = 4

    magic = read_int32(sf)
    version = read_int32(sf)
    keyoff = read_int32(sf)
    valoff = read_int32(sf)
    count = read_int32(sf)

    if magic == 0x46535000:
        for i in range(0, count):
            nameoff = read_int16(sf)
            align = read_int8(sf)
            vtype = read_int8(sf)
            vsize = read_int32(sf)
            totalsize = read_int32(sf)
            dataoff = read_int32(sf)

            keylocation = keyoff + nameoff
            valuelocation = valoff + dataoff

            keyValue = None
            keyName = read_at(sf, keylocation, read_cstr)
            if vtype == PSF_TYPE_BIN:
                keyValue = read_at(sf, valuelocation, lambda f: f.read(vsize))
            elif vtype == PSF_TYPE_STR:
                keyValue = read_at(sf, valuelocation, read_cstr)
            elif vtype == PSF_TYPE_VAL:
                keyValue = read_at(sf, valuelocation, read_int32)
            else:
                continue

            sfoKeys[keyName] = keyValue
    return sfoKeys

def github_api(path):
    while True:
        headers = {"Authorization": "Token " + TOKEN} if TOKEN else {}
        r = s.get("https://api.github.com" + path, headers=headers)
        if r.status_code == 200:
            return r.json()
        if r.status_code == 404:
            return None
        time.sleep(5)

def get_github_url(repo, pattern):
    releases = github_api("/repos/" + repo + "/releases?per_page=20")
    if not releases:
        return None
    for release in releases:
        if release.get("draft"):
            continue
        for asset in release.get("assets", []):
            if "browser_download_url" in asset:
                if len(fnmatch.filter([os.path.basename(asset["browser_download_url"])], pattern)) > 0:
                    return asset["browser_download_url"]
    return None

class RangeFile(io.RawIOBase):
    def __init__(self, url, size):
        self.url = url
        self.size = size
        self.pos = 0

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        else:
            self.pos = self.size + offset
        return self.pos

    def tell(self):
        return self.pos

    def readinto(self, b):
        end = min(self.pos + len(b), self.size) - 1
        if self.pos > end:
            return 0
        r = s.get(self.url, headers={"Range": "bytes=%d-%d" % (self.pos, end)})
        data = r.content
        b[:len(data)] = data
        self.pos += len(data)
        return len(data)

def download_vpk(url):
    r = s.head(url, allow_redirects=True)
    if r.status_code != 200 or "Content-Length" not in r.headers:
        return None
    return RangeFile(url, int(r.headers["Content-Length"]))

def get_title_id(vpkfile):
    PARAM_FILE = "sce_sys/param.sfo"
    with zipfile.ZipFile(vpkfile) as zf:
        if PARAM_FILE in zf.namelist():
            with zf.open(PARAM_FILE, "r") as pf:
                params = parse_sfo(pf)
                if "TITLE_ID" in params:
                    return params["TITLE_ID"]
                else:
                    return None
        else:
            return None

vlist = [p for p in os.listdir(ENTRIES_DIR) if not p.startswith("_")]

for path in vlist:
    entry = json.loads(open(os.path.join(ENTRIES_DIR, path), "rb").read())
    expTitleId = entry['titleid']

    url = None
    if 'repo' in entry and 'asset' in entry:
        url = get_github_url(entry['repo'], entry["asset"])
    elif 'direct_url' in entry:
        url = entry['direct_url']

    if url is not None:
        file = download_vpk(url)
        if file is not None:
            try:
                gotTitleId = get_title_id(file)
            except zipfile.BadZipFile:
                gotTitleId = None
            if gotTitleId is not None:
                if not expTitleId == gotTitleId:
                    print("title id mismatch; expected " + expTitleId + ", got: " + gotTitleId)
                    print("title id mismatch; expected " + expTitleId + ", got: " + gotTitleId, file=sys.stderr)
                # else:
                #     print("title id match! " + expTitleId + " == " + gotTitleId, file=sys.stderr)
            else:
                print(url + " is missing a title_id!")
                print(url + " is missing a title_id!", file=sys.stderr)
