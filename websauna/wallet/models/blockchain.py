"""Accounting primitives for blockchain operations."""

from decimal import Decimal
import enum

import sqlalchemy
from sqlalchemy import func
from sqlalchemy import Enum
from sqlalchemy import Column, Integer, Numeric, ForeignKey, func, String, LargeBinary
from sqlalchemy import CheckConstraint
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship, backref, Session
from sqlalchemy.dialects.postgresql import UUID
from websauna.system.model.columns import UTCDateTime
from websauna.system.user.models import User
from websauna.utils.time import now
from websauna.system.model.meta import Base
from websauna.wallet.utils import ensure_positive

from .account import Account
from .account import AssetNetwork
from .account import AssetFormat
from .account import Asset


class CryptoOperationType(enum.Enum):

    address = "address"
    create_address = "create_address"
    withdraw = "withdraw"
    deposit = "deposit"

    transaction = "transaction"

class CryptoOperationState(enum.Enum):
    """Different crypto operations."""

    #: Operation is created by web process and it's waiting to be picked up the service daemon
    waiting = "waiting"

    #: The operation was success
    success = "success"

    #: The operation failed after max retry attempts
    failed = "failed"

    #: The operation was created by the service daemon itself e.g. in the case of incoming funds. This should never fail as these operations cannot be retried as they only write to database and do not communicate with external services.
    immediate = "immediate"


# #
# # class ExternalTransactionOperation(CryptoOperation):
#     """Operation which has one cryptonetwork address."""
#
#     __abstract__ = True
#
#     #: Reverse operation had been generated due to failure
#     external_transaction_id = Column(ForeignKey("external_transaction.id"))
#     external_transaction = relationship("ExternalTransaction", uselist=False, post_update=True)
#
#
# class Deposit(ExternalTransactionOperation):
#
#     __tablename__ = "crypto_operation_deposit"
#     id = Column(UUID(as_uuid=True), ForeignKey('crypto_operation.id'), primary_key=True)
#
#     __mapper_args__ = {
#         'polymorphic_identity': OperationType.deposit,
#     }
#
# class Withdraw(ExternalTransactionOperation):
#
#     __tablename__ = "crypto_operation_withdraw"
#     id = Column(UUID(as_uuid=True), ForeignKey('crypto_operation.id'), primary_key=True)
#
#     __mapper_args__ = {
#         'polymorphic_identity': OperationType.withdraw,
#     }
#
#
# class ExternalTransaction:
#     """Cached state of raw blockchain transaction."""
#     __tablename__ = "external_transaction"
#     id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)
#     txid = Column(String(256), nullable=True)
#
#     network_id = Column(ForeignKey("assetnetwork.id"), nullable=False)
#     network  = relationship(User, primaryjoin=network_id == AssetNetwork.id, backref=backref("assets", uselist=False))
#

class CryptoAddress(Base):
    """Crypto account is an Ethereum account and Bitcoin address.

    It holds multiple different :class:`CryptoAddressAccount` for different asset types.

    It's target for external crypto operations.

    We only register addresses where private keys are owned by our system.

    Crypto account is only updated by a separate service and all web process write communications with this accout must go through :py:class:`CryptoOperation` async pipeline.
    """

    __tablename__ = "crypto_address"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)

    #: Native presentation of account / address. Hex string format for Ethereum.
    address = Column(LargeBinary(length=20), nullable=True)

    # Network where this operation happens
    network_id = Column(ForeignKey("asset_network.id"), nullable=False)
    network = relationship("AssetNetwork", uselist=False, backref="addresses")

    def create_account(self, asset: Asset) -> "CryptoAddressAccount":
        """Create an account holding certain asset under this address."""

        # Check validity of this object
        assert self.id
        assert asset
        assert asset.id
        assert self.address

        dbsession = Session.object_session(self)
        account = Account(asset=asset)
        dbsession.flush()

        ca_account = CryptoAddressAccount(account=account)
        ca_account.address = self
        dbsession.flush()
        # self.crypto_address_accounts.append(account)

        return ca_account


