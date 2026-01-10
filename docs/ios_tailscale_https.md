# Tailscale + HTTPS path for iOS (no web-app changes)

Goal: let the SwiftUI app talk to your existing FastAPI backend over **HTTPS** without changing the public web app. We do this by using a Tailscale hostname (e.g., `node-name.tailnet-123.ts.net`) that is only reachable by devices on your tailnet.

## Prereqs
- You have SSH access to the DigitalOcean droplet.
- The droplet can install packages (sudo apt).
- You can install the Tailscale iOS app on your phone.
- Your domain and public Nginx/web app stay untouched.

## Step 1 — Install Tailscale on the droplet
```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up --ssh
# Follow the URL shown to authenticate the node into your tailnet.
```

Notes:
- `--ssh` lets you SSH over Tailscale (optional but handy).
- After auth, the node gets a stable Tailscale name + IP (shown in `tailscale status`).

## Step 2 — Verify Tailscale HTTPS from another device
On your laptop (also logged into the same tailnet):
```bash
tailscale status        # confirm both devices are in the tailnet
curl -v https://YOUR-NODE.ts.net/api/health
```
You should see `{"ok": true}`. If you get cert errors, see “TLS” below.

## Step 3 — Install Tailscale on iPhone and verify
1) Install the **Tailscale** app from the App Store.  
2) Log into the same tailnet.  
3) Safari test: browse to `https://YOUR-NODE.ts.net/api/health`.  
4) If it loads `{ok:true}`, networking is good for the app.

## Step 4 — Ensure HTTPS is valid (ATS compliance)
If the droplet already serves HTTPS correctly on the Tailscale hostname, you’re done. If not, you have options:
- **Easiest (if you have a cert for your public domain)**: keep using the same Nginx cert; add a server_name for the Tailscale hostname if needed.
- **Tailscale Serve (built-in HTTPS)**:  
  ```bash
  sudo tailscale serve https 443 http://127.0.0.1:8000
  ```
  This terminates TLS at Tailscale and forwards to your uvicorn/gunicorn backend.
- **Caddy/Certbot on the Tailscale host**: issue a cert for the Tailscale hostname if you control DNS.

App Transport Security (ATS) rules:
- iOS requires a trusted cert. Avoid self-signed unless you add ATS exceptions (not recommended).
- Prefer the Tailscale “serve https” path or reuse your existing valid cert.

## Step 5 — Keep public web app unchanged
- Do **not** change existing Nginx sites that serve the public domain.
- The SwiftUI app will be configured to hit `https://YOUR-NODE.ts.net` (or another tailnet hostname) only.

## Step 6 — Smoke tests to run
- `curl https://YOUR-NODE.ts.net/api/health` → `{"ok": true}`
- `curl https://YOUR-NODE.ts.net/api/flags` → small JSON object (no HTML/redirect)
- `curl -I https://YOUR-NODE.ts.net/breach` should return `200` with `text/html` (verifies static still works)

## Step 7 — (Optional) Pin a stable name
- In the Tailscale admin console, set a **MagicDNS** name and/or tag the node so its name doesn’t change.
- If you prefer a custom DNS name, create a CNAME to the `*.ts.net` hostname inside your private DNS; keep it private.

## What to share with me when done
- The reachable HTTPS base URL (e.g., `https://node-name.tailnet-123.ts.net`).
- Confirmation that `/api/health` works from iPhone Safari while on Tailscale.

Once this is reachable, we’ll point the SwiftUI app’s `BaseURL` to that hostname for TestFlight builds.
