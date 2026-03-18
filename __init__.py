"""
USDG Auto-Claim Tool for Solana Agents
=======================================

Detects claimable USDG rewards and auto-sweeps to treasury.

Usage:
    from usdg_auto_claim import USDGClaimer, ClaimConfig, check_claimable
    
    config = ClaimConfig(network="mainnet", threshold=10.0)
    claimer = USDGClaimer(config)
    result = claimer.check_and_claim()

Modules:
    - usdg_auto_claim: Main module with USDGClaimer class
"""

from usdg_auto_claim import (
    USDGClaimer,
    ClaimConfig,
    ClaimableBalance,
    SweepResult,
    GasEstimate,
    RewardSource,
    check_claimable,
    estimate_claim_gas,
    execute_sweep,
    monitor_and_sweep,
    load_keypair,
    get_associated_token_address,
    USDG_MINT_MAINNET,
    USDC_MINT_MAINNET,
    USDT_MINT_MAINNET,
    TOKEN_PROGRAM_ID,
    ATA_PROGRAM_ID,
    RPC_ENDPOINTS,
    CircuitBreaker,
    RetryConfig,
    PriorityFeeEstimator,
    ClaimHistoryDB,
)

__all__ = [
    # Main classes
    "USDGClaimer",
    "ClaimConfig",
    "ClaimableBalance",
    "SweepResult",
    "GasEstimate",
    # Enums
    "RewardSource",
    # Functions
    "check_claimable",
    "estimate_claim_gas",
    "execute_sweep",
    "monitor_and_sweep",
    "load_keypair",
    "get_associated_token_address",
    # Constants
    "USDG_MINT_MAINNET",
    "USDC_MINT_MAINNET",
    "USDT_MINT_MAINNET",
    "TOKEN_PROGRAM_ID",
    "ATA_PROGRAM_ID",
    "RPC_ENDPOINTS",
    # Utilities
    "CircuitBreaker",
    "RetryConfig",
    "PriorityFeeEstimator",
    "ClaimHistoryDB",
]

__version__ = "2.0.0"
