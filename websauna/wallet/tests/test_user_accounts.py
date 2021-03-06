import pytest
import transaction
from decimal import Decimal

from websauna.tests.utils import create_user
from websauna.tests.webserver import customized_web_server
from websauna.wallet.models import AssetClass

from ..models import AssetNetwork
from ..models import UserOwnedAccount
from ..models import Asset


def test_user_account_top_up(dbsession, registry):

    with transaction.manager:

        network = AssetNetwork(name="Foo Bank")
        dbsession.add(network)
        dbsession.flush()

        asset = Asset(name="US Dollar", symbol="USD", asset_class=AssetClass.fiat)
        network.assets.append(asset)
        dbsession.flush()
        assert asset.id

        user = create_user(dbsession, registry)
        dbsession.flush()
        oa = UserOwnedAccount.create_for_user(user=user, asset=asset)
        dbsession.flush()

        oa.account.do_withdraw_or_deposit(Decimal("+100"), "Topping up")

