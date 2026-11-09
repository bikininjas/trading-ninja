"""Enregistre les frontières I/O et rejoue le vrai orchestrateur sans réseau ni base."""

from contextlib import asynccontextmanager
from dataclasses import asdict, fields, is_dataclass
import inspect
import threading
import platform
from decimal import getcontext
from pathlib import Path
import subprocess
from functools import lru_cache
import ccxt

from app.insights.codec import SECRET, encode, decode, digest, bounded, code_fingerprint


@lru_cache(maxsize=1)
def runtime_identity():
    try:
        root = Path(__file__).resolve().parents[3]
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=root, text=True
            )
        )
    except (OSError, subprocess.CalledProcessError):
        commit = None
        dirty = None
    return {
        "python": platform.python_version(),
        "ccxt": ccxt.__version__,
        "decimal_precision": getcontext().prec,
        "decimal_rounding": getcontext().rounding,
        "source_commit": commit,
        "source_dirty": dirty,
    }


class Unreplayable(ValueError):
    pass


class ReplayDivergence(ValueError):
    pass


class Recorder:
    def __init__(self, settings):
        self.secrets = set()

        def collect(value):
            mapping = (
                {f.name: getattr(value, f.name) for f in fields(value)}
                if is_dataclass(value)
                else value
                if isinstance(value, dict)
                else {}
            )
            for key, item in mapping.items():
                if SECRET.search(str(key)) and isinstance(item, str) and len(item) >= 4:
                    self.secrets.add(item)
                if is_dataclass(item) or isinstance(item, dict):
                    collect(item)

        collect(settings)
        self.calls = []
        self.artifacts = {}
        self.payload_bytes = 0
        self.failure = None
        self._mutex = threading.Lock()
        self.manifest = {
            "schema_version": 1,
            "code_fingerprint": code_fingerprint(),
            "runtime": runtime_identity(),
            "settings": encode(settings),
            "properties": {},
            "methods": {},
            "model": settings.llm.model,
            "model_digest": None,
            "inference_mode": "RECORDED_ACTIONS",
            "data_provenance": "RECORDED_IO",
            "max_packet_bytes": 8 * 1024 * 1024,
        }

    def scrub(self, value):
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, "REDACTED")
            return value
        if isinstance(value, list):
            return [self.scrub(v) for v in value]
        if isinstance(value, dict):
            return {k: self.scrub(v) for k, v in value.items()}
        return value

    def _payload(self, value):
        data = self.scrub(encode(value))
        size = len(bounded(data).encode())
        key = digest(data) if size > 2048 else None
        if key is None or key not in self.artifacts:
            self.payload_bytes += size
        if self.payload_bytes > 7 * 1024 * 1024:
            raise ValueError("budget de trace dépassé")
        if key:
            self.artifacts[key] = data
            return {"artifact": key}
        return {"value": data}

    def call(self, target, method, args, kwargs, result=None, error=None):
        # Instrumentation must never interrupt an already open position or a fill.
        with self._mutex:
            if self.failure:
                return
            try:
                if len(self.calls) >= 5000:
                    raise ValueError("limite de trace dépassée")
                call = {
                    "target": target,
                    "method": method,
                    "args": self._payload(args),
                    "kwargs": self._payload(kwargs),
                }
                if error:
                    call["error"] = {
                        "type": type(error).__name__,
                        "message": self.scrub(str(error))[:500],
                    }
                else:
                    call["result"] = self._payload(result)
                self.calls.append(call)
            except Exception as exc:
                self.failure = type(exc).__name__

    def proxy(self, target, source):
        return RecordingProxy(self, target, source)

    def packet(self, report=None, error=None):
        out = {
            "schema_version": 1,
            "manifest": self.manifest,
            "trace": self.calls,
            "artifacts": self.artifacts,
            "report": self.scrub(encode(report)),
            "status": "FAILED" if error else "COMPLETE",
            "error": type(error).__name__ + ": " + self.scrub(str(error))[:500]
            if error
            else None,
        }
        try:
            if self.failure:
                raise ValueError(self.failure)
            out["digest"] = digest(out)
            bounded(out)
        except (ValueError, TypeError):
            out.update(
                status="INCOMPLETE",
                trace=[],
                artifacts={},
                report=None,
                error="RECORDER_LIMIT_OR_ENCODING",
            )
            out["digest"] = digest({k: v for k, v in out.items() if k != "digest"})
        return out


class RecordingProxy:
    def __init__(self, recorder, target, source):
        self._recorder, self._target, self._source = recorder, target, source
        recorder.manifest["methods"][target] = {}
        recorder.manifest["properties"][target] = {}

    def __getattr__(self, name):
        source = getattr(self._source, name)
        if name.startswith("_"):
            return source
        recorder, target = self._recorder, self._target
        if not callable(source):
            recorder.manifest["properties"][target][name] = encode(source)
            return source
        if name in ("cycle_lock", "transaction"):
            recorder.manifest["methods"][target][name] = "context"
            return source
        if inspect.iscoroutinefunction(source):
            recorder.manifest["methods"][target][name] = "async"

            async def run(*args, **kwargs):
                try:
                    result = await source(*args, **kwargs)
                except Exception as exc:
                    recorder.call(target, name, args, kwargs, error=exc)
                    raise
                recorder.call(target, name, args, kwargs, result=result)
                return result

            return run
        recorder.manifest["methods"][target][name] = "sync"

        def run(*args, **kwargs):
            try:
                result = source(*args, **kwargs)
            except Exception as exc:
                recorder.call(target, name, args, kwargs, error=exc)
                raise
            recorder.call(target, name, args, kwargs, result=result)
            return result

        return run


