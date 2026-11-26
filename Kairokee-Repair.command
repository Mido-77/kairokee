#!/bin/bash
# Use this if Kairokee stopped working. It installs the program parts again.
# Your songs and downloaded models are kept.
exec /bin/bash "$(dirname "$0")/setup/launch.sh" --repair
