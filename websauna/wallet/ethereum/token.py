"""Tokenized asset support."""
from eth_ipc_client import Client

from populus.contracts import Contract, deploy_contract
from populus.contracts.core import ContractBase
from populus.utils import get_contract_address_from_txn
from websauna.wallet.ethereum.populuscontract import get_compiled_contract_cached



DEFAULT_TOKEN_CREATION_GAS = 1500000


class TokenCreationError(Exception):
    pass


def get_token_contract_class() -> type:
    name = "Token"
    contract_meta = get_compiled_contract_cached("token.sol", name)
    contract = Contract(contract_meta, name)
    return contract


class Token:
    """Proxy object for a deployed token contract

    Allows creation of new token contracts as well accessing existing ones.
    """

    def __init__(self, contract: ContractBase, version=0, initial_txid=None):
        """
        :param contract: Populus Contract object for underlying token contract
        :param version: What is the version of the deployed contract.
        :param initial_txid: Set on token creation to the txid that deployed the contract. Only available objects accessed through create().
        """

        # Make sure we are bound to an address
        assert contract._meta.address
        self.contract = contract

        self.version = version

        self.initial_txid = initial_txid

    @property
    def address(self) -> str:
        """Get wallet address as 0x hex string."""
        return self.contract._meta.address

    @property
    def client(self) -> Client:
        """Get access to RPC client we are using for this wallet."""
        return self.wallet_contract._meta.blockchain_client

    @classmethod
    def get(cls, rpc: Client, address: str, contract=get_token_contract_class()) -> "Token":
        """Get a proxy object to existing hosted token contrac.t"""

        assert address.startswith("0x")
        instance = contract(address, rpc)
        return Token(instance, rpc)

    @classmethod
    def create(cls, rpc: Client, name: str, symbol: str, supply: int, owner: str, wait_for_tx_seconds=90, gas=DEFAULT_TOKEN_CREATION_GAS, contract=get_token_contract_class()) -> "Token":
        """Creates a new token contract.

        The cost of deployment is paid from coinbase account.

        :param name: Asset name in contract

        :param symbol: Asset symbol in contract

        :param supply: How many tokens are created

        :param owner: Initial owner os the asset

        :param contract: Which contract we deploy as Populus Contract class.

        :return: Populus Contract proxy object for new contract
        """

        assert owner.startswith("0x")

        version = 2  # Hardcoded for now

        txid = deploy_contract(rpc, contract, gas=gas, constructor_args=[supply, name, 0, symbol, str(version), owner])

        if wait_for_tx_seconds:
            rpc.wait_for_transaction(txid, max_wait=wait_for_tx_seconds)
        else:
            # We cannot get contract address until the block is mined
            return (None, txid, version)

        try:
            contract_addr = get_contract_address_from_txn(rpc, txid)
        except ValueError:
            raise TokenCreationError("Could not create token with {} gas. Txid {}. Out of gas? Check in http://testnet.etherscan.io/tx/{}".format(DEFAULT_TOKEN_CREATION_GAS, txid, txid))

        instance = contract(contract_addr, rpc)
        return Token(instance, version=2, initial_txid=txid)



