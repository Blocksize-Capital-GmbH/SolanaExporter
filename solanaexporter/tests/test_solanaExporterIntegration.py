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
            "LABEL": "Integration_Test_Label",
            "VERSION": "1.0.0",
            "DOUBLE_ZERO_FEES_ADDRESS": "11111111111111111111111111111111",
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
        self.assertGreater(exporter.slot_number._value.get(), 0, "Slot number should be greater than 0")
        self.assertGreaterEqual(exporter.balance._value.get(), 0, "Balance should not be negative")
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

    @patch.dict("os.environ", {}, clear=True)
    def test_double_zero_fees_balance_mainnet(self):
        """Integration test for double_zero_fees_address balance with mainnet address."""
        # Use mainnet configuration with the specific address
        mainnet_env = {
            "SOLANA_RPC_URL": "https://api.mainnet-beta.solana.com",
            "SOLANA_PUBLIC_RPC_URL": "https://api.mainnet-beta.solana.com",
            "EXPORTER_PORT": "7896",
            "POLL_INTERVAL": "120",
            "VOTE_PUBKEY": "HMk1qny4fvMnajErxjXG5kT89JKV4cx1PKa9zhQBF9ib",
            "VALIDATOR_PUBKEY": "BH6aHw9y4Ejes5KdPYA3ezwERCvJd2zMzGLKze45kfy3",
            "LABEL": "Integration_Test_Mainnet",
            "VERSION": "v0.712.30006",
            "DOUBLE_ZERO_FEES_ADDRESS": "4wm9PFxxRox3vgntwVdwbqvkRDjyjaqEdSiohosEJSj5",
        }

        os.environ.update(mainnet_env)

        # Initialize SolanaExporter with environment configuration
        exporter = SolanaExporter(config_source="fromEnv")

        # Collect metrics
        exporter.collect_metrics()

        # Validate that double_zero_balance is set and greater than 0
        double_zero_balance = exporter.double_zero_balance._value.get()
        self.assertIsNotNone(double_zero_balance, "Double zero balance should be set")
        self.assertGreater(double_zero_balance, 0, "Double zero balance should be greater than 0")

        # Based on Solscan, the expected balance should be around 4.89 SOL
        # We'll use a reasonable range to account for any changes
        self.assertGreater(
            double_zero_balance, 4.0, f"Double zero balance should be at least 4.0 SOL, got {double_zero_balance}"
        )
        self.assertLess(
            double_zero_balance,
            10.0,
            f"Double zero balance should be less than 10.0 SOL (sanity check), got {double_zero_balance}",
        )

        print(f"\n✓ Double zero fees address balance: {double_zero_balance} SOL")


if __name__ == "__main__":
    unittest.main()
