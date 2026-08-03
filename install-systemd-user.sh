#!/bin/sh
set -eu

repository=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
unit_directory="$HOME/.config/systemd/user"

mkdir -p "$unit_directory"
ln -sfn "$repository/systemd/commitfest-reviews-sync.service" \
  "$unit_directory/commitfest-reviews-sync.service"
ln -sfn "$repository/systemd/commitfest-reviews-sync.timer" \
  "$unit_directory/commitfest-reviews-sync.timer"

systemctl --user daemon-reload
systemctl --user enable --now commitfest-reviews-sync.timer
systemctl --user start commitfest-reviews-sync.service
