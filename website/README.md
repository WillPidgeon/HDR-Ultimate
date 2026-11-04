# hdrultra.help

The HDR Ultimate download page and daily user counter, as a Docker container behind a Cloudflare Tunnel. The tunnel connects out from your server to Cloudflare, so no router ports are opened and your home IP stays hidden.

- `public/`: the page.
- `server.py`: serves the page, plus `POST /ping` (the app's once-a-day check-in). Uses only Python's standard library. It keeps nothing but one number per day, and doesn't log requests.
- `admin.py`: the admin panel at `/admin`, with the daily user counts, website traffic by country and admin accounts. Only admins can sign in, and there's no sign-up.
- `countries.py`: country names for the traffic table.

Website traffic is counted from home page views, using the visitor's country from Cloudflare's `CF-IPCountry` header (IP Geolocation, on by default under **Network** in the Cloudflare dashboard). Only a total per day and country is kept: no IPs, no cookies.
- `Dockerfile`, `docker-compose.yml`: the site container and the tunnel container.

## 1. Cloudflare (once)

1. Add `hdrultra.help` to Cloudflare on the Free plan. At Namecheap, set its nameservers to the two Cloudflare gives you.
2. In **Zero Trust > Networks > Tunnels**, choose **Create a tunnel > Cloudflared** and name it `hdrultra`. On the install page pick **Docker**, and copy the token (the long text after `--token`).
3. Under **Public Hostname**, add `hdrultra.help` with service type **HTTP** and URL `web:8080`.

## 2. A Docker host on Proxmox (once)

A Debian container (LXC) with Docker is the lightest option. On the Proxmox host:

```bash
pveam update
pveam download local debian-12-standard_12.12-1_amd64.tar.zst
pct create 210 local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst --hostname hdrultra --memory 512 --cores 1 \
  --rootfs local-lvm:4 --net0 name=eth0,bridge=vmbr0,ip=dhcp --unprivileged 1 --features nesting=1,keyctl=1 --onboot 1
pct start 210
pct exec 210 -- bash -c "apt-get update && apt-get install -y curl && curl -fsSL https://get.docker.com | sh"
```

Use a free container ID instead of 210 if it's taken, and check `pveam available --section system` for the current Debian 12 template name.

## 3. Deploy

Copy this folder to the Docker host, then in it:

```bash
cp .env.example .env
nano .env                 # fill in TUNNEL_TOKEN
docker compose up -d --build
```

The site is live at https://hdrultra.help once the tunnel shows **Healthy** in Cloudflare.

## Update the site

Copy the changed files over and run `docker compose up -d --build` again. The counts live in the `usage-data` volume and survive updates.

## Change the tunnel token

After **Rotate token** in Cloudflare, run `bash set-token.sh` in this folder on the Docker host and paste the new token. It saves it to `.env` and restarts the tunnel.

## Admin panel

Make the first admin account on the Docker host (it asks for a password, at least 8 characters):

```bash
docker compose exec web python admin.py add <name>
```

Then sign in at https://hdrultra.help/admin. From there you can see the counts, add or remove admins, and change your password. Sign-ins last 12 hours, and after 8 wrong passwords from one address, sign-in is blocked from there for 15 minutes.

Locked out? On the Docker host, `docker compose exec web python admin.py password <name>` sets a new password, `... admin.py list` shows the accounts, and `... admin.py remove <name>` deletes one.

## Limits

Anyone could send fake check-ins, so treat the numbers as close, not exact. Someone who uses both the Windows and Linux versions on the same day counts twice.
