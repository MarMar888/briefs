#!/usr/bin/env bash
# Keep trying to launch the Always-Free Ampere A1 instance until capacity appears.
# Oracle's free A1 pool is a lottery — capacity blinks in and out. This hammers every
# (shape, AD) combo on an interval, refreshing the OCI session token so it survives a
# long wait. On success it writes INST/IP to /tmp/oci_state.env and exits 0.
#
#   Usage: oci-launch-retry.sh [max_minutes]   (default 120)
set -uo pipefail
export OCI_CLI_AUTH=security_token
. /tmp/oci_state.env

IMG=ocid1.image.oc1.us-chicago-1.aaaaaaaaremq2g7crdfi5dv44tadt75p7c4jop4kkhbdesybo25gjj6sd5nq
PUBKEY="$HOME/.ssh/id_ed25519.pub"
ADS=("fJzh:US-CHICAGO-1-AD-1" "fJzh:US-CHICAGO-1-AD-2" "fJzh:US-CHICAGO-1-AD-3")
CFGS=('{"ocpus":4,"memoryInGBs":24}' '{"ocpus":2,"memoryInGBs":12}' '{"ocpus":1,"memoryInGBs":6}')

MAX_MIN="${1:-120}"
deadline=$(( $(date +%s) + MAX_MIN*60 ))
attempt=0
while [ "$(date +%s)" -lt "$deadline" ]; do
  attempt=$((attempt+1))
  # refresh the session token every ~5 attempts (~25 min) so the ~hourly token never lapses
  if [ $((attempt % 5)) -eq 0 ]; then oci session refresh --profile DEFAULT >/dev/null 2>&1 || true; fi
  for CFG in "${CFGS[@]}"; do
    for AD in "${ADS[@]}"; do
      INST=$(oci compute instance launch -c "$TEN" --availability-domain "$AD" \
        --shape VM.Standard.A1.Flex --shape-config "$CFG" \
        --image-id "$IMG" --subnet-id "$SUB" --assign-public-ip true \
        --display-name briefs-bridge --ssh-authorized-keys-file "$PUBKEY" \
        --wait-for-state RUNNING --query 'data.id' --raw-output 2>/tmp/launch_err.txt) || true
      if [ -n "${INST:-}" ] && [ "$INST" != "null" ]; then
        sed -i '' '/^INST=/d;/^IP=/d' /tmp/oci_state.env 2>/dev/null || true
        echo "INST=$INST" >> /tmp/oci_state.env
        IP=$(oci compute instance list-vnics --instance-id "$INST" --query 'data[0]."public-ip"' --raw-output)
        echo "IP=$IP" >> /tmp/oci_state.env
        echo "LAUNCHED $CFG @ $AD"
        echo "INSTANCE=$INST"
        echo "PUBLIC_IP=$IP"
        exit 0
      fi
    done
  done
  msg=$(grep -o '"message": "[^"]*"' /tmp/launch_err.txt | head -1)
  echo "[attempt $attempt @ $(date -u +%H:%M:%S)] no capacity ($msg)"
  sleep 90
done
echo "GAVE_UP after ${MAX_MIN}m — still out of capacity"
exit 7
