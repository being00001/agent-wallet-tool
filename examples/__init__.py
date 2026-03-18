"""
Integration Examples for USDG Auto-Claim with agent_wallet.py
============================================================

This package contains examples demonstrating how to use usdg_auto_claim.py
together with agent_wallet.py for comprehensive autonomous agent wallet management.

Examples:
    - usdg_claim_integration.py: Full integration examples

Usage:
    python examples/usdg_claim_integration.py
    python examples/usdg_claim_integration.py --example 3
"""

from usdg_claim_integration import (
    example_wallet_status_integration,
    example_transaction_history_integration,
    example_check_usdg_balance,
    example_estimate_gas,
    example_claimer_configuration,
    example_full_claiming_workflow,
    example_monitoring_loop_simulation,
    example_reward_source_options,
    example_error_handling,
)

__all__ = [
    "example_wallet_status_integration",
    "example_transaction_history_integration",
    "example_check_usdg_balance",
    "example_estimate_gas",
    "example_claimer_configuration",
    "example_full_claiming_workflow",
    "example_monitoring_loop_simulation",
    "example_reward_source_options",
    "example_error_handling",
]
