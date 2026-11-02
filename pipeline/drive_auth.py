#!/usr/bin/env python3
"""One-time setup: get a Google Drive refresh token. Run this on YOUR computer (it opens a browser).

1. https://console.cloud.google.com -> new project -> APIs & Services -> enable "Google Drive API".
2. APIs & Services -> OAuth consent screen -> External, add yourself as a test user.
3. Credentials -> Create credentials -> OAuth client ID -> type "Desktop app". Copy client id + secret.
4. python3 pipeline/drive_auth.py CLIENT_ID CLIENT_SECRET
5. Put the three values printed at the end into the cloud environment's secrets:
   GDRIVE_CLIENT_ID, GDRIVE_CLIENT_SECRET, GDRIVE_REFRESH_TOKEN
"""
import http.server, json, sys, urllib.parse, urllib.request, webbrowser

PORT = 8765
SCOPE = "https://www.googleapis.com/auth/drive"


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    cid, secret = sys.argv[1:3]
    redirect = f"http://localhost:{PORT}/"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(dict(
        client_id=cid, redirect_uri=redirect, response_type="code", scope=SCOPE,
        access_type="offline", prompt="consent"))
    print("Opening browser... if it does not open, visit:\n", url)
    webbrowser.open(url)
    code = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code["v"] = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("code", [""])[0]
            self.send_response(200); self.end_headers()
            self.wfile.write(b"Done. You can close this tab.")
        def log_message(self, *a): pass

    http.server.HTTPServer(("localhost", PORT), H).handle_request()
    data = urllib.parse.urlencode(dict(code=code["v"], client_id=cid, client_secret=secret,
                                       redirect_uri=redirect, grant_type="authorization_code")).encode()
    tok = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data))
    print("\nAdd these as environment secrets:\n")
    print(f"GDRIVE_CLIENT_ID={cid}\nGDRIVE_CLIENT_SECRET={secret}\nGDRIVE_REFRESH_TOKEN={tok['refresh_token']}")


if __name__ == "__main__":
    main()
