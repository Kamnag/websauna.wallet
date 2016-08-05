"""
Setup initial parameters for running the demo.
"""
import transaction
from decimal import Decimal
from sqlalchemy.orm import Session

from websauna.system.http import Request
from websauna.wallet.ethereum.asset import get_eth_network, create_house_address, get_house_address, get_toy_box
from websauna.wallet.ethereum.confirm import finalize_pending_crypto_ops
from websauna.wallet.ethereum.ethjsonrpc import get_web3
from websauna.wallet.models import AssetClass
from websauna.wallet.models import Asset
from websauna.wallet.models import CryptoAddress
from websauna.wallet.models import AssetNetwork


def create_token(network: AssetNetwork, name: str, symbol: str, supply: int, initial_owner_address: CryptoAddress) -> Asset:
    asset = network.create_asset(name=name, symbol=symbol, supply=Decimal(supply), asset_class=AssetClass.token)
    op = initial_owner_address.create_token(asset)
    return asset


def setup_networks(request):
    """Setup different networks supported by the instance.

    Setup ETH giveaway on testnet and private testnet.
    """

    dbsession = request.dbsession
    for network in ["ethereum", "testnet", "private testnet"]:

        with transaction.manager:
            network = get_eth_network(dbsession, network)
            house_address = get_house_address(network)
            if not house_address:
                create_house_address(network)

            # Setup ETH give away
            if network in ("testnet", "private testnet"):
                network.other_data["initial_assets"] = {}
                network.other_data["initial_assets"]["eth_amount"] = "0.1"

    finalize_pending_crypto_ops(dbsession)


def setup_toybox(request):
    """Setup TOYBOX asset for testing."""
    dbsession = request.dbsessoin

    with transaction.manager:
        network = get_eth_network(dbsession, "testnet")
        toybox = get_toy_box(network)
        if toybox:
            return

        # Roll out toybox contract
        asset = create_token(network, "Toybox", "TOYBOX", 10222333, get_house_address(network))

        # setup toybox give away data for primary network
        network.other_data["initial_assets"]["toybox"] = str(asset.id)
        network.other_data["initial_assets"]["toybox_amount"] = 50

    finalize_pending_crypto_ops(dbsession)


def bootstrap(request: Request):
    """Setup environment for demo."""
    setup_networks(request)
    setup_toybox(request)
