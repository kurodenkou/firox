"""Where a Firox user's own files go: the standard places in their home
folder (~/.config, ~/.local/share, ~/.local/state) and their private
runtime folder (/run/user/UID).

They come from the account itself (the user database and the user ID), not
from environment variables such as XDG_CONFIG_HOME: whatever started a
program can't point its settings, data or logs somewhere else. Firox never
sets those variables, so these are the folders they'd name anyway.
"""

import os
import pwd

UID = os.getuid()
HOME = pwd.getpwuid(UID).pw_dir
CONFIG = os.path.join(HOME, ".config")
DATA = os.path.join(HOME, ".local", "share")
STATE = os.path.join(HOME, ".local", "state")
RUNTIME = f"/run/user/{UID}"
