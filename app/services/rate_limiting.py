"""Provider-neutral HTTP request rate limiting primitives.

The default backend is deliberately process-local memory: it is suitable for
local development, tests, and single-process deployments, but it is not a
cluster-wide quota.  The backend boundary is intentionally small so a
distributed store such as Redis can replace it without changing route/domain
contracts.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class RateLimitPolicyId(StrEnum):
    AUTH_LOGIN = "auth_login"
    INTEGRATION_INGEST = "integration_ingest"
    OUTBOUND_DELIVERY = "outbound_delivery"
    AI_CONVERSATION = "ai_conversation"


@dataclass(frozen=True)
class RateLimitPolicy:
    policy_id: RateLimitPolicyId
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("Rate limit must be at least 1")
        if self.window_seconds < 1:
            raise ValueError("Rate-limit window must be at least 1 second")


@dataclass(frozen=True)
class RateLimitDecision:
    policy_id: RateLimitPolicyId
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int
    reset_after_seconds: int


class RateLimitBackend(Protocol):
    def check(self, policy: RateLimitPolicy, scope_key: str) -> RateLimitDecision:
        """Consume one request for a trusted scope and return the decision."""


@dataclass
class _FixedWindowBucket:
    window_started_at: float
    window_seconds: int
    count: int


class InMemoryFixedWindowRateLimitBackend:
    """Concurrency-safe, bounded, process-local fixed-window backend."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_buckets: int = 10_000,
        cleanup_batch_size: int = 500,
    ) -> None:
        if max_buckets < 1:
            raise ValueError("max_buckets must be positive")
        if cleanup_batch_size < 1:
            raise ValueError("cleanup_batch_size must be positive")
        self._clock = clock
        self._max_buckets = max_buckets
        self._cleanup_batch_size = cleanup_batch_size
        self._lock = threading.Lock()
        self._buckets: dict[tuple[RateLimitPolicyId, str], _FixedWindowBucket] = {}

    def check(self, policy: RateLimitPolicy, scope_key: str) -> RateLimitDecision:
        now = self._clock()
        key = (policy.policy_id, scope_key)
        with self._lock:
            self._cleanup_expired(now, max_to_remove=self._cleanup_batch_size)
            bucket = self._buckets.get(key)
            if bucket is None or self._is_expired(bucket, policy, now):
                bucket = _FixedWindowBucket(
                    window_started_at=now,
                    window_seconds=policy.window_seconds,
                    count=0,
                )
                self._buckets[key] = bucket

            reset_after_seconds = _ceil_positive(
                bucket.window_started_at + policy.window_seconds - now
            )
            if bucket.count < policy.limit:
                bucket.count += 1
                decision = RateLimitDecision(
                    policy_id=policy.policy_id,
                    allowed=True,
                    limit=policy.limit,
                    remaining=max(policy.limit - bucket.count, 0),
                    retry_after_seconds=0,
                    reset_after_seconds=reset_after_seconds,
                )
            else:
                decision = RateLimitDecision(
                    policy_id=policy.policy_id,
                    allowed=False,
                    limit=policy.limit,
                    remaining=0,
                    retry_after_seconds=max(reset_after_seconds, 1),
                    reset_after_seconds=reset_after_seconds,
                )

            self._enforce_bound(now)
            return decision

    def bucket_count(self) -> int:
        """Expose deterministic backend size for focused cleanup tests only."""
        with self._lock:
            return len(self._buckets)

    def cleanup_expired(self) -> int:
        """Remove stale buckets deterministically without needing a thread."""
        now = self._clock()
        with self._lock:
            return self._cleanup_expired(now, max_to_remove=len(self._buckets))

    def _cleanup_expired(self, now: float, *, max_to_remove: int) -> int:
        removed = 0
        for key, bucket in list(self._buckets.items()):
            if removed >= max_to_remove:
                break
            if self._is_bucket_stale(bucket, now):
                del self._buckets[key]
                removed += 1
        return removed

    def _enforce_bound(self, now: float) -> None:
        if len(self._buckets) <= self._max_buckets:
            return
        self._cleanup_expired(now, max_to_remove=len(self._buckets))
        if len(self._buckets) <= self._max_buckets:
            return
        excess = len(self._buckets) - self._max_buckets
        oldest_keys = sorted(
            self._buckets,
            key=lambda item: self._buckets[item].window_started_at,
        )[:excess]
        for key in oldest_keys:
            self._buckets.pop(key, None)

    @staticmethod
    def _is_expired(
        bucket: _FixedWindowBucket,
        policy: RateLimitPolicy,
        now: float,
    ) -> bool:
        return now >= bucket.window_started_at + policy.window_seconds

    @staticmethod
    def _is_bucket_stale(
        bucket: _FixedWindowBucket,
        now: float,
    ) -> bool:
        return now >= bucket.window_started_at + bucket.window_seconds


class RateLimitExceeded(RuntimeError):
    def __init__(self, decision: RateLimitDecision) -> None:
        self.decision = decision
        super().__init__("Rate limit exceeded")


class RateLimitService:
    def __init__(
        self,
        backend: RateLimitBackend,
        *,
        enabled: bool = True,
    ) -> None:
        self._backend = backend
        self._enabled = enabled

    def check(self, policy: RateLimitPolicy, scope_key: str) -> RateLimitDecision:
        if not self._enabled:
            return RateLimitDecision(
                policy_id=policy.policy_id,
                allowed=True,
                limit=policy.limit,
                remaining=policy.limit,
                retry_after_seconds=0,
                reset_after_seconds=policy.window_seconds,
            )
        return self._backend.check(policy, scope_key)

    def enforce(self, policy: RateLimitPolicy, scope_key: str) -> RateLimitDecision:
        decision = self.check(policy, scope_key)
        if not decision.allowed:
            raise RateLimitExceeded(decision)
        return decision


def rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    return {
        "Retry-After": str(max(decision.retry_after_seconds, 1)),
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(max(decision.reset_after_seconds, 0)),
    }


def _ceil_positive(value: float) -> int:
    return max(math.ceil(value), 0)
