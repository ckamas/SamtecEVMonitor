#!/usr/bin/env bash
# Start the local Firebase Realtime Database emulator; data persists in ./emulator-data
cd "$(dirname "$0")"
exec ./tools/firebase/node_modules/.bin/firebase emulators:start \
  --only database --project demo-samtec-chargers \
  --import ./emulator-data --export-on-exit ./emulator-data