class CryptoAddressAccount(Base):
    """Hold balances of crypto currency, token or other asset in address.

    This is primarily used to model user holdings in their web wallet. You have one CryptoAddressAccount for ETH, one for each held token.
    """

    __tablename__ = "crypto_address_account"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)

    account_id = Column(ForeignKey("account.id"), nullable=False)
    account = relationship(Account,
                           uselist=False,
                           backref=backref("crypto_address_accounts",
                                        lazy="dynamic",
                                        cascade="all, delete-orphan",
                                        single_parent=True,),)

    address_id = Column(ForeignKey("crypto_address.id"), nullable=False)
    address = relationship(CryptoAddress,
                           uselist=False,
                           backref=backref("crypto_address_accounts",
                                        lazy="dynamic",
                                        cascade="all, delete-orphan",
                                        single_parent=True,),)

    def __init__(self, account: Account):
        assert account
        assert account.id
        assert account.asset
        assert account.asset.id
        super().__init__(account=account)

    def withdraw(self, amount: Decimal, to_address: bytes, note: str) -> "CryptoAddressWithdraw":
        """Initiates the withdraw operation.

        :to_address: External address in binary format where we withdraw

        """

        assert to_address
        assert self.id
        assert self.account
        assert self.account.id
        assert isinstance(amount, Decimal)
        assert isinstance(note, str)

        ensure_positive(amount)

        network = self.account.asset.network
        assert network.id

        op = CryptoAddressWithdraw(network=network)
        op.crypto_account  = self
        op.holding_account = Account(asset=self.account.asset)
        dbsession = Session.object_session(self)
        dbsession.add(op)
        dbsession.flush()  # Give ids

        # Lock assetes in transfer to this object
        Account.transfer(amount, self.account, op.holding_account, note)

        return op

    def get_operations(self):
        """List all crypto operations (deposit, withdraw, account creation) related to this account.

        This limits to operations of asset type on this account.
        """
        dbsession = Session.object_session(self)
        return dbsession.query(CryptoTransactionOperation)


class CryptoOperation(Base):
    """External network operation.

    These operations are not run immediately, but queued to run by a service daemon asynchronously (due to async nature of blockchain). Even if operations complete they can be later shuflfled around e.g. due to blockchain fork.

    We use SQLAlchemy single table inheritance model here: http://docs.sqlalchemy.org/en/latest/orm/inheritance.html#single-table-inheritance
    """

    __tablename__ = "crypto_operation"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)

    # Network where this operation happens
    network_id = Column(ForeignKey("asset_network.id"), nullable=False)
    network = relationship("AssetNetwork", uselist=False, backref="operations")

    #: Polymorphic column
    operation_type = Column(Enum(CryptoOperationType), nullable=False)

    #: When this was created
    created_at = Column(UTCDateTime, default=now, nullable=False)

    #: When this data was updated last time
    updated_at = Column(UTCDateTime, onupdate=now)

    #: When this operations was last time attempted to be broadcasted to network.
    #: If the connection to a node is down the operation will be attempted to be rescheduled later.
    attempted_at = Column(UTCDateTime, default=None, nullable=True)
    attempts = Column(Integer, default=0, nullable=False)

    #: When we are created we start in waiting state.
    #: It's up to service daemon to complete the operation and update the state field.
    state = Column(Enum(CryptoOperationState, name="operation_state"), nullable=False, default='waiting')

    # When this operation was completed according to network (when included in block)
    completed_at = Column(UTCDateTime, default=None, nullable=True)

    #: External network transaction id for this column
    txid = Column(LargeBinary(length=32), nullable=True)

    #: Related crypto account
    crypto_account_id = Column(ForeignKey("crypto_address_account.id"), nullable=True)
    crypto_account = relationship(CryptoAddressAccount,
                       uselist=False,
                       backref=backref("crypto_address_transaction_operations",
                                    lazy="dynamic",
                                    cascade="all, delete-orphan",
                                    single_parent=True,),)

    __mapper_args__ = {
        'polymorphic_on': operation_type,
    }

    def __init__(self, network, **kwargs):
        assert network
        assert network.id
        super().__init__(network=network, **kwargs)


