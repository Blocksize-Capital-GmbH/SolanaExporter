"""SFDP version-policy monitoring for local Solana validator RPC results."""

import math
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import requests
from prometheus_client import Gauge

SFDP_POLICY_URL = "https://api.solana.org/api/community/v1/sfdp_required_versions"
SUPPORTED_CLUSTERS = {"mainnet-beta", "testnet"}
POLICY_CACHE_SECONDS = 15 * 60
POLICY_STALE_SECONDS = 60 * 60
FAILURE_RETRY_SECONDS = 60
CLIENT_FIELDS = {
    "agave": ("agave_min_version", "agave_max_version"),
    "jito": ("agave_min_version", "agave_max_version"),
    "frankendancer": ("firedancer_min_version", "firedancer_max_version"),
    "firedancer": ("firedancer_full_min_version", "firedancer_full_max_version"),
}


class SfdpMonitor:
    """Expose SFDP policy state from local-node results and a cached policy."""

    def __init__(
        self,
        registry: Any,
        cluster: str,
        client: str = "agave",
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Create SFDP metrics for one configured validator client and cluster."""
        self.registry = registry
        self.cluster = cluster if cluster in SUPPORTED_CLUSTERS else "unknown"
        self.client = client
        self._clock = clock
        self._policy: Optional[List[Dict[str, Any]]] = None
        self._policy_success_at: Optional[float] = None
        self._cached_cluster: Optional[str] = None
        self._last_fetch_attempt: Optional[float] = None
        self._version_labels: Optional[Tuple[str, str, str, str]] = None

        self.node_version_info = Gauge(
            "solana_node_version_info",
            "Local Solana node version and feature set",
            ["version", "feature_set", "client", "cluster"],
            registry=registry,
        )
        self.upgrade_due_seconds = Gauge(
            "solana_sfdp_upgrade_due_seconds",
            "Estimated seconds until the next SFDP-required upgrade",
            registry=registry,
        )
        self.upgrade_required = Gauge(
            "solana_sfdp_upgrade_required",
            "Whether a listed SFDP minimum exceeds the local node version",
            registry=registry,
        )
        self.policy_current_epoch_covered = Gauge(
            "solana_sfdp_policy_current_epoch_covered",
            "Whether the SFDP policy has an explicit current-epoch entry",
            registry=registry,
        )
        self.version_compliant = Gauge(
            "solana_sfdp_version_compliant",
            "Whether the local version meets explicit current-epoch SFDP bounds",
            registry=registry,
        )
        self.policy_last_success_timestamp_seconds = Gauge(
            "solana_sfdp_policy_last_success_timestamp_seconds",
            "Unix timestamp of the most recent successfully parsed SFDP policy",
            registry=registry,
        )
        self.policy_fetch_success = Gauge(
            "solana_sfdp_policy_fetch_success",
            "Whether SFDP policy is currently available from cache or last fetch",
            registry=registry,
        )
        self.policy_max_epoch = Gauge(
            "solana_sfdp_policy_max_epoch",
            "Highest epoch in the cached SFDP policy",
            registry=registry,
        )
        self.upgrade_due_epoch = Gauge(
            "solana_sfdp_upgrade_due_epoch",
            "Earliest listed epoch whose minimum exceeds the local version",
            registry=registry,
        )
        self._set_unavailable_policy_metrics()

    def update(
        self,
        version_result: Optional[Dict[str, Any]],
        epoch_info: Optional[Dict[str, Any]],
        performance_samples: Optional[List[Dict[str, Any]]],
    ) -> None:
        """Update all metrics without allowing policy or input failures to escape."""
        try:
            self._update(version_result, epoch_info, performance_samples)
        except Exception:
            self._set_unavailable_policy_metrics()

    def _update(
        self,
        version_result: Optional[Dict[str, Any]],
        epoch_info: Optional[Dict[str, Any]],
        performance_samples: Optional[List[Dict[str, Any]]],
    ) -> None:
        version = self._set_node_version(version_result)
        policy = self._get_policy()
        epoch = _integer(epoch_info, "epoch")

        if policy is None:
            self._set_policy_derived_unavailable()
            return

        self.policy_last_success_timestamp_seconds.set(self._policy_success_at or math.nan)
        epochs = [_integer(record, "epoch") for record in policy]
        valid_epochs = [value for value in epochs if value is not None]
        self.policy_max_epoch.set(max(valid_epochs) if valid_epochs else math.nan)

        if epoch is None:
            self.policy_current_epoch_covered.set(math.nan)
            self.version_compliant.set(math.nan)
        else:
            current = _record_for_epoch(policy, epoch)
            self.policy_current_epoch_covered.set(1 if current is not None else 0)
            self.version_compliant.set(self._current_compliance(version, current))

        due_epoch = self._next_upgrade_epoch(version, policy)
        if due_epoch is None:
            self.upgrade_required.set(math.nan if version is None else 0)
            self.upgrade_due_epoch.set(math.nan)
            self.upgrade_due_seconds.set(math.nan if version is None else math.inf)
        else:
            self.upgrade_required.set(1)
            self.upgrade_due_epoch.set(due_epoch)
            self.upgrade_due_seconds.set(self._due_seconds(due_epoch, epoch_info, performance_samples))

    def _set_node_version(
        self, version_result: Optional[Dict[str, Any]]
    ) -> Optional[Tuple[Tuple[int, ...], Tuple[Any, ...]]]:
        raw_version = _value(version_result, "solana-core", "solana_core", "version")
        feature_set = _value(version_result, "feature-set", "feature_set")
        version_text = raw_version.strip() if isinstance(raw_version, str) else ""
        parsed = _parse_version(version_text, self.client) if version_text else None

        if not version_text:
            if self._version_labels is not None:
                self.node_version_info.remove(*self._version_labels)
                self._version_labels = None
            return None

        labels = (version_text, str(feature_set or ""), self.client, self.cluster)
        if self._version_labels is not None and self._version_labels != labels:
            self.node_version_info.remove(*self._version_labels)
        self.node_version_info.labels(*labels).set(1)
        self._version_labels = labels
        return parsed

    def _get_policy(self) -> Optional[List[Dict[str, Any]]]:
        if self.cluster not in SUPPORTED_CLUSTERS or _client_fields(self.client) is None:
            self.policy_fetch_success.set(math.nan)
            self.policy_last_success_timestamp_seconds.set(math.nan)
            return None

        now = self._clock()
        if self._cached_cluster != self.cluster:
            self._policy = None
            self._policy_success_at = None
            self._cached_cluster = self.cluster
            self._last_fetch_attempt = None

        if self._policy is not None and self._policy_success_at is not None:
            age = now - self._policy_success_at
            if age <= POLICY_CACHE_SECONDS:
                self.policy_fetch_success.set(1)
                return self._policy

        if self._last_fetch_attempt is not None and now - self._last_fetch_attempt < FAILURE_RETRY_SECONDS:
            self.policy_fetch_success.set(0)
            return self._stale_policy(now)

        try:
            self._last_fetch_attempt = now
            response = requests.get(SFDP_POLICY_URL, params={"cluster": self.cluster}, timeout=10)
            response.raise_for_status()
            payload = response.json()
            records = payload.get("data") if isinstance(payload, dict) else None
            if not _valid_policy(records, self.cluster, self.client):
                raise ValueError("malformed SFDP policy")
            self._policy = records
            self._policy_success_at = now
            self.policy_fetch_success.set(1)
            return records
        except (requests.RequestException, ValueError, TypeError):
            self.policy_fetch_success.set(0)
            return self._stale_policy(now)

    def _stale_policy(self, now: float) -> Optional[List[Dict[str, Any]]]:
        if self._policy is not None and self._policy_success_at is not None:
            if now - self._policy_success_at <= POLICY_STALE_SECONDS:
                return self._policy
        self._policy = None
        return None

    def _next_upgrade_epoch(
        self, version: Optional[Tuple[Tuple[int, ...], Tuple[Any, ...]]], policy: Sequence[Dict[str, Any]]
    ) -> Optional[int]:
        if version is None:
            return None
        fields = _client_fields(self.client)
        if fields is None:
            return None
        field, _ = fields
        due_epochs = []
        for record in policy:
            minimum = _parse_version(record.get(field), self.client)
            epoch = _integer(record, "epoch")
            if minimum is not None and epoch is not None and _compare_versions(minimum, version) > 0:
                due_epochs.append(epoch)
        return min(due_epochs) if due_epochs else None

    def _current_compliance(
        self, version: Optional[Tuple[Tuple[int, ...], Tuple[Any, ...]]], record: Optional[Dict[str, Any]]
    ) -> float:
        if record is None or version is None:
            return math.nan
        fields = _client_fields(self.client)
        if fields is None:
            return math.nan
        minimum_field, maximum_field = fields
        minimum = _parse_version(record.get(minimum_field), self.client)
        maximum = _parse_version(record.get(maximum_field), self.client)
        if (minimum is not None and _compare_versions(version, minimum) < 0) or (
            maximum is not None and _compare_versions(version, maximum) > 0
        ):
            return 0
        return 1

    def _due_seconds(
        self, due_epoch: int, epoch_info: Optional[Dict[str, Any]], samples: Optional[List[Dict[str, Any]]]
    ) -> float:
        current_epoch = _integer(epoch_info, "epoch")
        slots_in_epoch = _integer(epoch_info, "slotsInEpoch", "slots_in_epoch")
        slot_index = _integer(epoch_info, "slotIndex", "slot_index")
        seconds_per_slot = _seconds_per_slot(samples)
        if (
            current_epoch is None
            or slots_in_epoch is None
            or slot_index is None
            or seconds_per_slot is None
            or slots_in_epoch <= 0
            or slot_index >= slots_in_epoch
        ):
            return math.nan
        return ((due_epoch - current_epoch) * slots_in_epoch - slot_index) * seconds_per_slot

    def _set_policy_derived_unavailable(self) -> None:
        self.policy_current_epoch_covered.set(math.nan)
        self.version_compliant.set(math.nan)
        self.upgrade_required.set(math.nan)
        self.upgrade_due_epoch.set(math.nan)
        self.upgrade_due_seconds.set(math.nan)
        self.policy_max_epoch.set(math.nan)

    def _set_unavailable_policy_metrics(self) -> None:
        self.policy_fetch_success.set(0)
        self.policy_last_success_timestamp_seconds.set(self._policy_success_at or math.nan)
        self._set_policy_derived_unavailable()


def _client_fields(client: str) -> Optional[Tuple[str, str]]:
    """Return policy field names for an explicitly configured client family."""
    return CLIENT_FIELDS.get(client)


def _parse_version(value: Any, client: str) -> Optional[Tuple[Tuple[int, ...], Tuple[Any, ...]]]:
    """Parse numeric semantic versions, ignoring build metadata and ordering prereleases."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("v"):
        text = text[1:]
    # Jito's package suffix labels the distribution rather than an Agave prerelease.
    # Only normalize it when the caller explicitly configured the Jito client.
    if client == "jito" and text.endswith("-jito"):
        text = text[: -len("-jito")]
    text = text.split("+", 1)[0]
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(?:-([0-9A-Za-z.-]+))?", text)
    if match is None:
        return None
    numbers = tuple(int(part) for part in match.group(1).split("."))
    prerelease = match.group(2)
    if prerelease is None:
        return numbers, (1,)
    parts: List[Any] = [0]
    for part in prerelease.split("."):
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return numbers, tuple(parts)


def _compare_versions(
    left: Tuple[Tuple[int, ...], Tuple[Any, ...]], right: Tuple[Tuple[int, ...], Tuple[Any, ...]]
) -> int:
    """Compare parsed semantic versions, treating omitted numeric components as zero."""
    length = max(len(left[0]), len(right[0]))
    left_numbers = left[0] + (0,) * (length - len(left[0]))
    right_numbers = right[0] + (0,) * (length - len(right[0]))
    if left_numbers != right_numbers:
        return 1 if left_numbers > right_numbers else -1
    if left[1] == right[1]:
        return 0
    return 1 if left[1] > right[1] else -1


def _integer(value: Any, *keys: str) -> Optional[int]:
    candidate = _value(value, *keys)
    if isinstance(candidate, bool):
        return None
    if isinstance(candidate, int):
        return candidate if candidate >= 0 else None
    if isinstance(candidate, str) and re.fullmatch(r"0|[1-9]\d*", candidate):
        return int(candidate)
    return None


def _value(value: Any, *keys: str) -> Any:
    if not isinstance(value, dict):
        return None
    for key in keys:
        if key in value:
            return value[key]
    return None


def _record_for_epoch(policy: Sequence[Dict[str, Any]], epoch: int) -> Optional[Dict[str, Any]]:
    for record in policy:
        if _integer(record, "epoch") == epoch:
            return record
    return None


def _valid_policy(records: Any, cluster: str, client: str) -> bool:
    """Validate the selected client's policy before deriving a safe pass result.

    A present ``null`` bound is intentionally unbounded. Missing both selected
    fields is unknown, because it cannot safely be treated as no requirement.
    """
    fields = _client_fields(client)
    if fields is None or not isinstance(records, list) or not records:
        return False
    minimum_field, maximum_field = fields
    observed: Dict[int, Tuple[Any, Any]] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("cluster") != cluster:
            return False
        epoch = _integer(record, "epoch")
        if epoch is None or (minimum_field not in record and maximum_field not in record):
            return False
        minimum_raw = record.get(minimum_field)
        maximum_raw = record.get(maximum_field)
        minimum = _parse_version(minimum_raw, client) if minimum_raw is not None else None
        maximum = _parse_version(maximum_raw, client) if maximum_raw is not None else None
        if (minimum_raw is not None and minimum is None) or (maximum_raw is not None and maximum is None):
            return False
        if minimum is not None and maximum is not None and _compare_versions(minimum, maximum) > 0:
            return False
        bounds = (minimum_raw, maximum_raw)
        if epoch in observed and observed[epoch] != bounds:
            return False
        observed[epoch] = bounds
    return True


def _seconds_per_slot(samples: Optional[List[Dict[str, Any]]]) -> Optional[float]:
    if not isinstance(samples, list):
        return None
    total_seconds = 0.0
    total_slots = 0
    for sample in samples:
        slots = _integer(sample, "numSlots", "num_slots")
        seconds = _value(sample, "samplePeriodSecs", "sample_period_secs")
        if slots is None or slots <= 0 or isinstance(seconds, bool):
            continue
        try:
            seconds_value = float(seconds)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(seconds_value) or seconds_value <= 0:
            continue
        total_slots += slots
        total_seconds += seconds_value
    return total_seconds / total_slots if total_slots else None
