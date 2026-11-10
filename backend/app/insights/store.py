"""Persistance des observations privées ; aucune instruction d'ordre exchange."""

from datetime import datetime, timezone, timedelta
import json
import uuid
from app.insights.codec import bounded, public, digest


def obj(value):
    return json.loads(value) if isinstance(value, str) else value


def row(value):
    if value is None:
        return None
    return {
        k: obj(v)
        if k
        in {
            "manifest",
            "trace",
            "report",
            "body",
            "result",
            "evidence",
            "payload",
            "applied_payload",
        }
        else v
        for k, v in dict(value).items()
    }


class InsightsStore:
    async def entry_control(self, mode):
        await self._conn.execute(
            "INSERT INTO entry_controls(mode) VALUES($1::execution_mode) ON CONFLICT DO NOTHING",
            mode,
        )
        return dict(
            await self._conn.fetchrow(
                "SELECT * FROM entry_controls WHERE mode=$1::execution_mode", mode
            )
        )

    async def set_entry_permission(
        self, mode, enabled, revision, *, reason="", who="admin"
    ):
        from app.db.repository import StateTransitionError

        async with self._conn.transaction():
            await self.entry_control(mode)
            current = dict(
                await self._conn.fetchrow(
                    "SELECT * FROM entry_controls WHERE mode=$1::execution_mode FOR UPDATE",
                    mode,
                )
            )
            if (
                current["revision"] == revision + 1
                and current["enabled"] == enabled
                and current["reason"] == reason[:240]
                and current["updated_by"] == who
            ):
                return current
            if enabled:
                state, _ = await self.state(mode)
                pending = await self._conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM pending_exits WHERE mode=$1::execution_mode)",
                    mode,
                )
                if (
                    state in ("EMERGENCY_HALT", "LOCKED_CIRCUIT_BREAKER", "EXECUTING")
                    or pending
                ):
                    raise StateTransitionError(
                        "réautorisation refusée : état bloqué ou exécution à rapprocher"
                    )
            changed = await self._conn.fetchrow(
                "UPDATE entry_controls SET enabled=$2,revision=revision+1,reason=$4,updated_by=$5,updated_at=CLOCK_TIMESTAMP() "
                "WHERE mode=$1::execution_mode AND revision=$3 RETURNING *",
                mode,
                enabled,
                revision,
                reason[:240],
                who,
            )
            if not changed:
                raise StateTransitionError("révision des entrées obsolète")
            return dict(changed)

    async def save_policy(self, policy):
        if policy["id"] != digest(policy["body"]):
            raise ValueError("empreinte de politique incorrecte")
        await self._conn.execute(
            "INSERT INTO position_policies(id,body) VALUES($1,$2::jsonb) ON CONFLICT DO NOTHING",
            policy["id"],
            bounded(policy["body"]),
        )
        return policy["id"]

    async def attach_policy(self, mode, position_id, policy):
        await self.save_policy(policy)
        return await self._conn.fetchval(
            "UPDATE positions SET policy_id=$3 WHERE id=$1 AND mode=$2::execution_mode AND status='OPEN' AND policy_id IS NULL RETURNING id",
            position_id,
            mode,
            policy["id"],
        )

    async def position_policy(self, position_id):
        result = await self._conn.fetchrow(
            "SELECT p.id,p.body FROM position_policies p JOIN positions t ON t.policy_id=p.id WHERE t.id=$1",
            position_id,
        )
        return row(result)

    async def begin_cycle(self, cycle_id, mode, at, manifest):
        storage = await self.storage_state()
        if storage["free_space_bytes"] < 108 * 1024 * 1024:
            raise RuntimeError(
                "observation refusée : moins de 108 Mio disponibles (100 Mio réservés au ledger)"
            )
        await self._conn.execute(
            "INSERT INTO decision_cycles(id,mode,started_at,status,manifest) VALUES($1,$2::execution_mode,$3,'RUNNING',$4::jsonb)",
            cycle_id,
            mode,
            at,
            bounded(manifest),
        )

    async def finish_cycle(self, cycle_id, packet):
        size = len(bounded(packet).encode())
        storage = await self.storage_state()
        if storage["free_space_bytes"] < 100 * 1024 * 1024 + size:
            await self._conn.execute(
                "UPDATE decision_cycles SET finished_at=CLOCK_TIMESTAMP(),status='INCOMPLETE',error='STORAGE_BUDGET' WHERE id=$1",
                cycle_id,
            )
            return
        async with self._conn.transaction():
            for key, payload in packet.get("artifacts", {}).items():
                if key != digest(payload):
                    raise ValueError("artefact altéré")
                await self._conn.execute(
                    "INSERT INTO decision_artifacts(digest,payload) VALUES($1,$2::jsonb) ON CONFLICT DO NOTHING",
                    key,
                    bounded(payload),
                )
                await self._conn.execute(
                    "INSERT INTO decision_cycle_artifacts(cycle_id,digest) VALUES($1,$2) ON CONFLICT DO NOTHING",
                    cycle_id,
                    key,
                )
            await self._conn.execute(
                "UPDATE decision_cycles SET finished_at=CLOCK_TIMESTAMP(),status=$2,trace=$3::jsonb,report=$4::jsonb,digest=$5,error=$6,manifest=$7::jsonb WHERE id=$1",
                cycle_id,
                packet["status"],
                bounded(packet["trace"]),
                bounded(packet.get("report")),
                packet["digest"],
                packet.get("error"),
                bounded(packet["manifest"]),
            )

    async def append_opportunity(
        self,
        cycle_id,
        seq,
        mode,
        stage,
        at,
        *,
        symbol=None,
        opportunity_id=None,
        terminal=False,
        body=None,
    ):
        await self._conn.execute(
            "INSERT INTO opportunity_events(cycle_id,seq,mode,stage,observed_at,symbol,opportunity_id,terminal,body) "
            "VALUES($1,$2,$3::execution_mode,$4,$5,$6,$7,$8,$9::jsonb)",
            cycle_id,
            seq,
            mode,
            stage,
            at,
            symbol,
            opportunity_id,
            terminal,
            bounded(public(body or {})),
        )

    async def cycle_events(self, mode, cycle_id):
        return [
            row(r)
            for r in await self._conn.fetch(
                "SELECT e.* FROM opportunity_events e JOIN decision_cycles c ON c.id=e.cycle_id "
                "WHERE c.mode=$1::execution_mode AND c.id=$2 ORDER BY e.seq LIMIT 1000",
                mode,
                cycle_id,
            )
        ]

    async def recent_cycles(self, mode, limit=20):
        return [
            row(r)
            for r in await self._conn.fetch(
                "SELECT id,mode,started_at,finished_at,status,report,error FROM decision_cycles WHERE mode=$1::execution_mode ORDER BY started_at DESC,id LIMIT $2",
                mode,
                limit,
            )
        ]

    async def cycle_packet(self, mode, cycle_id):
        r = row(
            await self._conn.fetchrow(
                "SELECT * FROM decision_cycles WHERE id=$1 AND mode=$2::execution_mode",
                cycle_id,
                mode,
            )
        )
        if not r:
            return None
        artifacts = {
            a["digest"]: obj(a["payload"])
            for a in await self._conn.fetch(
                "SELECT a.digest,a.payload FROM decision_artifacts a JOIN decision_cycle_artifacts c ON c.digest=a.digest WHERE c.cycle_id=$1",
                cycle_id,
            )
        }
        return {
            "schema_version": 1,
            "manifest": r["manifest"],
            "trace": r["trace"],
            "report": r["report"],
            "status": r["status"],
            "error": r["error"],
            "digest": r["digest"],
            "artifacts": artifacts,
        }

    async def opportunities(self, mode, since, until, symbol=None, limit=5000):
        if until <= since or (until - since).days > 93:
            raise ValueError("période invalide ou supérieure à 93 jours")
        return [
            row(r)
            for r in await self._conn.fetch(
                "SELECT cycle_id,seq,opportunity_id,symbol,stage,observed_at,received_at,terminal,body FROM opportunity_events "
                "WHERE mode=$1::execution_mode AND observed_at>=$2 AND observed_at<$3 AND ($4::text IS NULL OR symbol=$4) "
                "ORDER BY observed_at DESC,cycle_id,seq LIMIT $5",
                mode,
                since,
                until,
                symbol,
                limit,
            )
        ]

    async def observation_coverage(self, mode, since, until):
        r = await self._conn.fetchrow(
            "SELECT COUNT(*) AS started,COUNT(*) FILTER(WHERE status='COMPLETE') AS complete,"
            "COUNT(*) FILTER(WHERE status='FAILED') AS failed,COUNT(*) FILTER(WHERE status='RUNNING') AS unfinished,"
            "MAX(started_at) AS last_started,MAX(finished_at) AS last_finished FROM decision_cycles "
            "WHERE mode=$1::execution_mode AND started_at>=$2 AND started_at<$3",
            mode,
            since,
            until,
        )
        return dict(r)

    async def diagnostics_input(self, mode, position_id):
        position = await self._conn.fetchrow(
            "SELECT * FROM positions WHERE id=$1 AND mode=$2::execution_mode",
            position_id,
            mode,
        )
        if not position:
            return None
        orders = await self._conn.fetch(
            "SELECT * FROM orders WHERE position_id=$1 AND filled_amount>0 ORDER BY created_at,id LIMIT 5001",
            position_id,
        )
        end = position["closed_at"] or datetime.now(timezone.utc)
        policy = await self.position_policy(position_id)
        timeframe = policy["body"]["timeframe"] if policy else "4h"
        candles = await self._conn.fetch(
            "SELECT open_time,close_price FROM market_candles_history WHERE symbol=$1 AND timeframe=$2 AND open_time>=$3 AND open_time<$4 ORDER BY open_time LIMIT 5001",
            position["symbol"],
            timeframe,
            position["opened_at"]
            - timedelta(seconds={"1h": 3600, "4h": 14400, "1d": 86400}[timeframe]),
            end,
        )
        # Snapshots give observations actually available during the position even without archived candles.
        marks = await self._conn.fetch(
            "SELECT taken_at,equity_quote,position_value_quote FROM equity_snapshots WHERE mode=$1::execution_mode AND symbol=$2 AND taken_at>=$3 AND taken_at<=$4 ORDER BY taken_at LIMIT 5001",
            mode,
            position["symbol"],
            position["opened_at"],
            end,
        )
        return {
            "position": dict(position),
            "orders": [dict(o) for o in orders[:5000]],
            "orders_truncated": len(orders) > 5000,
            "observations_truncated": len(candles) > 5000 or len(marks) > 5000,
            "candles": [dict(c) for c in candles[:5000]],
            "marks": [dict(m) for m in marks[:5000]],
            "policy": policy,
            "until": end,
            "timeframe": timeframe,
        }

    async def runtime_snapshot(self, mode):
        data = await self._conn.fetchrow(
            "SELECT payload,updated_at,revision,applied_revision,applied_payload,applied_at,apply_error FROM runtime_settings WHERE mode=$1::execution_mode",
            mode,
        )
        return row(data) or {
            "payload": {},
            "updated_at": None,
            "revision": 0,
            "applied_revision": None,
            "applied_payload": None,
            "applied_at": None,
            "apply_error": None,
        }

    async def settings_state(self, mode):
        result = await self._conn.fetchrow(
            "SELECT revision,applied_revision,applied_payload,applied_at,apply_error FROM runtime_settings WHERE mode=$1::execution_mode",
            mode,
        )
        return row(result) or {
            "revision": 0,
            "applied_revision": None,
            "applied_payload": None,
            "applied_at": None,
            "apply_error": None,
        }

    async def acknowledge_settings(self, mode, revision, payload, error=None):
        await self._conn.execute(
            "UPDATE runtime_settings SET applied_revision=CASE WHEN $4::text IS NULL THEN $2 ELSE applied_revision END,"
            "applied_payload=CASE WHEN $4::text IS NULL THEN $3::jsonb ELSE applied_payload END,applied_at=CLOCK_TIMESTAMP(),apply_error=$4 "
            "WHERE mode=$1::execution_mode AND revision=$2",
            mode,
            revision,
            bounded(public(payload)),
            error,
        )

    async def start_experiment(self, mode, manifest):
        eid = uuid.uuid4()
        await self._conn.execute(
            "INSERT INTO observation_experiments(id,mode,manifest,manifest_digest,state) VALUES($1,$2::execution_mode,$3::jsonb,$4,'RUNNING')",
            eid,
            mode,
            bounded(manifest, max_bytes=1048576),
            digest(manifest),
        )
        return eid

    async def finish_experiment(self, mode, eid, result, *, failed=False):
        await self._conn.execute(
            "UPDATE observation_experiments SET state=$3,result=$4::jsonb,finished_at=CLOCK_TIMESTAMP() WHERE id=$1 AND mode=$2::execution_mode AND state='RUNNING'",
            eid,
            mode,
            "FAILED" if failed else "COMPLETE",
            bounded(public(result)),
        )

    async def save_experiment(self, mode, manifest, result, experiment_id=None):
        experiment_id = experiment_id or uuid.uuid4()
        await self._conn.execute(
            "INSERT INTO observation_experiments(id,mode,manifest,manifest_digest,state,result,finished_at) VALUES($1,$2::execution_mode,$3::jsonb,$4,'COMPLETE',$5::jsonb,CLOCK_TIMESTAMP())",
            experiment_id,
            mode,
            bounded(manifest),
            digest(manifest),
            bounded(public(result)),
        )
        return experiment_id

    async def experiments(self, mode, limit=20):
        return [
            row(r)
            for r in await self._conn.fetch(
                "SELECT * FROM observation_experiments WHERE mode=$1::execution_mode ORDER BY created_at DESC LIMIT $2",
                mode,
                limit,
            )
        ]

    async def calibration_input(self, mode, since, until):
        # Closed trades only; all labels keep their selection population explicit.
        rows = await self._conn.fetch(
            "SELECT s.id,s.action::text,s.confidence,s.model_name,s.prompt_sha256,s.market_regime::text,s.raw_response,s.created_at,"
            "p.id AS position_id,p.policy_id,p.closed_at,p.net_pnl_quote FROM llm_signals s LEFT JOIN positions p ON p.entry_signal_id=s.id "
            "AND p.mode=s.mode AND p.closed_at<$3 WHERE s.mode=$1::execution_mode AND s.created_at>=$2 AND s.created_at<$3 "
            "ORDER BY s.created_at LIMIT 10001",
            mode,
            since,
            until,
        )
        return [dict(r) for r in rows]

    async def protection_health(self, mode):
        latest = row(
            await self._conn.fetchrow(
                "SELECT * FROM protection_observations WHERE mode=$1::execution_mode",
                mode,
            )
        )
        incidents = [
            row(r)
            for r in await self._conn.fetch(
                "SELECT * FROM protection_incidents WHERE mode=$1::execution_mode ORDER BY opened_at DESC LIMIT 100",
                mode,
            )
        ]
        return {"latest": latest, "incidents": incidents,
                "runtime_heartbeat_at": await self.runtime_heartbeat(mode)}

    async def record_protection(self, mode, observation, issues):
        async with self._conn.transaction():
            await self._conn.execute(
                "SELECT pg_advisory_xact_lock(1414416977,hashtext($1))", mode
            )
            prior = await self._conn.fetchval(
                "SELECT observed_at FROM protection_observations WHERE mode=$1::execution_mode",
                mode,
            )
            if prior is not None and prior > observation["observed_at"]:
                return False
            await self._conn.execute(
                "INSERT INTO protection_observations(mode,observed_at,body) VALUES($1::execution_mode,$2,$3::jsonb) "
                "ON CONFLICT(mode) DO UPDATE SET observed_at=EXCLUDED.observed_at,body=EXCLUDED.body",
                mode,
                observation["observed_at"],
                bounded(public(observation)),
            )
            keys = []
            for issue in issues:
                keys.append(issue["key"])
                iid = await self._conn.fetchval(
                    "INSERT INTO protection_incidents(id,mode,incident_key,severity,evidence) VALUES($1,$2::execution_mode,$3,$4,$5::jsonb) "
                    "ON CONFLICT(mode,incident_key) WHERE resolved_at IS NULL DO UPDATE SET last_seen_at=CLOCK_TIMESTAMP(),evidence=EXCLUDED.evidence RETURNING id",
                    uuid.uuid4(),
                    mode,
                    issue["key"],
                    issue["severity"],
                    bounded(public(issue)),
                )
                if issue["severity"] == "CRITICAL":
                    await self._conn.execute(
                        "INSERT INTO observation_commands(id,mode,incident_id,kind) VALUES($1,$2::execution_mode,$3,'HALT') ON CONFLICT DO NOTHING",
                        uuid.uuid4(),
                        mode,
                        iid,
                    )
            if observation.get("complete"):
                await self._conn.execute(
                    "UPDATE protection_incidents SET resolved_at=CLOCK_TIMESTAMP() WHERE mode=$1::execution_mode "
                    "AND resolved_at IS NULL AND NOT(incident_key=ANY($2::text[]))",
                    mode,
                    keys,
                )

        return True

    async def acknowledge_incident(self, mode, incident_id, who="admin"):
        return await self._conn.fetchval(
            "UPDATE protection_incidents SET acknowledged_at=CLOCK_TIMESTAMP(),acknowledged_by=$3 WHERE id=$1 AND mode=$2::execution_mode RETURNING id",
            incident_id,
            mode,
            who,
        )

    async def protection_notification_state(self, mode, observed_at, pending):
        # Une réponse tardive ne peut modifier une observation plus récente.
        await self._conn.execute(
            "UPDATE protection_observations SET body=jsonb_set(body,'{notification_pending}',to_jsonb($3::boolean)) "
            "WHERE mode=$1::execution_mode AND observed_at=$2", mode, observed_at, pending,
        )

    async def consume_observation_commands(self, mode):
        async with self._conn.transaction():
            pending = await self._conn.fetch(
                "SELECT * FROM observation_commands WHERE mode=$1::execution_mode AND applied_at IS NULL FOR UPDATE",
                mode,
            )
            if pending:
                await self.force_halt(
                    mode, "surveillance indépendante : contrôle humain requis"
                )
                await self._conn.execute(
                    "UPDATE observation_commands SET applied_at=CLOCK_TIMESTAMP() WHERE mode=$1::execution_mode AND applied_at IS NULL",
                    mode,
                )
            return len(pending)
