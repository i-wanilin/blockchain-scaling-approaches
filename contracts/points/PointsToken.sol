// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title PointsToken — the game's points, an ERC-20 with no value outside it.
/// @notice Deployed once per chain (it is part of each chain's snapshot). The
/// deployer is the only minter: at match start it gives every player the same
/// balance with one `mintBatch` call. The score of a match is how much each
/// balance changed. The same source is compiled for the EVM (Anvil for Perun,
/// Arbitrum) and for EraVM (zkSync), so all three systems run the same contract.
/// Cluster spec §7.3 "Points, not money".
contract PointsToken {
    string public constant name = "Points";
    string public constant symbol = "PTS";
    uint8 public constant decimals = 0; // one unit is one point

    address public immutable minter;
    uint256 public totalSupply;
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner, address indexed spender, uint256 value);

    error NotMinter();
    error InsufficientBalance();
    error InsufficientAllowance();

    constructor() {
        minter = msg.sender;
    }

    function mint(address to, uint256 amount) external {
        if (msg.sender != minter) revert NotMinter();
        _mint(to, amount);
    }

    /// Gives every address in `to` the same `amount`: one transaction per match.
    function mintBatch(address[] calldata to, uint256 amount) external {
        if (msg.sender != minter) revert NotMinter();
        for (uint256 i = 0; i < to.length; i++) _mint(to[i], amount);
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            if (allowed < amount) revert InsufficientAllowance();
            allowance[from][msg.sender] = allowed - amount;
        }
        _transfer(from, to, amount);
        return true;
    }

    function _mint(address to, uint256 amount) private {
        totalSupply += amount;
        balanceOf[to] += amount;
        emit Transfer(address(0), to, amount);
    }

    function _transfer(address from, address to, uint256 amount) private {
        uint256 bal = balanceOf[from];
        if (bal < amount) revert InsufficientBalance();
        balanceOf[from] = bal - amount;
        balanceOf[to] += amount;
        emit Transfer(from, to, amount);
    }
}
