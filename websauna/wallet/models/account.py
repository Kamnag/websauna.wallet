"""Core accounting primitivtes."""
import datetime
from decimal import Decimal
from typing import Tuple, Optional
import enum

import sqlalchemy
from sqlalchemy import Enum
from sqlalchemy import LargeBinary
from sqlalchemy import UniqueConstraint
from sqlalchemy import Column, Integer, Numeric, ForeignKey, func, String
import sqlalchemy.dialects.postgresql as psql
from sqlalchemy.orm import relationship, backref, Session
from sqlalchemy.dialects.postgresql import UUID

from slugify import slugify
from websauna.system.model.columns import UTCDateTime
from websauna.system.model.json import NestedMutationDict
from websauna.system.user.models import User
from websauna.utils.time import now
from websauna.system.model.meta import Base


class AssetState(enum.Enum):
    """What kind of global visibility asset has in the system."""

    #: Asset information and ownership is publicly known
    public = "public"

    #: Only who own asset have access to the information
    shared = "shared"

    #: Only asset owner sees the information
    owner = "owner"

    #: Asset is frozen. All withdraws from accounts are blocked.
    frozen = "frozen"


class AssetNetwork(Base):
    __tablename__ = "asset_network"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)

    #: Internal string identifier for this network

    name = Column(String(256), nullable=False)

    #: All assets listed in this network
    assets = relationship("Asset", lazy="dynamic", back_populates="network")

    #: Bag of random things one can assign to this network
    #: * house_address
    #: * initial_assets.toybox
    #: * initial_assets.eth_amount
    other_data = Column(NestedMutationDict.as_mutable(psql.JSONB), default=dict)

    def __str__(self):
        return "<Network {} {}>".format(self.name, self.id)

    def __repr__(self):
        return self.__str__()

    @property
    def human_friendly_name(self):
        name = self.other_data.get("human_friendly_name")
        if name:
            return name
        return self.name.title()

    def create_asset(self, name: str, symbol: str, supply: Decimal, asset_class: "AssetClass") -> "Asset":
        """Instiate the asset."""
        assert isinstance(supply, Decimal)
        dbsession = Session.object_session(self)
        asset = Asset(name=name, symbol=symbol, asset_class=asset_class, supply=supply)
        self.assets.append(asset)
        dbsession.flush()
        return asset

    def get_asset(self, id: UUID) -> "Asset":
        """Get asset by id within this network."""
        return self.assets.filter_by(id=id).one_or_none()

    def get_asset_by_symbol(self, symbol: str) -> "Asset":
        """Get asset by id within this network."""
        return self.assets.filter_by(symbol=symbol).one_or_none()

    def get_asset_by_name(self, name: str) -> "Asset":
        """Get asset by id within this network."""
        return self.assets.filter_by(name=name).one_or_none()

    def get_or_create_asset_by_name(self, name: str, symbol=None, supply=0, asset_class=None) -> Tuple["Asset", bool]:
        """Get asset by id within this network.

        :return. tuple(asset object, created flag)
        """

        asset = self.get_asset_by_name(name)
        if not asset:
            asset = Asset(name=name)
            asset.symbol = symbol
            asset.supply = supply
            asset.asset_class = asset_class or AssetClass.token
            self.assets.append(asset)
            return asset, True

        return asset, False


class AssetClass(enum.Enum):
    """What's preferred display format for this asset."""

    #: 0,00
    fiat = "fiat"

    #: 100.000,000,000
    cryptocurrency = "cryptocurrency"

    #: 10.000
    token = "token"

    #: 10.000
    tokenized_shares = "tokenized_shares"

    #: up to 18 decimals
    ether = "ether"


class AssetFrozen(Exception):
    """A frozen asset was transferred."""


