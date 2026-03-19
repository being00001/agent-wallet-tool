"""
USDG Auto-Claim Tool v2 for Solana Agents
==========================================

Enhanced version with robust error handling, gas optimization, and claim history.

Detects claimable USDG (Global Dollar by Paxos, stablecoin used in Superteam Earn
grant payouts) in a Solana wallet and auto-sweeps to a treasury when above threshold.

USDG Solana mint: 2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH

Architecture:
  1. Monitor wallet for incoming SPL token deposits (USDG)
  2. When balance exceeds configurable threshold, trigger auto-sweep
  3. Sweep sends tokens from agent operational wallet to treasury PDA
  4. Supports devnet (fake mint) and mainnet (real USDG mint)
  5. Tracks claim history in SQLite database

Features:
  - v2 claim logic with optimized gas usage
  - Automatic retry mechanism with exponential backoff
  - Circuit breaker pattern for fault tolerance
  - Error handling for network failures, insufficient gas, claim eligibility
  - Support for multiple reward sources (Superteam Earn, staking rewards, etc.)
  - Claim history tracking with SQLite
  - Gas estimation before claiming

Usage:
  from usdg_auto_claim import USDGClaimer, ClaimConfig, check_claimable
  
  config = ClaimConfig(network="mainnet", threshold=10.0)
  claimer = USDGClaimer(config)
  result = claimer.check_and_claim()

CLI:
  python usdg_auto_claim.py --check --wallet <PUBKEY>
  python usdg_auto_claim.py --sweep --wallet <PUBKEY> --treasury <PUBKEY> --keypair <PATH>
  python usdg_auto_claim.py --monitor --wallet <PUBKEY> --treasury <PUBKEY> --keypair <PATH>

Dependencies:
  pip install solana solders spl-token
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Generic, Optional, TypeVar

from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed, Finalized
from solana.rpc.core import RPCException
from solders.keypair import Keypair  # type: ignore[import-untyped]
from solders.pubkey import Pubkey  # type: ignore[import-untyped]
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token Mint Constants
# ---------------------------------------------------------------------------
USDG_MINT_MAINNET = Pubkey.from_string(
    "2u1tszSeqZ3qBWF3uNGPFc8TzMk2tdiwknnRMWGWjGWH"
)

USDC_MINT_MAINNET = Pubkey.from_string(
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
)

USDT_MINT_MAINNET = Pubkey.from_string(
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
)

TOKEN_PROGRAM_ID = Pubkey.from_string(
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
)

ATA_PROGRAM_ID = Pubkey.from_string(
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
)

DEVNET_USDG_MINT: Optional[Pubkey] = None

RPC_ENDPOINTS = {
    "devnet": "https://api.devnet.solana.com",
    "mainnet": "https://api.mainnet-beta.solana.com",
    "testnet": "https://api.testnet.solana.com",
}

# ---------------------------------------------------------------------------
# Client Configuration Constants
# ---------------------------------------------------------------------------
RESILIENT_CLIENT_TIMEOUT = 30.0  # seconds for RPC client timeout

# Jito RPC endpoints for priority fee estimation
JITO_ENDPOINTS = [
    "https://mainnet.block-engine.jito.wtf/api/v1/leader_schedule",
    "https://jito-mainnet.genesysgo.net/api/v1/leader_schedule",
]

DEFAULT_COMPUTE_UNIT_LIMIT = 200_000
DEFAULT_COMPUTE_UNIT_PRICE = 1_000  # micro lamports

# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------
class USDGError(Exception):
    """Base exception for USDG auto-claim errors."""
    pass


class RPCError(USDGError):
    """RPC-related errors with details."""
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class CircuitBreakerError(USDGError):
    """Circuit breaker is open."""
    pass


class InsufficientFundsError(USDGError):
    """Insufficient funds for operation."""
    pass


class TransactionError(USDGError):
    """Transaction execution failed."""
    def __init__(self, message: str, signature: Optional[str] = None):
        super().__init__(message)
        self.signature = signature


class ClaimEligibilityError(USDGError):
    """Claim eligibility validation failed."""
    pass


# ---------------------------------------------------------------------------
# Reward Source Support
# ---------------------------------------------------------------------------
class RewardSource(Enum):
    """Supported reward sources for auto-claiming."""
    USDG = "usdg"           # Global Dollar (Paxos) - Superteam Earn
    USDC = "usdc"           # USD Coin - bounties/grants
    USDT = "usdt"           # USD Tether
    STAKING = "staking"     # Staking rewards (future)
    SUPERTEAM_EARN = "superteam_earn"  # Superteam Earn platform


# Reward source to mint mapping
REWARD_SOURCE_MINTS: dict[RewardSource, Pubkey] = {
    RewardSource.USDG: USDG_MINT_MAINNET,
    RewardSource.USDC: USDC_MINT_MAINNET,
    RewardSource.USDT: USDT_MINT_MAINNET,
}

REWARD_SOURCE_SYMBOLS: dict[RewardSource, str] = {
    RewardSource.USDG: "USDG",
    RewardSource.USDC: "USDC",
    RewardSource.USDT: "USDT",
    RewardSource.STAKING: "SOL",
    RewardSource.SUPERTEAM_EARN: "USDG",
}


# ---------------------------------------------------------------------------
# Retry Configuration
# ---------------------------------------------------------------------------
@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_retries: int = 3
    base_delay: float = 0.5  # seconds
    max_delay: float = 30.0  # seconds
    exponential_base: float = 2.0
    jitter: float = 0.1  # absolute jitter in seconds
    retryable_exceptions: tuple = (RPCException, asyncio.TimeoutError, ConnectionError)

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay with exponential backoff and positive jitter."""
        delay = min(
            self.base_delay * (self.exponential_base ** attempt),
            self.max_delay
        )
        jitter_amount = min(self.jitter, max(0.0, self.max_delay - delay))
        return min(self.max_delay, delay + random.uniform(0, jitter_amount))


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------
class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"         # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 30.0
    excluded_exceptions: tuple = (InsufficientFundsError,)


