import argparse
from getpass import getpass

import _path  # noqa: F401
from clinic_agent.auth import Accounts
from clinic_agent.config import get_settings


def main():
    parser = argparse.ArgumentParser(description="Attach a local account to an existing synthetic clinic profile.")
    parser.add_argument("role", choices=["patient", "doctor"])
    parser.add_argument("profile_id")
    parser.add_argument("email")
    args = parser.parse_args()
    settings = get_settings()
    if not settings.clinic_db.exists():
        parser.error("Start the CareBridge website once to initialize the clinic database.")
    password = getpass("Password (at least 12 characters): ")
    accounts = Accounts(settings.clinic_db)
    accounts.prepare()
    accounts.attach_fixture(args.role, args.profile_id, args.email, password)
    print(f"Created the account for {args.profile_id}.")


if __name__ == "__main__":
    main()