class Asset(Base):

    __tablename__ = "asset"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)

    #: When this was created
    created_at = Column(UTCDateTime, default=now, nullable=False)

    #: When this data was updated last time
    updated_at = Column(UTCDateTime, onupdate=now)

    network_id = Column(ForeignKey("asset_network.id"), nullable=False)
    network  = relationship("AssetNetwork", uselist=False, back_populates="assets")

    #: Human readable name of this asset. Cannot be unique, because there can be several independent token contracts with the same asset name.
    name = Column(String(256), nullable=True, default=None, unique=False)

    #: One line plain text description of the asset
    description = Column(String(256), nullable=True, default=None, unique=False)

    #: Stock like symbol of the asset.
    symbol = Column(String(32), nullable=True, default=None, unique=False)

    #: The id of the asset in its native network
    external_id = Column(LargeBinary(32), nullable=True, default=None, unique=True)

    #: Total amount os assets in the distribution
    supply = Column(Numeric(60, 20), nullable=True)

    #: What kind of asset
    #  is this
    asset_class = Column(Enum(AssetClass), nullable=False)

    #: How asset should be treated
    state = Column(Enum(AssetState), nullable=False, default=AssetState.public)

    #: Misc parameters we can set
    other_data = Column(NestedMutationDict.as_mutable(psql.JSONB), default=dict)

    __table_args__ = (
        UniqueConstraint('network_id', 'symbol', name='symbol_per_network'),
        UniqueConstraint('network_id', 'name', name='name_per_network'),
        UniqueConstraint('network_id', 'external_id', name='contract_per_network'),
    )

    def __str__(self):
        return "Asset:{} in network:{}".format(self.symbol, self.network.name)

    def __repr__(self):
        return self.__str__()

    def get_local_liabilities(self):
        """Get sum how much assets we are holding on all of our accounts."""
        dbsession = Session.object_session(self)
        asset_total = dbsession.query(func.sum(Account.denormalized_balance)).join(Asset).scalar()
        return asset_total

    def ensure_not_frozen(self):
        """Is the transfer of this asset blocked.

        :raise: :class:`AssetFrozen` if the current state is frozen
        """

        if self.state == AssetState.frozen:
            raise AssetFrozen("Asset is frozen: {}".format(self))

    @property
    def long_description(self):
        """Optional long description (Markdown)."""
        return self.other_data.get("long_description")

    @property
    def slug(self):
        """Automatic or persistent slug for URLs."""
        slug = self.other_data.get("slug")
        if slug:
            return slug
        return slugify(self.name)

    @property
    def archived_at(self) -> datetime.datetime:
        """Is this asset set to archived status"""
        return self.other_data.get("archived_at", None)

    @archived_at.setter
    def archived_at(self, dt):
        self.other_data["archived_at"] = dt.isoformat() if dt else None

    def is_publicly_listed(self) -> bool:
        """Asset should not appear in the public listings.

        Asset can be still accessible via direct link, etc.
        """
        return self.state == AssetState.public and not self.archived_at


class IncompatibleAssets(Exception):
    """Transfer between accounts of different assets."""


class AccountOverdrawn(Exception):
    """Tried to send more than account has."""


class Account(Base):
    """Internal credit/debit account.

    Accounts can be associated with user, escrow, etc. They offer simple but robust account-to-account transfer mechanisms.s
    """
    __tablename__ = "account"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)

    #: When this was created
    created_at = Column(UTCDateTime, default=now, nullable=False)

    #: When this data was updated last time
    updated_at = Column(UTCDateTime, onupdate=now)

    #: Asset this account is holding
    asset_id = Column(ForeignKey("asset.id"), nullable=False)
    asset = relationship(Asset, backref=backref("accounts", uselist=True, lazy="dynamic"))

    #: Hold cached balance that is sum of all transactions
    denormalized_balance = Column(Numeric(60, 20), nullable=False, server_default='0')

    def __str__(self):
        return "<Acc:{} asset:{} bal:{}>".format(self.id, self.asset.symbol, self.get_balance())

    def __repr__(self):
        return self.__str__()

    def get_balance(self):
        # denormalized balance can be non-zero until the account is created
        return self.denormalized_balance or Decimal(0)

    def update_balance(self) -> Decimal:
        assert self.id
        dbsession = Session.object_session(self)
        results = dbsession.query(func.sum(AccountTransaction.amount.label("sum"))).filter(AccountTransaction.account_id == self.id).all()
        self.denormalized_balance = results[0][0] if results else Decimal(0)

    def do_withdraw_or_deposit(self, amount: Decimal, note: str, allow_negative: bool=False) -> "AccountTransaction":
        """Do a top up operation on account.

        This operation does not have matching credit/debit transaction on any account. It's main purpose is to initialize accounts with certain balance.

        :param amount: How much
        :param note: Human readable
        :param allow_negative: Set true to create negative balances or allow overdraw.
        :raise AccountOverdrawn: If the account is overdrawn
        :return: Created AccountTransaction
        """

        assert self.id
        assert isinstance(amount, Decimal)

        if note:
            assert isinstance(note, str)

        # Confirm we are not withdrawing frozen asset
        if amount > 0:
            self.asset.ensure_not_frozen()

        if not allow_negative:
            if amount < 0 and self.get_balance() < abs(amount):
                raise AccountOverdrawn("Cannot withdraw more than you have on the account")

        DBSession = Session.object_session(self)
        t = AccountTransaction(account=self)
        t.amount = Decimal(amount)
        t.message = note
        DBSession.add(t)

        self.update_balance()

        return t

    @classmethod
    def transfer(cls, amount: Decimal, from_: "Account", to: "Account", note: Optional[str]=None) -> Tuple["AccountTransaction", "AccountTransaction"]:
        """Transfer asset between accounts.

        This creates two transactions

        - Debit on from account

        - Credit on to account

        - Transaction counterparty fields point each other

        :return: tuple(withdraw transaction, deposit transaction)
        """
        DBSession = Session.object_session(from_)

        if from_.asset != to.asset:
            raise IncompatibleAssets("Tried to transfer between {} and {}".format(from_, to))

        from_.asset.ensure_not_frozen()

        withdraw = from_.do_withdraw_or_deposit(-amount, note)
        deposit = to.do_withdraw_or_deposit(amount, note)

        DBSession.flush()

        deposit.counterparty = withdraw
        withdraw.counterparty = deposit

        return withdraw, deposit


