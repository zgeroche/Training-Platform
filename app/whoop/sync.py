"""Pull WHOOP data into SQLite.

Incremental: each resource re-fetches from (last successful sync - 3 days), because
WHOOP keeps updating scores for a while after a workout or sleep ends.
Run once from the command line with:  python -m app.whoop.sync
"""

import logging
import threading
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import get_settings
from app.matching import rebuild_matches
from app.db import SessionLocal, init_db, parse_ts, utcnow
from app.models import SyncState, WhoopCycle, WhoopRecovery, WhoopSleep, WhoopUser, WhoopWorkout
from app.whoop.client import WhoopClient

log = logging.getLogger(__name__)

OVERLAP = timedelta(days=3)
_sync_lock = threading.Lock()


def _score(rec: dict) -> dict:
    return rec.get("score") or {}


def upsert_cycle(session: Session, rec: dict) -> None:
    s = _score(rec)
    session.merge(
        WhoopCycle(
            id=rec["id"],
            user_id=rec.get("user_id"),
            start=parse_ts(rec["start"]),
            end=parse_ts(rec.get("end")),
            timezone_offset=rec.get("timezone_offset"),
            score_state=rec.get("score_state"),
            strain=s.get("strain"),
            kilojoule=s.get("kilojoule"),
            average_heart_rate=s.get("average_heart_rate"),
            max_heart_rate=s.get("max_heart_rate"),
            updated_at=parse_ts(rec.get("updated_at")),
            raw=rec,
        )
    )


def upsert_recovery(session: Session, rec: dict) -> None:
    s = _score(rec)
    session.merge(
        WhoopRecovery(
            cycle_id=rec["cycle_id"],
            sleep_id=rec.get("sleep_id"),
            user_id=rec.get("user_id"),
            score_state=rec.get("score_state"),
            user_calibrating=s.get("user_calibrating"),
            recovery_score=s.get("recovery_score"),
            resting_heart_rate=s.get("resting_heart_rate"),
            hrv_rmssd_milli=s.get("hrv_rmssd_milli"),
            spo2_percentage=s.get("spo2_percentage"),
            skin_temp_celsius=s.get("skin_temp_celsius"),
            created_at=parse_ts(rec.get("created_at")),
            updated_at=parse_ts(rec.get("updated_at")),
            raw=rec,
        )
    )


def upsert_sleep(session: Session, rec: dict) -> None:
    s = _score(rec)
    stages = s.get("stage_summary") or {}
    session.merge(
        WhoopSleep(
            id=str(rec["id"]),
            cycle_id=rec.get("cycle_id"),
            user_id=rec.get("user_id"),
            start=parse_ts(rec["start"]),
            end=parse_ts(rec.get("end")),
            timezone_offset=rec.get("timezone_offset"),
            nap=rec.get("nap"),
            score_state=rec.get("score_state"),
            sleep_performance_percentage=s.get("sleep_performance_percentage"),
            sleep_efficiency_percentage=s.get("sleep_efficiency_percentage"),
            sleep_consistency_percentage=s.get("sleep_consistency_percentage"),
            respiratory_rate=s.get("respiratory_rate"),
            total_in_bed_time_milli=stages.get("total_in_bed_time_milli"),
            total_awake_time_milli=stages.get("total_awake_time_milli"),
            total_light_sleep_time_milli=stages.get("total_light_sleep_time_milli"),
            total_slow_wave_sleep_time_milli=stages.get("total_slow_wave_sleep_time_milli"),
            total_rem_sleep_time_milli=stages.get("total_rem_sleep_time_milli"),
            disturbance_count=stages.get("disturbance_count"),
            updated_at=parse_ts(rec.get("updated_at")),
            raw=rec,
        )
    )


