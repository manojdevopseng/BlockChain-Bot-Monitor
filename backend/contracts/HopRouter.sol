// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// HopRouter — reaches a pair no router can address.
//
// Some launchpads run their own AMM and deploy pairs from their own factory.
// A router finds a pair by hashing its own factory's address, so a pair that
// factory never made is one it cannot address at all — whatever the pair is
// quoted in, and however healthy it is. A token whose only market is such a
// pair is unreachable, not because anything is wrong with it, but because
// nobody can call two different AMMs from one externally owned account.
//
// This contract is that account's missing half. It spends the coin on a normal
// router, has the proceeds delivered straight into the odd pair, and then works
// the pair by hand — a plain UniswapV2 swap, which any pair of that shape
// answers no matter who deployed it.
//
// It owns nothing and remembers nothing. There is no admin, no upgrade path and
// no state; every call starts and ends with an empty contract, and anything
// that somehow lands here can be swept by whoever asks. What it will not do is
// finish a trade that did not pay: the floor is checked against the recipient's
// own balance before and after, so a token that quietly taxes the transfer is
// caught by the same check as a pool that moved.

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function transferFrom(address, address, uint256) external returns (bool);
    function approve(address, uint256) external returns (bool);
}

interface IWETH is IERC20 {
    function deposit() external payable;
    function withdraw(uint256) external;
}

interface IPair {
    function token0() external view returns (address);
    function token1() external view returns (address);
    function getReserves() external view returns (uint112, uint112, uint32);
    function swap(uint256, uint256, address, bytes calldata) external;
}

interface IRouter {
    function swapExactTokensForTokensSupportingFeeOnTransferTokens(
        uint256 amountIn, uint256 amountOutMin, address[] calldata path,
        address to, uint256 deadline) external;
}

contract HopRouter {
    address public immutable WNATIVE;

    constructor(address wnative) {
        WNATIVE = wnative;
    }

    // Only ever from unwrapping. Nothing else has a reason to send here.
    receive() external payable {}

    // What a UniswapV2 pair will pay for amountIn, at a fee the caller states.
    // The fee is a parameter rather than a constant because these pairs are not
    // all built to the same recipe — 0.25% and 0.30% are both common and some
    // launchpads pick their own. Naming it wrong is safe in only one direction:
    // too high asks the pair for less than it would have given, and the pair
    // agrees. Too low is refused by the pair's own invariant, which is why the
    // caller finds the right one by asking rather than by believing.
    function amountOut(uint256 amountIn, uint256 reserveIn, uint256 reserveOut,
                       uint256 feeBps) public pure returns (uint256) {
        uint256 afterFee = amountIn * (10000 - feeBps);
        return (afterFee * reserveOut) / (reserveIn * 10000 + afterFee);
    }

    // Work a pair that has already been paid. tokenIn must have been
    // transferred to pair before this runs — the amount is read as the
    // difference between what the pair holds and what it believes it holds,
    // which is how a router does it and the only way that survives a token
    // taking a cut in transit.
    function _work(address pair, address tokenIn, address to, uint256 feeBps)
        private returns (uint256)
    {
        (uint112 r0, uint112 r1,) = IPair(pair).getReserves();
        bool inIsZero = IPair(pair).token0() == tokenIn;
        uint256 reserveIn = inIsZero ? uint256(r0) : uint256(r1);
        uint256 reserveOut = inIsZero ? uint256(r1) : uint256(r0);

        uint256 paid = IERC20(tokenIn).balanceOf(pair) - reserveIn;
        require(paid > 0, "HopRouter: pair was not paid");
        uint256 out = amountOut(paid, reserveIn, reserveOut, feeBps);
        require(out > 0, "HopRouter: pair would pay nothing");

        IPair(pair).swap(inIsZero ? 0 : out, inIsZero ? out : 0, to, "");
        return out;
    }

    // Buy tokenOut with the coin, across two AMMs.
    //
    // path is walked on router — it starts at the wrapped coin and ends at
    // whatever pair is quoted in — and its proceeds are delivered into pair
    // rather than to this contract, so the second leg needs no approval and no
    // second transfer.
    function buy(address router, address[] calldata path, address pair,
                 address tokenOut, uint256 minOut, uint256 feeBps)
        external payable returns (uint256 received)
    {
        require(msg.value > 0, "HopRouter: nothing to spend");
        require(path.length >= 2 && path[0] == WNATIVE, "HopRouter: bad path");
        address mid = path[path.length - 1];

        IWETH(WNATIVE).deposit{value: msg.value}();
        IERC20(WNATIVE).approve(router, msg.value);
        IRouter(router).swapExactTokensForTokensSupportingFeeOnTransferTokens(
            msg.value, 0, path, pair, block.timestamp);

        uint256 before = IERC20(tokenOut).balanceOf(msg.sender);
        _work(pair, mid, msg.sender, feeBps);
        received = IERC20(tokenOut).balanceOf(msg.sender) - before;
        require(received >= minOut, "HopRouter: below the floor");
    }

    // Sell tokenIn back to the coin, the same road walked backwards. The caller
    // must have approved this contract for amountIn first.
    function sell(address pair, address tokenIn, address router,
                  address[] calldata path, uint256 amountIn, uint256 minOut,
                  uint256 feeBps) external returns (uint256 received)
    {
        require(amountIn > 0, "HopRouter: nothing to sell");
        require(path.length >= 2 && path[path.length - 1] == WNATIVE,
                "HopRouter: bad path");
        address mid = path[0];

        IERC20(tokenIn).transferFrom(msg.sender, pair, amountIn);
        _work(pair, tokenIn, address(this), feeBps);

        uint256 got = IERC20(mid).balanceOf(address(this));
        IERC20(mid).approve(router, got);
        IRouter(router).swapExactTokensForTokensSupportingFeeOnTransferTokens(
            got, 0, path, address(this), block.timestamp);

        received = IERC20(WNATIVE).balanceOf(address(this));
        require(received >= minOut, "HopRouter: below the floor");
        IWETH(WNATIVE).withdraw(received);
        (bool sent,) = msg.sender.call{value: received}("");
        require(sent, "HopRouter: coin would not send");
    }

    // Nothing is meant to stay here. If something does — a rounding crumb, a
    // token that pays more than it said — it belongs to whoever asks, not to
    // this contract, which has no one to keep it for.
    function sweep(address token) external {
        uint256 amount = IERC20(token).balanceOf(address(this));
        if (amount > 0) IERC20(token).transfer(msg.sender, amount);
        if (address(this).balance > 0) {
            (bool sent,) = msg.sender.call{value: address(this).balance}("");
            require(sent, "HopRouter: coin would not send");
        }
    }
}
