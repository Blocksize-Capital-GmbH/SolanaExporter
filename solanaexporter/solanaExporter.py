import os
import time
from typing import List, Literal, Optional

from exporter.jsonRPCRequest import JsonRPCRequest
from exporter.jsonRPCResponse import JsonRPCResponse
from exporter.rpcExporter import RPCExporter
from prometheus_client import Gauge


class SolanaExporter(RPCExporter):
    def __init__(self, config_source: str, config_file: Optional[str] = None):
        super().__init__(network="solana", config_source=config_source, config_file=config_file)

        # Prometheus metrics setup
        self.slot_number = Gauge(
            "solana_slot_number",
            "Current slot number of the Solana validator",
            registry=self.registry,
        )
        self.absolute_slot_number = Gauge(
            "solana_absolute_slot_number",
            "Absolute slot number of the Solana chain",
            registry=self.registry,
        )
        self.slot_lag = Gauge(
            "solana_slot_lag",
            "Slot number lag of validator vs the Solana chain",
            registry=self.registry,
        )
        self.sync_status = Gauge(
            "solana_sync_status",
            "Node sync status (1 for synced, 0 for not synced)",
            registry=self.registry,
        )
        self.slot_time = Gauge(
            "solana_slot_time",
            "Time taken to process a slot",
            registry=self.registry,
        )
        self.epoch = Gauge(
            "solana_epoch",
            "Current Solana epoch",
            registry=self.registry,
        )
        self.balance = Gauge(
            "solana_account_balance", "Validator's account balance", registry=self.registry
        )
        self.health_status = Gauge(
            "solana_health_status", "Health status of the Solana node", registry=self.registry
        )
        self.total_delegated_stake = Gauge(
            "solana_total_delegated_stake",
            "Total stake delegated to the validator",
            registry=self.registry,
        )
        self.delinquent_stake = Gauge(
            "solana_delinquent_stake", "Stake that is delinquent", registry=self.registry
        )
        self.pending_stake = Gauge(
            "solana_pending_stake",
            "Stake that is delegated but not active yet",
            registry=self.registry,
        )
        self.missed_slots = Gauge(
            "solana_missed_slots",
            "Number of slots missed by the validator",
            registry=self.registry,
        )
        self.leader_status = Gauge(
            "solana_leader_status", "Leader status (1 or 0)", registry=self.registry
        )
        self.vote_distance = Gauge(
            "solana_vote_distance",
            "Vote distance from the highest known slot",
            registry=self.registry,
        )
        self.block_production_success = Gauge(
            "solana_block_production_success",
            "Block production status (1 for success, 0 for failure)",
            registry=self.registry,
        )

        self.programAccountsCallCounter: int = -1
        self.stake_accounts: List[JsonRPCResponse] = []
        self.last_absolute_slot = None
        self.last_timestamp = None

    def collect_metrics(self):
        """Collect metrics using a batched RPC call."""
        self.programAccountsCallCounter += 1
        if self.programAccountsCallCounter % 5 != 0:
            self.stake_accounts = self._get_stake_accounts()
            self.programAccountsCallCounter = 0

        rpc_requests: List[JsonRPCRequest] = [
            JsonRPCRequest("getSlot", params=[]),  # For slot_number
            JsonRPCRequest("getBalance", params=[self.config.validator_pubkey]),  # For balance
            JsonRPCRequest("getVoteAccounts", params=[]),  # For vote and delinquent metrics
            JsonRPCRequest("getEpochInfo", params=[]),  # For epoch and slot time
            JsonRPCRequest("getLeaderSchedule", params=[]),  # For leader status
            JsonRPCRequest("getBlockProduction", params=[]),  # For block production
            JsonRPCRequest("getHealth", params=[]),  # For health status
        ]

        responses: List[JsonRPCResponse] = self._batched_rpc_call(rpc_requests)
        if not responses:
            self.logger.warning("No responses received from batched RPC call.")
            return

        for idx, response in enumerate(iterable=responses):
            if response.error:
                self.logger.error(
                    f"Error in RPC response for method {rpc_requests[idx].method}: {response.error}"
                )
                continue
            result = response.result

            if idx == 0:  # getSlot
                self._update_slot_metrics(current_slot=result)

            elif idx == 1:  # getBalance
                balance = result.get("value", 0) / 1_000_000_000
                self.balance.set(balance)
                self.logger.debug(f"Updated balance: {balance}")

            elif idx == 2:  # getVoteAccounts
                self._update_stake_metrics(vote_accounts=result)

            elif idx == 3:  # getEpochInfo
                self._update_epoch_metrics(epoch_info=result)

            elif idx == 4:  # getLeaderSchedule
                is_leader: bool = self.config.vote_pubkey in result
                self.leader_status.set(1 if is_leader else 0)
                self.logger.debug(f"Updated leader status: {1 if is_leader else 0}")

            elif idx == 5:  # getBlockProduction
                self._update_block_production_metrics(block_production_data=result)

            elif idx == 6:  # getHealth
                health: Literal[1] | Literal[0] = 1 if result == "ok" else 0
                self.health_status.set(value=health)
                self.logger.debug(msg=f"Updated health status: {health}")

    def _update_slot_metrics(self, current_slot):
        """Update slot-related metrics."""
        self.slot_number.set(current_slot)

        # Compute sync status based on the difference between current slot and the network's highest slot.
        last_slot_difference = (
            abs(current_slot - self.last_absolute_slot) if self.last_absolute_slot else 0
        )
        self.slot_lag.set(last_slot_difference)
        self.sync_status.set(
            1 if last_slot_difference <= 64 else 0
        )  # Node is synced if slot difference is <= 64

        self.logger.debug(
            f"Updated slot number: {current_slot}, sync status: {1 if last_slot_difference <= 64 else 0}"
        )

    def _get_stake_accounts(self) -> List[JsonRPCResponse]:
        """Query stake accounts using the public RPC endpoint."""

        program_id = "Stake11111111111111111111111111111111111111"
        filters = [
            {"dataSize": 200},
            {
                "memcmp": {
                    "offset": 124,
                    "bytes": self.config.vote_pubkey,
                }
            },
        ]
        request = JsonRPCRequest(
            method="getProgramAccounts",
            params=[
                program_id,
                {"filters": filters, "encoding": "base64"},
            ],
        )

        responses: List[JsonRPCResponse] = JsonRPCRequest.send(
            rpc_url=self.public_rpc_url, rpc_requests=request, logger=self.logger
        )

        accounts = []
        for response in responses:
            if response.is_valid():
                accounts.append(response)

        if not accounts:
            self.logger.error("Failed to fetch stake accounts: All responses failed")
        else:
            self.logger.debug(f"Retrieved stake accounts: {accounts}")
        return accounts

    def _update_stake_metrics(self, vote_accounts) -> None:
        """Update stake-related metrics."""
        current_accounts = vote_accounts.get("current", [])
        delinquent_accounts = vote_accounts.get("delinquent", [])

        # Total delegated stake for this validator
        total_stake = sum(
            account.get("activatedStake", 0) / 1_000_000_000
            for account in current_accounts
            if account["votePubkey"] == self.config.vote_pubkey
        )
        self.total_delegated_stake.set(total_stake)

        # Delinquent stake across all validators
        total_delinquent_stake = sum(
            account.get("activatedStake", 0) / 1_000_000_000 for account in delinquent_accounts
        )
        self.delinquent_stake.set(total_delinquent_stake)

        if self.stake_accounts != [] and self.stake_accounts[0].result is not None:
            # Pending stake calculation from stake accounts
            total_delegations = sum(
                stake_account.get("account", {}).get("lamports", 0) / 1_000_000_000
                for stake_account in self.stake_accounts[0].result
            )
            activating_stake = max(0, total_delegations - total_stake)
            self.pending_stake.set(activating_stake)
            self.logger.debug(f"Updated pending stake (activating): {activating_stake}")

        # Log metrics for debugging
        self.logger.debug(f"Updated total delegated stake: {total_stake}")
        self.logger.debug(f"Updated delinquent stake: {total_delinquent_stake}")

    def _update_epoch_metrics(self, epoch_info):
        """Update metrics related to epoch and slot time."""
        current_absolute_slot = epoch_info.get("absoluteSlot", 0)
        current_timestamp = time.time()

        self.epoch.set(epoch_info.get("epoch", 0))

        if self.last_absolute_slot is not None and self.last_timestamp is not None:
            elapsed_time = current_timestamp - self.last_timestamp
            slots_processed = current_absolute_slot - self.last_absolute_slot

            if elapsed_time > 0:
                slots_per_second = slots_processed / elapsed_time
                self.slot_time.set(1 / slots_per_second)
                self.logger.debug(
                    f"Updated slot time: {1 / slots_per_second}, slots_per_second: {slots_per_second}"
                )
            else:
                self.logger.warning("Elapsed time is zero, cannot calculate slots per second")

        self.last_absolute_slot = current_absolute_slot
        self.absolute_slot_number.set(self.last_absolute_slot)
        self.last_timestamp = current_timestamp

    def _update_block_production_metrics(self, block_production_data):
        """Update block production metrics."""
        production_stats = (
            block_production_data.get("value", {})
            .get("byIdentity", {})
            .get(self.config.validator_pubkey, [])
        )
        block_success = 1 if production_stats and production_stats[1] > 0 else 0
        self.block_production_success.set(block_success)
        self.logger.debug(f"Updated block production success: {block_success}")


if __name__ == "__main__":
    configFile: str | None = os.getenv("EXPORTER_ENV")
    print(f"starting solana exporter -- config {configFile}")
    exporter = SolanaExporter(config_source="fromFile", config_file=configFile)
    exporter.start_exporter()
