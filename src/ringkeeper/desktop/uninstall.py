"""What the Windows uninstaller runs when the user also deletes their data:
`python -m ringkeeper.desktop.uninstall`. The data folder it deletes itself;
the master key is in the system keychain, outside it, so it's removed here."""

import sys

from ringkeeper.config import Settings
from ringkeeper.keys import MasterKeyError, forget_keychain_key


def main() -> int:
    settings = Settings(_env_file=None, mode="desktop")  # pyright: ignore[reportCallIssue]
    try:
        removed = forget_keychain_key(settings)
    except MasterKeyError as error:
        print(error, file=sys.stderr)
        return 1
    print("removed the master key" if removed else "no master key to remove")
    return 0


if __name__ == "__main__":
    sys.exit(main())
