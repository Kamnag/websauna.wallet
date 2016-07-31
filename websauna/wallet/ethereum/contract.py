from typing import Optional, Tuple

from gevent import Timeout
from web3 import Web3
from web3.contract import Contract, construct_contract_class

from populus.utils.transactions import get_contract_address_from_txn, wait_for_transaction_receipt


class Contract(Contract):
    """Statically typing helper for contract class.

    May contain methods in the future.
    """
    pass



def deploy_contract(
        web3: Web3,
        contract_definition: dict,
        gas=1500000,
        timeout=60.0,
        constructor_arguments: Optional[list]=None,
        from_account=None) -> Tuple[Contract, str]:
    """Deploys a single contract using Web3 client.

    :param web3: Web3 client instance

    :param contract_definition: Dictionary of describing the contract interface,
        as read from ``contracts.json`` Contains

    :param gas: Max gas

    :param timeout: How many seconds to wait the transaction to
        confirm to get the contract address.

    :param constructor_arguments: Arguments passed to the smart contract
        constructor. Automatically encoded through ABI signature.

    :param from_account: Geth account that's balance is used for deployment.
        By default, the gas is spent from Web3 coinbase account. Account must be unlocked.

    :return: Tuple containing Contract proxy object and the transaction hash where it was deployed

    :raise gevent.timeout.Timeout: If we can't get our contract in a block within given timeout
    """

    # Check we are passed valid contract definition
    assert "abi" in contract_definition, \
        "Please pass a valid contract definition dictionary, got {}".format(contract_definition)

    contract_class = construct_contract_class(
        web3=web3,
        abi=contract_definition["abi"],
        code=contract_definition["code"],
        code_runtime=contract_definition["code_runtime"],
        source=contract_definition["source"],
            )

    if not from_account:
        from_account = web3.eth.coinbase

    # Set transaction parameters
    transaction = {
        "gas": gas,
        "from": from_account,
    }

    # Call web3 to deploy the contract
    txn_hash = contract_class.deploy(transaction, constructor_arguments)

    # Wait until we get confirmation and address
    address = get_contract_address_from_txn(web3, txn_hash, timeout=timeout)

    # Create Contract proxy object
    contract = contract_class(
        address=address,
        abi=contract_definition["abi"],
        code=contract_definition["code"],
        code_runtime=contract_definition["code_runtime"],
        source=contract_definition["source"])

    return contract, txn_hash


class TransactionConfirmationError(Exception):
    """A transaction was not correctly included in blockchain."""


def confirm_transaction(web3: Web3, txid: str, timeout=60) -> dict:
    """Make sure a transaction was correctly performed.

    Confirm that

    * The transaction has been mined in blockchain

    * The transaction did not throw an error (used up all its gas)

    http://ethereum.stackexchange.com/q/6007/620

    :raise TransactionConfirmationError: If we did not get it confirmed in time
    :return: Transaction receipt
    """

    try:
        receipt = wait_for_transaction_receipt(web3, txid, timeout)
    except Timeout as e:
        raise TransactionConfirmationError("Could not confirm tx {} within timeout {}".format(txid, timeout)) from e

    tx = web3.eth.getTransaction(txid)

    if tx["gas"] == receipt["gasUsed"]:
        raise TransactionConfirmationError("Transaction failed (out of gas, thrown): {}".format(txid))

    return receipt