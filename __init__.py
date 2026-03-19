"""Agent Wallet Tool for Solana.

Unified exports for wallet management, DAO integration, and token auto-claim.
"""

try:
    from .usdg_auto_claim import (
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
        RPC_ENDPOINTS as CLAIM_RPC_ENDPOINTS,
        CircuitBreaker,
        RetryConfig,
        PriorityFeeEstimator,
        ClaimHistoryDB,
    )
    from .agent_wallet import agent_wallet_status, get_transaction_history, jupiter_quote, jupiter_swap
    from .dao_integration import (
        get_dao_info,
        list_daos,
        create_proposal,
        cast_vote,
        get_voting_power,
        get_proposal_status,
    )
except ImportError:
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
        RPC_ENDPOINTS as CLAIM_RPC_ENDPOINTS,
        CircuitBreaker,
        RetryConfig,
        PriorityFeeEstimator,
        ClaimHistoryDB,
    )
    from agent_wallet import agent_wallet_status, get_transaction_history, jupiter_quote, jupiter_swap
    from dao_integration import (
        get_dao_info,
        list_daos,
        create_proposal,
        cast_vote,
        get_voting_power,
        get_proposal_status,
    )

__all__ = [
    "USDGClaimer",
    "ClaimConfig",
    "ClaimableBalance",
    "SweepResult",
    "GasEstimate",
    "RewardSource",
    "check_claimable",
    "estimate_claim_gas",
    "execute_sweep",
    "monitor_and_sweep",
    "load_keypair",
    "get_associated_token_address",
    "USDG_MINT_MAINNET",
    "USDC_MINT_MAINNET",
    "USDT_MINT_MAINNET",
    "TOKEN_PROGRAM_ID",
    "ATA_PROGRAM_ID",
    "CLAIM_RPC_ENDPOINTS",
    "CircuitBreaker",
    "RetryConfig",
    "PriorityFeeEstimator",
    "ClaimHistoryDB",
    "agent_wallet_status",
    "get_transaction_history",
    "jupiter_quote",
    "jupiter_swap",
    "get_dao_info",
    "list_daos",
    "create_proposal",
    "cast_vote",
    "get_voting_power",
    "get_proposal_status",
]

__version__ = "2.1.0"
