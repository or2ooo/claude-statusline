#!/usr/bin/env bash
# Shim that delegates to statusline.py. Keeps the settings.json `command`
# field stable even if the implementation language changes later.
exec /usr/bin/env python3 "$(dirname "$0")/statusline.py" "$@"