class AccountTransaction(Base):
    """Instant transaction between accounts."""

    __tablename__ = "account_transaction"
    id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)

    #: When this was created
    created_at = Column(UTCDateTime, default=now, nullable=False)

    #: When this data was updated last time
    updated_at = Column(UTCDateTime, onupdate=now, nullable=True)

    account_id = Column(ForeignKey("account.id"))
    account = relationship(Account,
                           primaryjoin=account_id == Account.id,
                           backref=backref("transactions",
                                            lazy="dynamic",
                                            cascade="all, delete-orphan",
                                            single_parent=True,
                                            ))

    amount = Column(Numeric(60, 20), nullable=False, server_default='0')
    message = Column(String(256))

    counterparty_id = Column(ForeignKey("account_transaction.id"))
    counterparty = relationship("AccountTransaction", primaryjoin=counterparty_id == id, uselist=False, post_update=True)

    def __str__(self):
        counter_account = self.counterparty.account if self.counterparty else "-"
        return "<ATX{} ${} FROM:{} TO:{} {}>".format(self.id, self.amount, self.account, counter_account, self.message)

    def __repr__(self):
        return self.__str__()

    def __json__(self, request):
        return dict(id=str(self.id), amount=float(self.amount), message=self.message)

    def reverse(self) -> "AccountTransaction":
        """Moves the funds back to the sending account."""
        counter_account = self.counterparty.account
        note = "Transaction {} reversed".format(self.id)
        return Account.transfer(self.amount, self.account, counter_account, note)


class UserOwnedAccount(Base):
    """An account belonging to a some user."""

    __tablename__ = "user_owned_account"

    id = Column(UUID(as_uuid=True), primary_key=True, server_default=sqlalchemy.text("uuid_generate_v4()"),)

    account_id = Column(ForeignKey("account.id"))
    account = relationship(Account,
                           single_parent=True,
                           cascade="all, delete-orphan",
                           primaryjoin=account_id == Account.id,
                           backref="user_owned_accounts")

    user_id = Column(ForeignKey("users.id"), nullable=False)
    user = relationship(User,
                        backref=backref("owned_accounts",
                                        lazy="dynamic",
                                        cascade="all, delete-orphan",
                                        single_parent=True,),
                        uselist=False)

    name = Column(String(256), nullable=True)

    @classmethod
    def create_for_user(cls, user, asset):
        dbsession = Session.object_session(user)
        account = Account(asset=asset)
        dbsession.flush()
        uoa = UserOwnedAccount(user=user, account=account)
        return uoa

    @classmethod
    def get_or_create_user_default_account(cls, user, asset: Asset) -> Tuple["UserOwnedAccount", bool]:
        dbsession = Session.object_session(user)
        account = user.owned_accounts.join(Account).filter(Account.asset == asset).first()

        # We already have an account for this asset
        if account:
            return account, False
        dbsession.flush()

        # TODO: Why cannot use relationship here
        account = Account(asset_id=asset.id)  # Create account
        dbsession.add(account)
        dbsession.flush()
        uoa = UserOwnedAccount(user=user, account=account)  # Assign it to a user
        dbsession.flush()  # Give id to UserOwnedAccount
        return uoa, True