class CryptoAddressOperation(CryptoOperation):
    """Operation which has one cryptonetwork address as source/destination."""

    address_id = Column(ForeignKey("crypto_address.id"))
    address = relationship(CryptoAddress,
                           single_parent=True,
                           cascade="all, delete-orphan",
                           primaryjoin=address_id == CryptoAddress.id,
                           backref="user_owned_crypto_accounts")

    __mapper_args__ = {
        'polymorphic_identity': CryptoOperationType.address,
    }

    def __init__(self, address: CryptoAddress):
        assert address
        assert address.id
        assert address.network
        assert address.network.id
        super().__init__(network=address.network)
        self.address = address


class CryptoAddressCreation(CryptoAddressOperation):
    """Create a receiving address.

    Start with null address and store the created address on this SQL row when the node creates a receiving address and has private keys stored within nodes internal storage.
    """

    __mapper_args__ = {
        'polymorphic_identity': CryptoOperationType.create_address,
    }

    class MultipleCreationOperations(Exception):
        pass

    def __init__(self, address: CryptoAddress):

        #: TODO: This is application side check that we don't attempt to create wallet side address for an crypto address account twice. E.g. we don't put to creation operations in the pipeline.
        dbsession = Session.object_session(address)
        existing = dbsession.query(CryptoAddressCreation).filter_by(address=address).one_or_none()
        if existing:
            raise CryptoAddressCreation.MultipleCreationOperations("Cannot create address for account twice: {}".format(address))

        super(CryptoAddressCreation, self).__init__(address=address)


class CryptoAddressDeposit(CryptoOperation):
    """Create a receiving address.

    Start with null address and store the created address on this SQL row when the node creates a receiving address and has private keys stored within nodes internal storage.
    """

    __mapper_args__ = {
        'polymorphic_identity': CryptoOperationType.deposit,
    }


class CryptoAddressWithdraw(CryptoOperation):
    """Withdraw assets under user address.

    - Move assets from the source account to a temporary holding account

    - Try broadcast the tx to the network on the next network tick
    """

    #: Holds the tokens until the operation is transacted to the network.
    holding_account_id = Column(ForeignKey("account.id"), nullable=True)
    holding_account = relationship(Account,
                           uselist=False,
                           backref=backref("crypto_withdraw_operation_holding_accounts",
                                        lazy="dynamic",
                                        cascade="all, delete-orphan",
                                        single_parent=True,),)

    __mapper_args__ = {
        'polymorphic_identity': CryptoOperationType.withdraw,
    }


class UserCryptoAddress(object):
    """An account belonging to a some user."""

    __tablename__ = "user_owned_crypto_address"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)

    address_account_id = Column(ForeignKey("crypto_address_account.id"))
    address_account_id = relationship(CryptoAddressAccount,
                           single_parent=True,
                           cascade="all, delete-orphan",
                           primaryjoin=address_account_id == CryptoAddressAccount.id,
                           backref="user_owned_crypto_addresses")

    user_id = Column(ForeignKey("users.id"), nullable=False)
    user = relationship(User,
                        backref=backref("owned_crypto_addresses",
                                        lazy="dynamic",
                                        cascade="all, delete-orphan",
                                        single_parent=True,),)

    @staticmethod
    def create_account(user: User):
        """Initiates account creation operation."""
        dbsession = Session.object_session(user)
        uca = UserCryptoAddress()
        user.owned_crypto_accounts.append(uca)

        dbsession.flush()

        # Put the creation operation in pipeline
        op = CryptoAddressCreation(address=uca.address)
        dbsession.add(op)