def upsert_workout(session: Session, rec: dict) -> None:
    s = _score(rec)
    zones = s.get("zone_durations") or {}
    session.merge(
        WhoopWorkout(
            id=str(rec["id"]),
            user_id=rec.get("user_id"),
            start=parse_ts(rec["start"]),
            end=parse_ts(rec.get("end")),
            timezone_offset=rec.get("timezone_offset"),
            sport_id=rec.get("sport_id"),
            sport_name=rec.get("sport_name"),
            score_state=rec.get("score_state"),
            strain=s.get("strain"),
            average_heart_rate=s.get("average_heart_rate"),
            max_heart_rate=s.get("max_heart_rate"),
            kilojoule=s.get("kilojoule"),
            percent_recorded=s.get("percent_recorded"),
            distance_meter=s.get("distance_meter"),
            altitude_gain_meter=s.get("altitude_gain_meter"),
            zone_zero_milli=zones.get("zone_zero_milli"),
            zone_one_milli=zones.get("zone_one_milli"),
            zone_two_milli=zones.get("zone_two_milli"),
            zone_three_milli=zones.get("zone_three_milli"),
            zone_four_milli=zones.get("zone_four_milli"),
            zone_five_milli=zones.get("zone_five_milli"),
            updated_at=parse_ts(rec.get("updated_at")),
            raw=rec,
        )
    )


RESOURCES: dict[str, tuple[str, Callable[[Session, dict], None]]] = {
    "whoop.cycle": ("/v2/cycle", upsert_cycle),
    "whoop.sleep": ("/v2/activity/sleep", upsert_sleep),
    "whoop.recovery": ("/v2/recovery", upsert_recovery),
    "whoop.workout": ("/v2/activity/workout", upsert_workout),
}


def _sync_user(client: WhoopClient, session: Session) -> None:
    profile = client.get("/v2/user/profile/basic")
    body = client.get("/v2/user/measurement/body")
    session.merge(
        WhoopUser(
            user_id=profile["user_id"],
            first_name=profile.get("first_name"),
            last_name=profile.get("last_name"),
            email=profile.get("email"),
            height_meter=body.get("height_meter"),
            weight_kilogram=body.get("weight_kilogram"),
            max_heart_rate=body.get("max_heart_rate"),
            raw_profile=profile,
            raw_body=body,
            updated_at=utcnow(),
        )
    )
    session.commit()


def sync_all() -> dict[str, int | str]:
    """Sync every WHOOP resource. Returns {resource: record count or error message}."""
    if not _sync_lock.acquire(blocking=False):
        log.info("WHOOP sync already running; skipping")
        return {"status": "already running"}
    results: dict[str, int | str] = {}
    try:
        settings = get_settings()
        with WhoopClient() as client, SessionLocal() as session:
            try:
                _sync_user(client, session)
            except Exception as exc:  # profile failure shouldn't block the data sync
                session.rollback()
                log.exception("WHOOP profile sync failed")
                results["whoop.user"] = f"error: {exc}"

            for name, (path, upsert) in RESOURCES.items():
                state = session.get(SyncState, name) or SyncState(resource=name, records_last_run=0)
                started = utcnow()
                if state.last_success_at:
                    since = state.last_success_at - OVERLAP
                elif settings.whoop_backfill_days > 0:
                    since = started - timedelta(days=settings.whoop_backfill_days)
                else:
                    since = None  # full history
                count = 0
                try:
                    for rec in client.paginate(path, start=since):
                        upsert(session, rec)
                        count += 1
                        if count % 100 == 0:
                            session.commit()
                    state.last_success_at = started
                    state.last_error = None
                    results[name] = count
                except Exception as exc:
                    session.rollback()
                    log.exception("WHOOP sync failed for %s", name)
                    state.last_error = str(exc)[:1000]
                    results[name] = f"error: {exc}"
                state.last_attempt_at = started
                state.records_last_run = count
                session.merge(state)
                session.commit()

            try:
                results["run_matches"] = rebuild_matches(session).matched
            except Exception as exc:
                session.rollback()
                log.exception("Run matching failed after WHOOP sync")
                results["run_matches"] = f"error: {exc}"
        log.info("WHOOP sync finished: %s", results)
        return results
    finally:
        _sync_lock.release()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    init_db()
    print(sync_all())
