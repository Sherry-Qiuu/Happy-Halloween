#!/usr/bin/env sh
# Back up the account and user-data directory.
#
#   ./backup.sh                          # /var/lib/xingjian -> ./backup-*.tar.gz
#   ./backup.sh ./data                   # a local data/ folder
#   ./backup.sh ./data /backups/xingjian.tar.gz
#
# The archive contains password hashes and private reflections, so keep
# it on encrypted storage with the same care as the live directory.
set -eu

SOURCE=${1:-/var/lib/xingjian}
TARGET=${2:-"./xingjian-$(date +%Y%m%d-%H%M%S).tar.gz"}

if [ ! -d "$SOURCE" ]; then
    echo "data directory not found: $SOURCE" >&2
    exit 1
fi

parent=$(dirname "$SOURCE")
name=$(basename "$SOURCE")
umask 077
tar -czf "$TARGET" -C "$parent" "$name"
echo "backup written: $TARGET"