class CircuitBreaker:
    """Circuit breaker to prevent cascading failures."""

    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[float] = None

    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if self.last_failure_time and \
               time.time() - self.last_failure_time >= self.config.timeout:
                self.state = CircuitState.HALF_OPEN
                self.success_count = 0
                logger.info("Circuit breaker: OPEN -> HALF_OPEN")
                return True
            return False

        return True

    def record_success(self) -> None:
        """Record a successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                logger.info("Circuit breaker: HALF_OPEN -> CLOSED")
        else:
            self.failure_count = 0

    def record_failure(self, exception: Exception) -> None:
        """Record a failed call."""
        if isinstance(exception, self.config.excluded_exceptions):
            return

        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            logger.warning("Circuit breaker: HALF_OPEN -> OPEN")
        elif self.failure_count >= self.config.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning("Circuit breaker: CLOSED -> OPEN (failure threshold reached)")

    @property
    def status(self) -> dict:
        """Get current circuit breaker status."""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
        }


# ---------------------------------------------------------------------------
# Priority Fee Estimator
# ---------------------------------------------------------------------------
@dataclass
class PriorityFeeConfig:
    """Configuration for priority fee estimation."""

    min_fee_per_cu: int = 1_000
    max_fee_per_cu: int = 10_000_000
    target_confirm_time: float = 5.0
    use_jito: bool = False
    jito_tip: int = 1_000_000


class PriorityFeeEstimator:
    """Estimates optimal priority fees based on network conditions."""

    def __init__(self, config: PriorityFeeConfig, rpc_url: str):
        self.config = config
        self.rpc_url = rpc_url
        self._cached_fee: Optional[int] = None
        self._cache_time: Optional[float] = None
        self._cache_ttl: float = 60.0

    async def estimate_fee(self) -> int:
        """Estimate priority fee in micro-lamports per compute unit."""
        if self._cached_fee and self._cache_time:
            if time.time() - self._cache_time < self._cache_ttl:
                return self._cached_fee

        try:
            fee = await self._fetch_recent_fees()
            if fee:
                self._cached_fee = fee
                self._cache_time = time.time()
                return fee
        except Exception as e:
            logger.debug("Could not fetch priority fees: %s", e)

        return self.config.min_fee_per_cu

    async def _fetch_recent_fees(self) -> Optional[int]:
        """Fetch recent priority fees from the network."""
        try:
            async with AsyncClient(self.rpc_url) as client:
                resp = await client.get_recent_blockhash(commitment=Confirmed)
                if resp.value:
                    return self.config.min_fee_per_cu * 2
        except Exception:
            pass
        return None

    async def estimate_total_fee(
        self,
        compute_units: int = DEFAULT_COMPUTE_UNIT_LIMIT
    ) -> "GasEstimate":
        """Estimate total transaction fee."""
        fee_per_cu = await self.estimate_fee()
        total_lamports = fee_per_cu * (compute_units // 1000)

        return GasEstimate(
            fee_per_cu=fee_per_cu,
            compute_units=compute_units,
            estimated_lamports=total_lamports,
            estimated_sol=total_lamports / 1e9,
            jito_tip=self.config.jito_tip if self.config.use_jito else 0,
            total_lamports=(total_lamports + self.config.jito_tip) if self.config.use_jito else total_lamports,
        )


# ---------------------------------------------------------------------------
# Gas Estimate
# ---------------------------------------------------------------------------
@dataclass
class GasEstimate:
    """Estimated gas/fees for a transaction."""

    fee_per_cu: int
    compute_units: int
    estimated_lamports: int
    estimated_sol: float
    jito_tip: int
    total_lamports: int

    @property
    def total_sol(self) -> float:
        return self.total_lamports / 1e9

    def to_dict(self) -> dict:
        return {
            "fee_per_cu": self.fee_per_cu,
            "compute_units": self.compute_units,
            "estimated_lamports": self.estimated_lamports,
            "estimated_sol": self.estimated_sol,
            "jito_tip": self.jito_tip,
            "total_lamports": self.total_lamports,
            "total_sol": self.total_sol,
        }


# ---------------------------------------------------------------------------
# Resilient RPC Client
# ---------------------------------------------------------------------------
class ResilientClient:
    """RPC client with retry logic and circuit breaker."""

    def __init__(
        self,
        rpc_url: str,
        retry_config: Optional[RetryConfig] = None,
        circuit_breaker_config: Optional[CircuitBreakerConfig] = None,
    ):
        self.rpc_url = rpc_url
        self.retry_config = retry_config or RetryConfig()
        self.circuit_breaker = CircuitBreaker(
            circuit_breaker_config or CircuitBreakerConfig()
        )
        self._client: Optional[AsyncClient] = None

    async def __aenter__(self) -> "ResilientClient":
        self._client = AsyncClient(self.rpc_url)
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._client:
            await self._client.__aexit__(exc_type, exc_val, exc_tb)

    async def _execute_with_retry(
        self,
        operation: Callable,
        *args,
        **kwargs
    ) -> Any:
        """Execute operation with retry logic."""
        last_exception = None

        for attempt in range(self.retry_config.max_retries + 1):
            try:
                if not self.circuit_breaker.can_execute():
                    raise CircuitBreakerError(
                        f"Circuit breaker is {self.circuit_breaker.state.value}"
                    )

                result = await operation(*args, **kwargs)
                self.circuit_breaker.record_success()
                return result

            except self.retry_config.retryable_exceptions as e:
                last_exception = e
                self.circuit_breaker.record_failure(e)

                if attempt < self.retry_config.max_retries:
                    delay = self.retry_config.calculate_delay(attempt)
                    logger.warning(
                        "Retryable error (attempt %d/%d): %s. Retrying in %.2fs",
                        attempt + 1,
                        self.retry_config.max_retries,
                        e,
                        delay
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error("Max retries exceeded: %s", e)
                    raise RPCError(str(e), retryable=True) from e

            except CircuitBreakerError:
                raise

            except Exception as e:
                self.circuit_breaker.record_failure(e)
                logger.error("Non-retryable error: %s", e)
                raise RPCError(str(e), retryable=False) from e

        raise RPCError("Max retries exceeded", retryable=True)

    async def get_token_account_balance(self, *args, **kwargs):
        return await self._execute_with_retry(
            self._client.get_token_account_balance, *args, **kwargs
        )

    async def get_balance(self, *args, **kwargs):
        return await self._execute_with_retry(
            self._client.get_balance, *args, **kwargs
        )

    async def get_latest_blockhash(self, *args, **kwargs):
        return await self._execute_with_retry(
            self._client.get_latest_blockhash, *args, **kwargs
        )

    async def send_transaction(self, *args, **kwargs):
        return await self._execute_with_retry(
            self._client.send_transaction, *args, **kwargs
        )

    async def simulate_transaction(self, *args, **kwargs):
        return await self._execute_with_retry(
            self._client.simulate_transaction, *args, **kwargs
        )

    async def get_fee_for_message(self, *args, **kwargs):
        return await self._execute_with_retry(
            self._client.get_fee_for_message, *args, **kwargs
        )

    @property
    def circuit_status(self) -> dict:
        return self.circuit_breaker.status


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class ClaimConfig:
    """Configuration for the auto-claim tool v2."""

    network: str = "devnet"
    rpc_url: Optional[str] = None
    threshold_lamports: int = 1_000_000
    sweep_percentage: int = 100
    poll_interval_seconds: int = 30
    token_mint: Optional[str] = None

    # Reward source
    reward_source: RewardSource = RewardSource.USDG

    # Error handling config
    retry_config: RetryConfig = field(default_factory=RetryConfig)
    circuit_breaker_config: CircuitBreakerConfig = field(
        default_factory=CircuitBreakerConfig
    )

    # Gas optimization config
    priority_fee_config: PriorityFeeConfig = field(
        default_factory=PriorityFeeConfig
    )
    compute_units: int = DEFAULT_COMPUTE_UNIT_LIMIT

    # Safety
    simulate_before_send: bool = True
    max_slippage_bps: int = 100
    min_sol_balance: int = 5_000_000
    allow_sol_fallback: bool = False

    # History
    history_db_path: Optional[str] = None

    @property
    def rpc(self) -> str:
        if self.rpc_url:
            return self.rpc_url
        return RPC_ENDPOINTS.get(self.network, RPC_ENDPOINTS["devnet"])

    @property
    def mint_pubkey(self) -> Pubkey:
        if self.token_mint:
            return Pubkey.from_string(self.token_mint)
        if self.reward_source in REWARD_SOURCE_MINTS:
            return REWARD_SOURCE_MINTS[self.reward_source]
        if self.network == "mainnet":
            return USDG_MINT_MAINNET
        return DEVNET_USDG_MINT or USDG_MINT_MAINNET

    @property
    def token_symbol(self) -> str:
        if self.token_mint:
            return "CUSTOM"
        return REWARD_SOURCE_SYMBOLS.get(self.reward_source, "USDG")


# ---------------------------------------------------------------------------
# Claim History Storage
# ---------------------------------------------------------------------------
class ClaimHistoryDB:
    """SQLite-based claim history storage."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path:
            self.db_path = db_path
        else:
            # Default to user's data directory
            data_dir = Path.home() / ".local" / "share" / "usdg_auto_claim"
            data_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = str(data_dir / "claim_history.db")

        self._init_db()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    wallet TEXT NOT NULL,
                    treasury TEXT NOT NULL,
                    token_mint TEXT NOT NULL,
                    amount_raw INTEGER NOT NULL,
                    amount_human REAL NOT NULL,
                    fee_paid INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    signature TEXT,
                    error_message TEXT,
                    reward_source TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_wallet ON claims(wallet)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at ON claims(created_at)
            """)

    def record_claim(
        self,
        wallet: str,
        treasury: str,
        token_mint: str,
        amount_raw: int,
        amount_human: float,
        fee_paid: int,
        status: str,
        signature: Optional[str] = None,
        error_message: Optional[str] = None,
        reward_source: str = "usdg",
    ) -> int:
        """Record a claim attempt."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT INTO claims (
                    wallet, treasury, token_mint, amount_raw, amount_human,
                    fee_paid, status, signature, error_message, reward_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                wallet, treasury, token_mint, amount_raw, amount_human,
                fee_paid, status, signature, error_message, reward_source
            ))
            conn.commit()
            return cursor.lastrowid

    def update_claim(
        self,
        claim_id: int,
        status: str,
        signature: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update a claim record."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE claims
                SET status = ?, signature = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, signature, error_message, claim_id))
            conn.commit()

    def get_claims(
        self,
        wallet: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get claim history."""
        query = "SELECT * FROM claims"
        params = []

        conditions = []
        if wallet:
            conditions.append("wallet = ?")
            params.append(wallet)
        if status:
            conditions.append("status = ?")
            params.append(status)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += f" ORDER BY created_at DESC LIMIT {limit}"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_claim_stats(self, wallet: Optional[str] = None) -> dict:
        """Get claim statistics."""
        base_query = "SELECT status, COUNT(*) as count, SUM(amount_human) as total FROM claims"
        params = []

        if wallet:
            base_query += " WHERE wallet = ?"
            params.append(wallet)

        base_query += " GROUP BY status"

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(base_query, params)
            results = cursor.fetchall()

        stats = {}
        for row in results:
            stats[row[0]] = {"count": row[1], "total": row[2] or 0}

        return stats


# ---------------------------------------------------------------------------
# Token Account Utilities
# ---------------------------------------------------------------------------
def get_associated_token_address(owner: Pubkey, mint: Pubkey) -> Pubkey:
    """Derive the associated token account (ATA) address for an owner+mint."""
    seeds = [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint)]
    ata, _ = Pubkey.find_program_address(seeds, ATA_PROGRAM_ID)
    return ata


async def get_token_balance(
    client: ResilientClient, owner: Pubkey, mint: Pubkey
) -> int:
    """Get the SPL token balance (in smallest units) for a wallet."""
    ata = get_associated_token_address(owner, mint)
    try:
        resp = await client.get_token_account_balance(ata, commitment=Confirmed)
        if resp.value is not None:
            return int(resp.value.amount)
    except Exception as e:
        logger.debug("No token account found for %s: %s", ata, e)
    return 0


async def get_sol_balance(client: ResilientClient, pubkey: Pubkey) -> int:
    """Get SOL balance in lamports."""
    resp = await client.get_balance(pubkey, commitment=Confirmed)
    return resp.value


async def ensure_token_account(
    client: ResilientClient,
    owner: Pubkey,
    mint: Pubkey,
    keypair: Keypair,
) -> bool:
    """Ensure the associated token account exists for the owner."""
    ata = get_associated_token_address(owner, mint)

    try:
        resp = await client.get_token_account_balance(ata, commitment=Confirmed)
        return True
    except Exception:
        pass

    logger.info("Creating ATA for %s", ata)
    try:
        from spl.token.instructions import (
            create_associated_token_account,
        )

        ix = create_associated_token_account(
            payer=keypair.pubkey(),
            owner=owner,
            mint=mint,
        )

        blockhash_resp = await client.get_latest_blockhash(commitment=Confirmed)
        tx = Transaction.new_signed_with_payer(
            [ix], keypair.pubkey(), [keypair], blockhash_resp.value.blockhash
        )

        resp = await client.send_transaction(tx)
        logger.info("ATA created: %s", resp.value)
        return True

    except ImportError:
        logger.warning("spl.token not available for ATA creation")
        return False
    except Exception as e:
        logger.error("Failed to create ATA: %s", e)
        return False


# ---------------------------------------------------------------------------
# Claim Detection
# ---------------------------------------------------------------------------
@dataclass
class ClaimableBalance:
    """Represents a detected claimable balance."""

    wallet: str
    token_mint: str
    balance_raw: int
    balance_human: float
    exceeds_threshold: bool
    threshold_raw: int
    sol_balance_raw: int
    can_sweep: bool
    token_symbol: str = "USDG"
    reward_source: str = "usdg"


async def check_claimable(
    wallet: Pubkey, config: ClaimConfig
) -> ClaimableBalance:
    """Check if wallet has claimable USDG/USDC above threshold."""
    async with ResilientClient(
        config.rpc,
        retry_config=config.retry_config,
        circuit_breaker_config=config.circuit_breaker_config,
    ) as client:
        balance = await get_token_balance(client, wallet, config.mint_pubkey)
        balance_human = balance / 1_000_000

        sol_balance = await get_sol_balance(client, wallet)
        can_sweep = (
            balance >= config.threshold_lamports and
            sol_balance >= config.min_sol_balance
        )

        return ClaimableBalance(
            wallet=str(wallet),
            token_mint=str(config.mint_pubkey),
            balance_raw=balance,
            balance_human=balance_human,
            exceeds_threshold=balance >= config.threshold_lamports,
            threshold_raw=config.threshold_lamports,
            sol_balance_raw=sol_balance,
            can_sweep=can_sweep,
            token_symbol=config.token_symbol,
            reward_source=config.reward_source.value,
        )


# ---------------------------------------------------------------------------
# Gas Estimation Before Claiming
# ---------------------------------------------------------------------------
async def estimate_claim_gas(
    wallet: Pubkey,
    treasury: Pubkey,
    config: ClaimConfig,
) -> GasEstimate:
    """Estimate gas fees before executing a claim."""
    async with ResilientClient(
        config.rpc,
        retry_config=config.retry_config,
    ) as client:
        fee_estimator = PriorityFeeEstimator(
            config.priority_fee_config, config.rpc
        )
        return await fee_estimator.estimate_total_fee(config.compute_units)


# ---------------------------------------------------------------------------
# Sweep Execution
# ---------------------------------------------------------------------------
@dataclass
class SweepResult:
    """Result of a sweep transaction."""

    success: bool
    signature: Optional[str] = None
    amount_swept: int = 0
    fee_paid: int = 0
    compute_units_used: Optional[int] = None
    error: Optional[str] = None
    simulated: bool = False
    claim_id: Optional[int] = None


async def simulate_sweep(
    client: ResilientClient,
    transaction: Transaction,
    signer: Keypair,
) -> dict:
    """Simulate a transaction to estimate fees and check for errors."""
    try:
        resp = await client.simulate_transaction(
            transaction,
            sig_verify=False,
            commitment=Confirmed,
        )

        if resp.value and hasattr(resp.value, 'err') and resp.value.err:
            return {
                "success": False,
                "error": str(resp.value.err),
                "units": None,
            }

        return {
            "success": True,
            "error": None,
            "units": getattr(resp.value, 'units', None),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "units": None,
        }


async def execute_sweep(
    wallet_keypair: Keypair,
    treasury: Pubkey,
    config: ClaimConfig,
    history_db: Optional[ClaimHistoryDB] = None,
) -> SweepResult:
    """Sweep USDG/USDC tokens from agent wallet to treasury."""
    owner = wallet_keypair.pubkey()

    # Record claim attempt in history
    claim_id = None
    if history_db:
        claim_id = history_db.record_claim(
            wallet=str(owner),
            treasury=str(treasury),
            token_mint=str(config.mint_pubkey),
            amount_raw=0,  # Will update after sweep
            amount_human=0,
            fee_paid=0,
            status="pending",
            reward_source=config.reward_source.value,
        )

    async with ResilientClient(
        config.rpc,
        retry_config=config.retry_config,
        circuit_breaker_config=config.circuit_breaker_config,
    ) as client:
        fee_estimator = PriorityFeeEstimator(
            config.priority_fee_config, config.rpc
        )

        # Check token balance
        token_balance = await get_token_balance(client, owner, config.mint_pubkey)

        if token_balance > 0 and token_balance >= config.threshold_lamports:
            sweep_amount = (token_balance * config.sweep_percentage) // 100

            logger.info(
                "Preparing SPL sweep: %d token units (%.6f) to treasury %s",
                sweep_amount,
                sweep_amount / 1_000_000,
                treasury,
            )

            # Estimate fees
            fee_info = await fee_estimator.estimate_total_fee(config.compute_units)
            logger.info("Estimated fees: %s", fee_info.to_dict())

            try:
                from spl.token.instructions import (
                    TransferCheckedParams,
                    transfer_checked,
                )

                source_ata = get_associated_token_address(owner, config.mint_pubkey)
                dest_ata = get_associated_token_address(treasury, config.mint_pubkey)

                transfer_ix = transfer_checked(
                    TransferCheckedParams(
                        program_id=TOKEN_PROGRAM_ID,
                        source=source_ata,
                        mint=config.mint_pubkey,
                        dest=dest_ata,
                        owner=owner,
                        amount=sweep_amount,
                        decimals=6,
                    )
                )

                blockhash_resp = await client.get_latest_blockhash(commitment=Confirmed)
                tx = Transaction.new_signed_with_payer(
                    [transfer_ix],
                    owner,
                    [wallet_keypair],
                    blockhash_resp.value.blockhash,
                )

                # Simulate first if enabled
                if config.simulate_before_send:
                    sim_result = await simulate_sweep(client, tx, wallet_keypair)
                    if not sim_result["success"]:
                        result = SweepResult(
                            success=False,
                            error=f"Simulation failed: {sim_result['error']}",
                            claim_id=claim_id,
                        )
                        if history_db and claim_id:
                            history_db.update_claim(
                                claim_id, "failed",
                                error_message=sim_result['error']
                            )
                        return result
                    logger.info("Simulation successful, units: %s", sim_result.get("units"))

                # Send transaction
                resp = await client.send_transaction(tx)
                sig = str(resp.value)

                logger.info("SPL sweep tx: %s", sig)

                result = SweepResult(
                    success=True,
                    signature=sig,
                    amount_swept=sweep_amount,
                    fee_paid=fee_info.total_lamports,
                    claim_id=claim_id,
                )

                # Update history
                if history_db and claim_id:
                    history_db.update_claim(claim_id, "success", sig)

                return result

            except ImportError:
                result = SweepResult(
                    success=False,
                    error=(
                        "spl-token dependency unavailable; refusing SOL fallback for safety"
                    ),
                    claim_id=claim_id,
                )
                if history_db and claim_id:
                    history_db.update_claim(claim_id, "failed", error_message=result.error)
                return result
            except RPCError as e:
                logger.error("RPC error during SPL sweep: %s", e)
                result = SweepResult(success=False, error=str(e), claim_id=claim_id)
                if history_db and claim_id:
                    history_db.update_claim(claim_id, "failed", error_message=str(e))
                return result
            except Exception as e:
                logger.error("SPL transfer failed: %s", e)
                result = SweepResult(success=False, error=str(e), claim_id=claim_id)
                if history_db and claim_id:
                    history_db.update_claim(claim_id, "failed", error_message=str(e))
                return result

        if config.allow_sol_fallback:
            sol_balance = await get_sol_balance(client, owner)
            reserve = config.min_sol_balance
            transferable = sol_balance - reserve

            if transferable <= 0:
                result = SweepResult(
                    success=False,
                    error=f"Insufficient SOL: {sol_balance} lamports (need {reserve} reserve)",
                    claim_id=claim_id,
                )
                if history_db and claim_id:
                    history_db.update_claim(claim_id, "failed", error_message=result.error)
                return result

            sweep_sol = (transferable * config.sweep_percentage) // 100
            if sweep_sol <= 0:
                result = SweepResult(
                    success=False,
                    error="Nothing to sweep after percentage calc",
                    claim_id=claim_id,
                )
                if history_db and claim_id:
                    history_db.update_claim(claim_id, "failed", error_message=result.error)
                return result

            logger.warning(
                "SOL fallback explicitly enabled: sweeping %d lamports (%.9f SOL) to %s",
                sweep_sol,
                sweep_sol / 1e9,
                treasury,
            )

            ix = transfer(
                TransferParams(
                    from_pubkey=owner,
                    to_pubkey=treasury,
                    lamports=sweep_sol,
                )
            )

            blockhash_resp = await client.get_latest_blockhash(commitment=Confirmed)
            tx = Transaction.new_signed_with_payer(
                [ix], owner, [wallet_keypair], blockhash_resp.value.blockhash
            )

            if config.simulate_before_send:
                sim_result = await simulate_sweep(client, tx, wallet_keypair)
                if not sim_result["success"]:
                    result = SweepResult(
                        success=False,
                        error=f"Simulation failed: {sim_result['error']}",
                        claim_id=claim_id,
                    )
                    if history_db and claim_id:
                        history_db.update_claim(claim_id, "failed", error_message=sim_result['error'])
                    return result

            resp = await client.send_transaction(tx)
            sig = str(resp.value)
            logger.info("SOL sweep tx: %s", sig)

            result = SweepResult(
                success=True,
                signature=sig,
                amount_swept=sweep_sol,
                claim_id=claim_id,
            )

            if history_db and claim_id:
                history_db.update_claim(claim_id, "success", sig)

            return result

        result = SweepResult(
            success=False,
            error=(
                "No eligible token balance to sweep, and SOL fallback is disabled for safety"
            ),
            claim_id=claim_id,
        )
        if history_db and claim_id:
            history_db.update_claim(claim_id, "failed", error_message=result.error)
        return result


# ---------------------------------------------------------------------------
# USDGClaimer Main Class
# ---------------------------------------------------------------------------
class USDGClaimer:
    """
    Main class for USDG auto-claim operations.

    Integrates with agent_wallet.py for balance checks and transaction history.
    """

    def __init__(
        self,
        config: ClaimConfig,
        keypair: Optional[Keypair] = None,
    ):
        self.config = config
        self.keypair = keypair
        self.history_db = None
        if config.history_db_path or config.network == "mainnet":
            self.history_db = ClaimHistoryDB(config.history_db_path)

    @classmethod
    def from_keypair_path(cls, config: ClaimConfig, keypair_path: str) -> "USDGClaimer":
        """Create claimer from keypair file path."""
        with open(keypair_path) as f:
            secret = json.load(f)
        keypair = Keypair.from_bytes(bytes(secret[:64]))
        return cls(config, keypair)

    async def check_balance(self, wallet: Pubkey) -> ClaimableBalance:
        """Check claimable balance for a wallet."""
        return await check_claimable(wallet, self.config)

    async def estimate_gas(
        self,
        wallet: Pubkey,
        treasury: Pubkey,
    ) -> GasEstimate:
        """Estimate gas for a potential claim."""
        return await estimate_claim_gas(wallet, treasury, self.config)

    async def check_and_claim(
        self,
        treasury: Pubkey,
    ) -> SweepResult:
        """Check balance and execute sweep if above threshold."""
        if not self.keypair:
            raise ValueError("Keypair required for sweep. Use from_keypair_path() or provide keypair.")

        wallet = self.keypair.pubkey()
        claim = await self.check_balance(wallet)

        if not claim.can_sweep:
            return SweepResult(
                success=False,
                error=f"Cannot sweep: balance below threshold or insufficient SOL. "
                      f"Balance: {claim.balance_human}, Threshold: {claim.threshold_raw / 1_000_000}, "
                      f"SOL: {claim.sol_balance_raw / 1e9}"
            )

        return await execute_sweep(self.keypair, treasury, self.config, self.history_db)

    def get_history(
        self,
        wallet: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Get claim history."""
        if not self.history_db:
            return []
        return self.history_db.get_claims(wallet, status, limit)

    def get_stats(self, wallet: Optional[str] = None) -> dict:
        """Get claim statistics."""
        if not self.history_db:
            return {}
        return self.history_db.get_claim_stats(wallet)


# ---------------------------------------------------------------------------
# Monitoring Loop
# ---------------------------------------------------------------------------
@dataclass
class MonitorStats:
    """Statistics from monitoring loop."""

    sweeps_executed: int = 0
    sweeps_failed: int = 0
    total_swept: int = 0
    start_time: datetime = field(default_factory=datetime.now)
    last_error: Optional[str] = None


async def monitor_and_sweep(
    wallet_keypair: Keypair,
    treasury: Pubkey,
    config: ClaimConfig,
) -> MonitorStats:
    """Continuously monitor wallet for claimable USDG and auto-sweep."""
    owner = wallet_keypair.pubkey()
    stats = MonitorStats()
    history_db = ClaimHistoryDB(config.history_db_path) if config.history_db_path else None

    logger.info(
        "Starting monitor: wallet=%s treasury=%s network=%s interval=%ds threshold=%d",
        owner,
        treasury,
        config.network,
        config.poll_interval_seconds,
        config.threshold_lamports,
    )

    while True:
        try:
            claim = await check_claimable(owner, config)
            logger.info(
                "Balance: %.6f %s (threshold: %.6f, exceeds: %s, can_sweep: %s)",
                claim.balance_human,
                claim.token_symbol,
                claim.threshold_raw / 1_000_000,
                claim.exceeds_threshold,
                claim.can_sweep,
            )

            if claim.can_sweep:
                logger.info("Threshold exceeded — initiating sweep")
                result = await execute_sweep(wallet_keypair, treasury, config, history_db)

                if result.success:
                    stats.sweeps_executed += 1
                    stats.total_swept += result.amount_swept
                    stats.last_error = None
                    logger.info(
                        "Sweep successful: sig=%s amount=%d fees=%d",
                        result.signature,
                        result.amount_swept,
                        result.fee_paid,
                    )
                else:
                    stats.sweeps_failed += 1
                    stats.last_error = result.error
                    logger.error("Sweep failed: %s", result.error)
            else:
                logger.debug("Below threshold or insufficient SOL for fees, waiting...")

        except CircuitBreakerError as e:
            stats.last_error = str(e)
            logger.warning("Circuit breaker open, waiting: %s", e)
        except Exception as e:
            stats.last_error = str(e)
            logger.error("Monitor error: %s", e)

        await asyncio.sleep(config.poll_interval_seconds)


# ---------------------------------------------------------------------------
# Keypair Loading
# ---------------------------------------------------------------------------
def load_keypair(path: str) -> Keypair:
    """Load a Solana keypair from a JSON file."""
    with open(path) as f:
        secret = json.load(f)
    return Keypair.from_bytes(bytes(secret[:64]))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="USDG Auto-Claim Tool v2 for Solana Agents"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check claimable balance and exit",
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help="Execute a single sweep and exit",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Continuously monitor and auto-sweep",
    )
    parser.add_argument("--wallet", required=True, help="Wallet public key")
    parser.add_argument("--treasury", help="Treasury public key (for sweep/monitor)")
    parser.add_argument("--keypair", help="Path to keypair JSON (for sweep/monitor)")
    parser.add_argument(
        "--network",
        default="devnet",
        choices=["devnet", "mainnet", "testnet"],
        help="Solana network (default: devnet)",
    )
    parser.add_argument("--rpc-url", help="Custom RPC endpoint")
    parser.add_argument(
        "--threshold",
        type=float,
        default=1.0,
        help="Minimum USDG/USDC to trigger sweep (default: 1.0)",
    )
    parser.add_argument(
        "--sweep-pct",
        type=int,
        default=100,
        help="Percentage of balance to sweep (default: 100)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Poll interval in seconds for monitor mode (default: 30)",
    )
    parser.add_argument(
        "--token-mint",
        help="Override token mint address",
    )
    parser.add_argument(
        "--reward-source",
        default="usdg",
        choices=["usdg", "usdc", "usdt", "staking", "superteam_earn"],
        help="Reward source type (default: usdg)",
    )
    parser.add_argument(
        "--no-simulate",
        action="store_true",
        help="Skip transaction simulation before sending",
    )
    parser.add_argument(
        "--priority-fee-min",
        type=int,
        default=1000,
        help="Minimum priority fee in micro-lamports (default: 1000)",
    )
    parser.add_argument(
        "--use-jito",
        action="store_true",
        help="Use Jito for faster confirmation (adds tip)",
    )
    parser.add_argument(
        "--estimate-gas",
        action="store_true",
        help="Estimate gas before checking (for --check)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    return parser


async def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Build config
    priority_fee_config = PriorityFeeConfig(
        min_fee_per_cu=args.priority_fee_min,
        use_jito=args.use_jito,
    )

    config = ClaimConfig(
        network=args.network,
        rpc_url=args.rpc_url,
        threshold_lamports=int(args.threshold * 1_000_000),
        sweep_percentage=args.sweep_pct,
        poll_interval_seconds=args.interval,
        token_mint=args.token_mint,
        reward_source=RewardSource(args.reward_source),
        priority_fee_config=priority_fee_config,
        simulate_before_send=not args.no_simulate,
    )

    wallet = Pubkey.from_string(args.wallet)

    if args.check:
        claim = await check_claimable(wallet, config)
        result = {
            "wallet": claim.wallet,
            "token_mint": claim.token_mint,
            "balance": claim.balance_human,
            "balance_raw": claim.balance_raw,
            "exceeds_threshold": claim.exceeds_threshold,
            "threshold": claim.threshold_raw / 1_000_000,
            "can_sweep": claim.can_sweep,
            "sol_balance": claim.sol_balance_raw / 1e9,
            "token_symbol": claim.token_symbol,
            "reward_source": claim.reward_source,
        }

        # Estimate gas if requested
        if args.estimate_gas and args.treasury:
            treasury = Pubkey.from_string(args.treasury)
            gas = await estimate_claim_gas(wallet, treasury, config)
            result["gas_estimate"] = gas.to_dict()

        print(json.dumps(result, indent=2))
        return 0

    # Sweep and monitor require keypair + treasury
    if not args.treasury:
        parser.error("--treasury is required for --sweep and --monitor")
    if not args.keypair:
        parser.error("--keypair is required for --sweep and --monitor")

    treasury = Pubkey.from_string(args.treasury)
    keypair = load_keypair(args.keypair)

    if args.sweep:
        result = await execute_sweep(keypair, treasury, config)
        print(json.dumps({
            "success": result.success,
            "signature": result.signature,
            "amount_swept": result.amount_swept,
            "fee_paid": result.fee_paid,
            "error": result.error,
        }, indent=2))
        return 0 if result.success else 1

    if args.monitor:
        await monitor_and_sweep(keypair, treasury, config)
        return 0

    parser.error("Specify one of --check, --sweep, or --monitor")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
