#!/usr/bin/env bash
# Ship code and, optionally, the database to the VM.
#
# The database step exists because copying a live SQLite file in WAL mode
# corrupts it: recent pages sit in the -wal sidecar, and a plain copy of the
# .db alone arrives with a schema that references pages which are not there.
# It fails as "malformed database schema ... invalid rootpage", which is not an
# obvious message for "you forgot to checkpoint".
#
# So: checkpoint, integrity-check, copy, then integrity-check on the far side.
#
#   scripts/deploy.sh            code only
#   scripts/deploy.sh --with-db  code and database
set -euo pipefail

VM=praevisum
ZONE=us-central1-a
PY=./.venv/Scripts/python.exe
WITH_DB=${1:-}

echo "== local checks =="
$PY -c "import src.main; print('  imports ok')"

if [ "$WITH_DB" = "--with-db" ]; then
  echo "== checkpointing the database =="
  $PY - <<'EOF'
import sys; sys.path.insert(0, '.')
from src import db
with db.connect() as c:
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    ok = c.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise SystemExit(f"  refusing to ship a database that says: {ok}")
    print("  integrity ok")
EOF
  tar --exclude=.venv --exclude=.git --exclude=__pycache__ --exclude=recordings \
      --exclude=data -czf /tmp/praevisum.tgz src scripts assets praevisum.db
else
  tar --exclude=.venv --exclude=.git --exclude=__pycache__ --exclude=recordings \
      --exclude=data -czf /tmp/praevisum.tgz src scripts assets
fi

echo "== copying =="
gcloud compute scp /tmp/praevisum.tgz "$VM":/tmp/praevisum.tgz --zone="$ZONE" --quiet 2>&1 | tail -1

echo "== unpacking and restarting =="
gcloud compute ssh "$VM" --zone="$ZONE" --quiet --command="
  set -e
  # stop first so nothing is mid-write while the file is replaced
  sudo systemctl stop praevisum
  # Fold the write-ahead log into the database before touching anything.
  #
  # This used to be 'rm -f praevisum.db-wal praevisum.db-shm', which is data
  # loss: committed transactions live in the -wal until a checkpoint moves
  # them, so deleting it silently reverts the database to its last checkpoint.
  # A code-only deploy threw away everything written since. It was found when a
  # migration that had definitely committed, and printed its own before and
  # after counts, was simply gone after the next deploy. Every call taken
  # between two deploys was at risk the same way.
  cd ~/app && ./.venv/bin/python -c \"
import sqlite3
c = sqlite3.connect('praevisum.db')
c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
print('  checkpointed the write-ahead log')
\"
  tar -xzf /tmp/praevisum.tgz -C ~/app
  cd ~/app
  ./.venv/bin/python -c \"
import sqlite3
c = sqlite3.connect('praevisum.db')
print('  integrity on the vm:', c.execute('PRAGMA integrity_check').fetchone()[0])
print('  dealers:', c.execute('SELECT COUNT(*) FROM dealers').fetchone()[0])
print('  repairs:', c.execute('SELECT COUNT(*) FROM repairs').fetchone()[0])
\"
  sudo systemctl start praevisum
  sleep 9
  echo -n '  service: '; sudo systemctl is-active praevisum
  sudo journalctl -u praevisum -n 30 --no-pager 2>/dev/null | grep -oE 'startup\].*' | tail -1
" 2>&1 | tail -8

# Startup loads the corpus before it binds, which takes the better part of a
# minute. A fixed sleep reported a 502 for a service that was simply still
# starting, which is a worse outcome than waiting: it says the deploy failed
# when it succeeded. Poll instead, and only call it a failure once it has
# genuinely had long enough.
echo "== public =="
HOST=https://twiliotestduck.duckdns.org
for i in $(seq 1 30); do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$HOST/health" || true)
  [ "$CODE" = "200" ] && break
  sleep 3
done
echo "  /health   $CODE  (after $(( i * 3 ))s)"
curl -s -o /dev/null -w "  /console  %{http_code}\n" --max-time 20 "$HOST/console"
