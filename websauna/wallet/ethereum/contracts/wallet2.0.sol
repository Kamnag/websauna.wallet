/**
 * Alternative wallet code for testing upgrades, see version string.
 */
contract Wallet {

    // Withdraw events
    event Deposit(address from, uint value);
    event Withdraw(address to, uint value);
    event ExceededWithdraw(address to, uint value);
    event OutOfGasWithdraw(address to, uint value);

    // Smart contract call events
    event Execute(address to, uint value);
    event ExceededExecuteWithValue(address to, uint value);
    event FailedExecute(address to, uint value);
    event NoMatchingFunction();

    // Transaction fee settlement log keeping
    event ClaimFee(bytes32 txid, uint value);
    event ExceededClaimFee(bytes32 txid, uint value);

    // Who is the owner of this hosted wallet. This is the (coinbase) address or geth node
    // that your server speaks to via RPC
    address public owner;

    string public version = "2.0";

    function Wallet() {
        // Lock down the wallet, so that only our private geth
        // has the owner private key to speak to us
        owner = msg.sender;
    }

    /**
     * Simple withdrawal operation.
     */
    function withdraw(address _to, uint _value) external {
        bool success;

        if(msg.sender != owner) {
            throw;
        }

        if(_value > this.balance) {
            ExceededWithdraw(_to, _value);
            return;
        }

        // Gas is always deducted from the value
        // when the transaction is received on the other side.
        success = _to.send(_value);

        if(success) {
            Withdraw(_to, _value);
        } else {
            OutOfGasWithdraw(_to, _value);
        }
    }

    /**
     * Executes a transaction from this wallet.
     *
     * We call a function in another smart contract and for the gas use value stored on this contract.
     */
    function execute(address _to, uint _value, uint _gas, bytes _data) external {
        bool success;

        if(msg.sender != owner) {
            throw;
        }

        if(_value > this.balance) {
            ExceededExecuteWithValue(_to, _value);
            return;
        }

        // http://ethereum.stackexchange.com/a/2971/620
        if(_value > 0) {
            success = _to.call.value(_value)(_data);
        } else {
            success = _to.call(_data);
        }

        if(success) {
            Execute(_to, _value);
        } else {
            FailedExecute(_to, _value);
        }
    }

    /**
     * Claim transaction fees from the previous execute().
     */
    function claimFees(bytes32 txid, uint _value) {
        bool success;

        if(msg.sender != owner) {
            throw;
        }

        if(_value > this.balance) {
            ExceededClaimFee(txid, _value);
            return;
        }

        success = owner.send(_value);

        if(success) {
            ClaimFee(txid, _value);
        } else {
            ExceededClaimFee(txid, _value);
        }
    }

    /**
     * Somebody sends ETH to this contract address
     */
    function() {
        // just being sent some cash?
        if (msg.value > 0) {
            Deposit(msg.sender, msg.value);
        } else {
            NoMatchingFunction();
        }
    }

}