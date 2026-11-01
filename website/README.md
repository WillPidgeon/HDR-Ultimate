# hdrultra.help

The HDR Ultimate download page and daily user counter, as a Docker container behind a Cloudflare Tunnel. The tunnel connects out from your server to Cloudflare, so no router ports are opened and your home IP stays hidden.

- `public/`: the page.
- `server.py`: serves the page, plus `POST /ping` (the app's once-a-day check-in) and `GET /stats?key=...` (your private counts). Uses only Python's standard library. It keeps nothing but one number per day, and doesn't log requests.
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
nano .env                 # fill in STATS_KEY and TUNNEL_TOKEN
docker compose up -d --build
```

The site is live at https://hdrultra.help once the tunnel shows **Healthy** in Cloudflare.

## Update the site

Copy the changed files over and run `docker compose up -d --build` again. The counts live in the `usage-data` volume and survive updates.

## Change the tunnel token

After **Rotate token** in Cloudflare, run `bash set-token.sh` in this folder on the Docker host and paste the new token. It saves it to `.env` and restarts the tunnel.

## See the counts

Open `https://hdrultra.help/stats?key=<your STATS_KEY>`.

## Limits

Anyone could send fake check-ins, so treat the numbers as close, not exact. Someone who uses both the Windows and Linux versions on the same day counts twice.
