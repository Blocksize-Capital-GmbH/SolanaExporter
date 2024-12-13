import os
import unittest
from unittest.mock import patch

from solanaexporter.solanaExporter import SolanaExporter


class TestSolanaExporterIntegration(unittest.TestCase):
    def setUp(self):
        # Define environment variables as a dictionary
        self.env = {
            "SOLANA_RPC_URL": "https://api.testnet.solana.com",
            "SOLANA_PUBLIC_RPC_URL": "https://api.testnet.solana.com",
            "EXPORTER_PORT": "7896",
            "POLL_INTERVAL": "10",
            "VOTE_PUBKEY": "6jJK69aeuLbVnM6nUKnmMMwyQG2rNjKNFrfM459kfAdL",
            "VALIDATOR_PUBKEY": "4EKxPYXmBha7ADnZphFFC13RaKNYLZCiQPKuSV8YWRZc",
        }

    @patch.dict("os.environ", {}, clear=True)
    def test_collect_metrics(self):
        """Integration test for collect_metrics with live RPC calls."""
        # Update the os.environ dictionary with test environment variables
        os.environ.update(self.env)

        # Initialize SolanaExporter with environment configuration
        exporter = SolanaExporter(config_source="fromEnv")

        # Collect metrics
        exporter.collect_metrics()

        # Validate key metrics after collection
        self.assertGreater(
            exporter.slot_number._value.get(), 0, "Slot number should be greater than 0"
        )
        self.assertGreaterEqual(
            exporter.balance._value.get(), 0, "Balance should not be negative"
        )
        self.assertIn(
            exporter.health_status._value.get(),
            {0, 1},
            "Health status should be either 0 (unhealthy) or 1 (healthy)",
        )
        self.assertGreaterEqual(exporter.epoch._value.get(), 0, "Epoch should not be negative")

    @patch.dict("os.environ", {}, clear=True)
    def test_get_stake_accounts(self):
        """Integration test for _get_stake_accounts with live RPC calls."""
        # Update the os.environ dictionary with test environment variables
        os.environ.update(self.env)

        # Initialize SolanaExporter with environment configuration
        exporter = SolanaExporter(config_source="fromEnv")

        # Fetch stake accounts
        stake_accounts = exporter._get_stake_accounts()

        # Validate the structure and content of stake accounts
        self.assertIsInstance(stake_accounts, list, "Stake accounts should be a list")
        if stake_accounts:  # If accounts are returned, validate their format
            self.assertTrue(all(account.is_valid() for account in stake_accounts))
            self.assertTrue(all(account.result is not None for account in stake_accounts))


if __name__ == "__main__":
    unittest.main()
