"""Run two competing synthetic patient requests in each shared clinic tick."""
from ..clinic_db import connect
from ..clinic_tools import ClinicService


def run_day(society, ticks, rng, extra_patients):
    env = society.env
    society.refresh_doctors()
    extra_patients = max(2, extra_patients)
    society.seed_synthetic_patients(extra_patients)
    active = [society.agents[f"SIM-{i:02d}"] for i in range(1, extra_patients + 1)]
    with connect(env.db_path) as conn:
        conn.executemany("INSERT OR IGNORE INTO patients VALUES (?, ?, ?)",
                         [(patient.agent_id, patient.name, int(patient.attributes["is_new"])) for patient in active])

    service = ClinicService(env.db_path, env.now)
    scheduler = society.get("scheduler")
    front_desk = society.get("receptionist")
    log = []
    retry = {}

    def send(message):
        society.dispatch(message)

    departments = sorted({agent.attributes["department"] for agent in society.agents.values() if agent.role == "doctor"})
    for _ in range(ticks):
        env.tick += 1
        selected = list(retry)[:2]
        selected.extend(rng.sample([p for p in active if p not in selected], 2 - len(selected)))
        common_department = rng.choice(departments)
        proposals = []
        for patient in selected:
            department = retry.pop(patient, common_department)
            if patient.attributes["is_new"]:
                department = "General Medicine"
            request = patient.request_slot(department)
            send(request)
            proposal = scheduler.propose(service, request)
            send(proposal)
            if proposal.performative == "PROPOSE":
                proposals.append((patient, department, proposal))
                log.append({"tick": env.tick, "event": "Slot proposed", "patient": patient.name,
                            "detail": proposal.content["date"] + " " + proposal.content["start_time"]})
            else:
                env.metrics["refusals"] += 1
                log.append({"tick": env.tick, "event": "No open time", "patient": patient.name, "detail": department})

        for patient, department, proposal in proposals:
            request = patient.select_slot(proposal)
            send(request)
            outcome = front_desk.book_synthetic(service, request)
            send(outcome)
            booked = outcome.performative == "CONFIRM"
            conflict = outcome.content.get("reason") == "SLOT_UNAVAILABLE"
            society.note_booking(booked, conflict)
            patient.state = "booked" if booked else "waiting"
            event = "Appointment confirmed" if booked else "Slot taken" if conflict else "Booking refused"
            if conflict:
                society.record_plan("Find the synthetic patient another slot", True)
                retry[patient] = department
            log.append({"tick": env.tick, "event": event, "patient": patient.name,
                        "detail": outcome.content.get("appointment_id") or outcome.content.get("reason")})

    return {"ticks": ticks, "log": log, "snapshot": society.snapshot()}
