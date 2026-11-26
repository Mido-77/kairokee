#!/bin/bash
# Same as Kairokee.command, but phones on the same Wi-Fi can open the app too.
exec /bin/bash "$(dirname "$0")/setup/launch.sh" --phone