def validate_packet(packet, *, require_current_code=True):
    bounded(packet)
    if packet.get("schema_version") != 1 or packet.get("status") != "COMPLETE":
        raise Unreplayable("cycle incomplet ou schéma inconnu")
    expected = packet.get("digest")
    if not expected or expected != digest(
        {k: v for k, v in packet.items() if k != "digest"}
    ):
        raise Unreplayable("empreinte de paquet incorrecte")
    if (
        require_current_code
        and packet["manifest"].get("code_fingerprint") != code_fingerprint()
    ):
        raise Unreplayable("version du code incompatible")
    identity = packet["manifest"].get("runtime", {})
    current = runtime_identity()
    if any(
        identity.get(k) != current[k]
        for k in ("python", "ccxt", "decimal_precision", "decimal_rounding")
    ):
        raise Unreplayable("environnement de calcul incompatible")
    if len(packet["trace"]) > 5000:
        raise Unreplayable("trace trop longue")
    for key, value in packet.get("artifacts", {}).items():
        if key != digest(value):
            raise Unreplayable("artefact modifié")
    return packet


class ReplayCursor:
    def __init__(self, packet):
        self.packet, self.offset = packet, 0

    def payload(self, data):
        if "value" in data:
            return decode(data["value"])
        try:
            return decode(self.packet["artifacts"][data["artifact"]])
        except KeyError as exc:
            raise Unreplayable("artefact manquant") from exc

    def call(self, target, method, args, kwargs):
        if self.offset >= len(self.packet["trace"]):
            raise ReplayDivergence(
                f"{self.offset}: appel supplémentaire {target}.{method}"
            )
        call = self.packet["trace"][self.offset]
        self.offset += 1
        if (call["target"], call["method"]) != (target, method):
            raise ReplayDivergence(
                f"{self.offset}: attendu {call['target']}.{call['method']}, reçu {target}.{method}"
            )
        if encode(args) != encode(self.payload(call["args"])) or encode(
            kwargs
        ) != encode(self.payload(call["kwargs"])):
            raise ReplayDivergence(
                f"{self.offset}: arguments différents pour {target}.{method}"
            )
        if "error" in call:
            from app.market.feed import FeedError
            from app.db.repository import StateTransitionError
            from app.execution.live import LiveExecutionError

            classes = {
                c.__name__: c
                for c in (
                    FeedError,
                    StateTransitionError,
                    LiveExecutionError,
                    RuntimeError,
                    ValueError,
                    TypeError,
                    KeyError,
                )
            }
            raise classes.get(call["error"]["type"], RuntimeError)(
                call["error"]["message"]
            )
        return self.payload(call["result"])


class ReplayProxy:
    def __init__(self, cursor, target):
        self._cursor, self._target = cursor, target

    def __getattr__(self, name):
        manifest = self._cursor.packet["manifest"]
        target = self._target
        if name in manifest["properties"].get(target, {}):
            return decode(manifest["properties"][target][name])
        kind = manifest["methods"].get(target, {}).get(name)
        if kind is None:
            raise AttributeError(name)
        if kind == "context":

            @asynccontextmanager
            async def context(*a, **kw):
                yield

            return context
        if kind == "async":

            async def call(*a, **kw):
                return self._cursor.call(target, name, a, kw)

            return call
        return lambda *a, **kw: self._cursor.call(target, name, a, kw)


async def replay_cycle(packet):
    validate_packet(packet)
    from app.runtime.orchestrator import Orchestrator

    cursor = ReplayCursor(packet)
    proxies = {
        k: ReplayProxy(cursor, k)
        for k in ("repo", "exchange", "llm", "broker", "clock")
    }
    settings = decode(packet["manifest"]["settings"])
    instance = Orchestrator(
        settings,
        proxies["repo"],
        proxies["exchange"],
        proxies["llm"],
        proxies["broker"],
        now=proxies["clock"].now,
        settings_loader=proxies["clock"].settings,
        insights=False,
        id_factory=proxies["clock"].new_id,
        perf_clock=proxies["clock"].perf,
    )
    state = packet["manifest"].get("runtime_state", {})
    instance._base_settings = (
        decode(state["base_settings"]) if state.get("base_settings") else settings
    )
    instance._settings_updated_at = decode(state.get("settings_updated_at"))
    instance._model_digest = packet["manifest"].get("model_digest")
    try:
        result = await instance.run_once()
    except (ReplayDivergence, Unreplayable):
        raise
    except Exception as exc:
        raise ReplayDivergence(f"{cursor.offset}: échec du rejeu : {exc}") from exc
    if cursor.offset != len(packet["trace"]):
        raise ReplayDivergence(f"{cursor.offset}: événements non consommés")
    if encode(result) != packet["report"]:
        raise ReplayDivergence("rapport final différent")
    return {
        "status": "IDENTICAL",
        "calls": cursor.offset,
        "report": asdict(result),
        "mode": "OFFLINE_RECORDED_IO",
        "code_fingerprint": code_fingerprint(),
    }
