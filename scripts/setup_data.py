"""Initialize missing clinic data and prepare the current policy index."""
import argparse

import _path  # noqa: F401
from clinic_agent.clinic_db import seed
from clinic_agent.config import get_settings
from clinic_agent.doctor_schedule import DoctorSchedule
from clinic_agent.runtime import build_knowledge_base


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--kb-only", action="store_true")
    mode.add_argument("--db-only", action="store_true")
    args = parser.parse_args()
    settings = get_settings()

    if not args.kb_only:
        if not settings.clinic_db.exists():
            seed(settings.clinic_db, settings.clinic_now)
        DoctorSchedule(settings.clinic_db, settings.clinic_now).prepare()
        print(f"Clinic database ready at {settings.clinic_db}")
    if not args.db_only:
        kb = build_knowledge_base(settings)
        try:
            print(f"Policy index ready with {kb.store.count()} chunks using {kb.embedder.name}")
        finally:
            kb.store.close()


if __name__ == "__main__":
    main()
