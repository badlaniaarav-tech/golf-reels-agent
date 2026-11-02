#!/usr/bin/env python3
"""Minimal Google Drive client (REST v3, OAuth refresh token) for big video files.

The Drive MCP connector is fine for listing and small files, but video bytes should
go through this script. Needs env: GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN
(get them once with drive_auth.py).

  drive.py list FOLDER_ID [--videos]          -> JSON list of files
  drive.py download FILE_ID OUT_PATH
  drive.py upload LOCAL_PATH FOLDER_ID [--name NAME]   -> JSON {id, webViewLink, downloadUrl}
  drive.py share FILE_ID                      -> makes it "anyone with link can view", prints URLs
  drive.py move FILE_ID NEW_FOLDER_ID
  drive.py setup                              -> find-or-create the "Golf Reels" folder tree, print ids as JSON
"""
import argparse, json, mimetypes, os, sys, urllib.parse, urllib.request

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
_token = None


def token():
    global _token
    if _token:
        return _token
    missing = [k for k in ("GDRIVE_CLIENT_ID", "GDRIVE_CLIENT_SECRET", "GDRIVE_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        sys.exit(f"missing env: {', '.join(missing)} (run pipeline/drive_auth.py once)")
    data = urllib.parse.urlencode(dict(client_id=os.environ["GDRIVE_CLIENT_ID"],
                                       client_secret=os.environ["GDRIVE_CLIENT_SECRET"],
                                       refresh_token=os.environ["GDRIVE_REFRESH_TOKEN"],
                                       grant_type="refresh_token")).encode()
    r = urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data))
    _token = json.load(r)["access_token"]
    return _token


def req(method, url, data=None, headers=None, raw=False):
    h = {"Authorization": f"Bearer {token()}"}
    h.update(headers or {})
    if data is not None and not isinstance(data, bytes):
        data = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    r = urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h, method=method))
    if raw:
        return r
    body = r.read()
    return json.loads(body) if body else {}


def list_files(folder, videos=False):
    q = f"'{folder}' in parents and trashed=false"
    if videos:
        q += " and mimeType contains 'video/'"
    files, page = [], None
    while True:
        params = dict(q=q, fields="nextPageToken,files(id,name,mimeType,size,createdTime,videoMediaMetadata)",
                      pageSize=100, orderBy="createdTime")
        if page:
            params["pageToken"] = page
        res = req("GET", f"{API}/files?{urllib.parse.urlencode(params)}")
        files += res.get("files", [])
        page = res.get("nextPageToken")
        if not page:
            return files


def download(file_id, out):
    r = req("GET", f"{API}/files/{file_id}?alt=media", raw=True)
    with open(out, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return out


def upload(path, folder, name=None):
    name = name or os.path.basename(path)
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    size = os.path.getsize(path)
    r = req("POST", f"{UPLOAD}/files?uploadType=resumable", data={"name": name, "parents": [folder]},
            headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)}, raw=True)
    session = r.headers["Location"]
    with open(path, "rb") as f:
        body = f.read()
    r = urllib.request.urlopen(urllib.request.Request(session, data=body, method="PUT",
                                                      headers={"Content-Type": mime, "Content-Length": str(size)}))
    info = json.load(r)
    return {"id": info["id"], "name": name, "webViewLink": f"https://drive.google.com/file/d/{info['id']}/view",
            "downloadUrl": f"https://drive.google.com/uc?export=download&id={info['id']}"}


def share(file_id):
    req("POST", f"{API}/files/{file_id}/permissions", data={"role": "reader", "type": "anyone"})
    return {"id": file_id, "webViewLink": f"https://drive.google.com/file/d/{file_id}/view",
            "downloadUrl": f"https://drive.google.com/uc?export=download&id={file_id}"}


def move(file_id, new_parent):
    cur = req("GET", f"{API}/files/{file_id}?fields=parents")
    old = ",".join(cur.get("parents", []))
    return req("PATCH", f"{API}/files/{file_id}?addParents={new_parent}&removeParents={old}&fields=id,parents", data={})


FOLDERS = {"inbox": "01 Inbox (drop raw clips here)", "output": "02 Edited reels",
           "processed": "03 Processed raw", "music": "music (optional background tracks)"}


def find_or_create_folder(name, parent=None):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    q += f" and '{parent}' in parents" if parent else " and 'root' in parents"
    res = req("GET", f"{API}/files?{urllib.parse.urlencode(dict(q=q, fields='files(id)'))}")
    if res.get("files"):
        return res["files"][0]["id"]
    body = {"name": name, "mimeType": "application/vnd.google-apps.folder"}
    if parent:
        body["parents"] = [parent]
    return req("POST", f"{API}/files?fields=id", data=body)["id"]


def setup():
    root = find_or_create_folder("Golf Reels")
    ids = {"root": root}
    for key, name in FOLDERS.items():
        ids[key] = find_or_create_folder(name, root)
    return ids


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list"); p.add_argument("folder"); p.add_argument("--videos", action="store_true")
    p = sub.add_parser("download"); p.add_argument("file_id"); p.add_argument("out")
    p = sub.add_parser("upload"); p.add_argument("path"); p.add_argument("folder"); p.add_argument("--name")
    p = sub.add_parser("share"); p.add_argument("file_id")
    p = sub.add_parser("move"); p.add_argument("file_id"); p.add_argument("folder")
    sub.add_parser("setup")
    a = ap.parse_args()
    if a.cmd == "setup":
        out = setup()
    elif a.cmd == "list":
        out = list_files(a.folder, a.videos)
    elif a.cmd == "download":
        out = {"saved": download(a.file_id, a.out), "bytes": os.path.getsize(a.out)}
    elif a.cmd == "upload":
        out = upload(a.path, a.folder, a.name)
    elif a.cmd == "share":
        out = share(a.file_id)
    else:
        out = move(a.file_id, a.folder)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
