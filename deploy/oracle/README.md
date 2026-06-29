# Oracle Cloud bridge (temporary, free)

A 3-day stand-in for GitHub Actions while your runner minutes are out. Runs the same
`run.py` / `enricher.py` jobs on the same cron, writing to the same prod Turso DB.
Cost: **$0** on Oracle's Always-Free ARM tier (it never charges for always-free shapes).

## 1. Create the VM (one time, ~10 min)

1. Sign up at <https://cloud.oracle.com> (free; card is for identity only, always-free never bills).
2. Console → **Compute → Instances → Create Instance**:
   - **Image:** Canonical **Ubuntu 24.04**
   - **Shape:** Change shape → **Ampere** → `VM.Standard.A1.Flex` → **4 OCPUs, 24 GB** (all within always-free).
   - **SSH keys:** upload your `~/.ssh/id_ed25519.pub` (or let it generate a keypair and download the private key).
   - **Networking:** create a new VCN (default — it has a public IP + internet gateway).
   - Create. Note the **public IP**.
   > If you hit "Out of host capacity" on the Ampere shape, switch the Availability Domain
   > in the dialog, or pick a different home region at signup. It's a known free-tier quirk.

SSH in to confirm access (the Ubuntu image's user is `ubuntu`):
```bash
ssh ubuntu@<PUBLIC_IP>
```

## 2. Ship the code up (from your Mac)

The runner only needs the Python files — skip `.git`, the frontend, and venvs:
```bash
rsync -avz --delete \
  --exclude '.git' --exclude 'frontend' --exclude '__pycache__' \
  --exclude '.venv' --exclude 'logs' \
  /Users/marleybarrett/conductor/workspaces/Briefs/toronto/ \
  ubuntu@<PUBLIC_IP>:~/briefs/
```

## 3. Provision (on the VM)
```bash
bash ~/briefs/deploy/oracle/setup.sh        # apt + venv + crawl4ai + Playwright Chromium
cp ~/briefs/deploy/oracle/.env.example ~/briefs/deploy/oracle/.env
nano ~/briefs/deploy/oracle/.env            # paste TURSO_AUTH_TOKEN, OPENROUTER_API_KEY, DOMAINS_MONITOR_TOKEN
```
Mint the Turso token on your Mac and paste it into `.env`:
```bash
turso db tokens create briefs
```

## 4. Smoke test before scheduling
```bash
~/briefs/deploy/oracle/run-pipeline.sh daily minnesota
tail -f ~/briefs/logs/daily-minnesota-*.log
```
You should see the firehose download, the geo phase, then `✓ YES` / `✗ NO` site lines,
and matches landing in your dashboard.

## 5. Turn on the schedule
```bash
bash ~/briefs/deploy/oracle/install-cron.sh
crontab -l        # verify
```

## Tear down (when GitHub Actions is back, in ~3 days)
```bash
crontab -r                      # stop the schedule
```
Then terminate the instance in the Oracle console (optional — always-free costs nothing
if you leave it). The durable Turso queue means GitHub picks up seamlessly where this left off.

## Notes
- **Times are UTC**, matching the workflow crons (`setup.sh` sets the VM clock to UTC).
- **Same-vertical overlap is prevented** by `flock` in `run-pipeline.sh`, mirroring the
  `scan-<vertical>` concurrency groups in CI. A backfill skips if its daily is still running.
- **`minnesota --domain-limit 0`** imports the full firehose daily (like CI). If you'd
  rather keep the queue bounded for a short bridge, edit the `daily:minnesota` line in
  `run-pipeline.sh` to a finite `--domain-limit` (e.g. `8000`).
- Don't run this *and* GitHub Actions against the prod DB at the same time — two writers
  can double-process. This is a bridge for while Actions is off.
