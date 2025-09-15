import os
import time
from typing import List, Literal, Optional

from exporter.jsonRPCRequest import JsonRPCRequest
from exporter.jsonRPCResponse import JsonRPCResponse
from exporter.rpcExporter import RPCExporter
from prometheus_client import Gauge, Info


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
        self.balance = Gauge("solana_account_balance", "Validator's account balance", registry=self.registry)
        self.health_status = Gauge("solana_health_status", "Health status of the Solana node", registry=self.registry)
        self.total_delegated_stake = Gauge(
            "solana_total_delegated_stake",
            "Total stake delegated to the validator",
            registry=self.registry,
        )
        self.delinquent_stake = Gauge("solana_delinquent_stake", "Stake that is delinquent", registry=self.registry)
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
        self.leader_status = Gauge("solana_leader_status", "Leader status (1 or 0)", registry=self.registry)
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
        self.credits_earned = Gauge(
            "solana_credits_earned",
            "Total vote credits earned by the validator",
            registry=self.registry,
        )
        self.build_info = Info(
            "solana_build",
            "Build information including version and instance label",
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
            JsonRPCRequest("getSlot"),
            JsonRPCRequest("getBalance", params=[self.config.validator_pubkey]),
            JsonRPCRequest("getVoteAccounts", params=[{"votePubkey": self.config.vote_pubkey}]),
            JsonRPCRequest("getEpochInfo"),
            JsonRPCRequest("getLeaderSchedule"),
            JsonRPCRequest("getBlockProduction"),
            JsonRPCRequest("getHealth"),
        ]

        responses: List[JsonRPCResponse] = self._batched_rpc_call(rpc_requests)
        if not responses or len(responses) != len(rpc_requests):
            self.logger.error(
                "RPC call failed or incomplete batch, setting health_status to 0 and other metrics to NaN"
            )
            self.health_status.set(0)
            self.sync_status.set(0)
            return

        vote_accounts_result = None
        epoch_info_result = None
        slot_value = None
        absolute_slot_value = None

        for idx, response in enumerate(iterable=responses):
            if response.error:
                self.logger.error(f"Error in RPC response for method {rpc_requests[idx].method}: {response.error}")
                if idx == 6:
                    self.health_status.set(0)
                    self.sync_status.set(0)
                continue
            result = response.result

            if idx == 0:  # getSlot
                slot_value = result
                self._update_slot_metrics(current_slot=result)
            elif idx == 1:  # getBalance
                balance = result.get("value", 0) / 1_000_000_000
                self.balance.set(balance)
                self.logger.debug(f"Updated balance: {balance}")
            elif idx == 2:  # getVoteAccounts
                self._update_stake_metrics(vote_accounts=result)
                self._update_credits_earned(result)
                vote_accounts_result = result
            elif idx == 3:  # getEpochInfo
                absolute_slot_value = result.get("absoluteSlot", 0)
                self._update_epoch_metrics(epoch_info=result)
                epoch_info_result = result
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

        # Calculate slot_lag and sync_status using values from the same probe
        if slot_value is not None and absolute_slot_value is not None:
            self._update_slot_lag_and_sync_status(slot_value, absolute_slot_value)

        self._update_vote_distance(vote_accounts_result, epoch_info_result)
        # update metrics from config file
        self._update_build_info()

    def _update_slot_lag_and_sync_status(self, slot_value, absolute_slot_value):
        """Update slot_lag and sync_status metrics using values from the same probe."""
        slot_lag = abs(slot_value - absolute_slot_value)
        self.slot_lag.set(slot_lag)
        self.sync_status.set(1 if slot_lag <= 64 else 0)
        self.logger.debug(f"Updated slot lag (same probe): {slot_lag}, sync status: {1 if slot_lag <= 64 else 0}")

    def _update_vote_distance(self, vote_accounts_result, epoch_info_result):
        """Update the vote distance metric."""
        if not vote_accounts_result or not epoch_info_result:
            return
        highest_vote = 0
        for account in vote_accounts_result.get("current", []):
            if account.get("votePubkey") == self.config.vote_pubkey:
                highest_vote = account.get("lastVote", 0)
                break
        highest_known_slot = epoch_info_result.get("absoluteSlot", 0)
        vote_distance = highest_known_slot - highest_vote if highest_vote else 0
        self.vote_distance.set(vote_distance)
        self.logger.debug(f"Updated vote distance: {vote_distance}")

    def _update_slot_metrics(self, current_slot):
        """Update slot-related metrics."""
        self.slot_number.set(current_slot)
        self.logger.debug(f"Updated slot number: {current_slot}")

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
        """Update stake-related metrics with full validation and fallback defaults."""
        # Safety: vote_accounts must be a valid dict
        if not isinstance(vote_accounts, dict):
            self.logger.warning("Invalid vote_accounts structure, resetting stake metrics to 0")
            self.total_delegated_stake.set(0)
            self.delinquent_stake.set(0)
            self.pending_stake.set(0)
            return

        current_accounts = vote_accounts.get("current", [])
        delinquent_accounts = vote_accounts.get("delinquent", [])

        # Validate current_accounts list
        if isinstance(current_accounts, list):
            total_stake = sum(
                account.get("activatedStake", 0) / 1_000_000_000
                for account in current_accounts
                if account.get("votePubkey") == self.config.vote_pubkey
            )
        else:
            total_stake = 0
            self.logger.warning("current_accounts missing or malformed — setting total delegated stake to 0")

        self.total_delegated_stake.set(total_stake)
        self.logger.debug(f"Updated total delegated stake: {total_stake}")

        # Validate delinquent_accounts list
        if isinstance(delinquent_accounts, list):
            total_delinquent_stake = sum(
                account.get("activatedStake", 0) / 1_000_000_000 for account in delinquent_accounts
            )
        else:
            total_delinquent_stake = 0
            self.logger.warning("delinquent_accounts missing or malformed — setting delinquent stake to 0")

        self.delinquent_stake.set(total_delinquent_stake)
        self.logger.debug(f"Updated delinquent stake: {total_delinquent_stake}")

        # Pending stake from stake_accounts (via getProgramAccounts)
        if self.stake_accounts and self.stake_accounts[0].result is not None:
            total_delegations = sum(
                stake_account.get("account", {}).get("lamports", 0) / 1_000_000_000
                for stake_account in self.stake_accounts[0].result
            )
            activating_stake = max(0, total_delegations - total_stake)
            self.pending_stake.set(activating_stake)
            self.logger.debug(f"Updated pending stake (activating): {activating_stake}")
        else:
            self.pending_stake.set(0)
            self.logger.debug("Stake accounts missing or empty — setting pending stake to 0")

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
                self.logger.debug(f"Updated slot time: {1 / slots_per_second}, slots_per_second: {slots_per_second}")
            else:
                self.logger.warning("Elapsed time is zero, cannot calculate slots per second")

        self.last_absolute_slot = current_absolute_slot
        self.absolute_slot_number.set(self.last_absolute_slot)
        self.last_timestamp = current_timestamp

    def _update_block_production_metrics(self, block_production_data):
        """Update block production metrics."""
        production_stats = (
            block_production_data.get("value", {}).get("byIdentity", {}).get(self.config.validator_pubkey, [])
        )
        if production_stats and len(production_stats) == 2:
            leader_slots = production_stats[0]
            blocks_produced = production_stats[1]
            missed_slots = leader_slots - blocks_produced
            self.missed_slots.set(missed_slots)
            self.logger.debug(f"Updated missed slots: {missed_slots}")
            block_success = 1 if blocks_produced > 0 else 0
            self.block_production_success.set(block_success)
            self.logger.debug(f"Updated block production success: {block_success}")
        else:
            self.missed_slots.set(0)
            self.block_production_success.set(0)
            self.logger.warning("Could not update missed slots: block production stats missing or malformed")

    def _update_credits_earned(self, vote_accounts_result) -> None:
        """Update the credits_earned metric."""
        credits = 0
        for account in vote_accounts_result.get("current", []):
            if account.get("votePubkey") == self.config.vote_pubkey:
                epoch_credits = account.get("epochCredits", [])
                credits = sum(ec[1] - ec[2] for ec in epoch_credits if len(ec) == 3)
                break
        self.credits_earned.set(credits)
        self.logger.debug(f"Updated credits earned: {credits}")

    def _update_build_info(self) -> None:
        """Update build information with version and label as string values."""
        build_info_data = {"version": str(self.config.version), "label": str(self.config.label)}
        self.build_info.info(build_info_data)
        self.logger.debug(f"Updated build info: {build_info_data}")


if __name__ == "__main__":
    configFile: str | None = os.getenv("EXPORTER_ENV")
    print(f"starting solana exporter -- config {configFile}")
    exporter = SolanaExporter(config_source="fromFile", config_file=configFile)
    exporter.start_exporter()
