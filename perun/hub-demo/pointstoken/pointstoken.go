// Code generated - DO NOT EDIT.
// This file is a generated binding and any manual changes will be lost.

package pointstoken

import (
	"errors"
	"math/big"
	"strings"

	ethereum "github.com/ethereum/go-ethereum"
	"github.com/ethereum/go-ethereum/accounts/abi"
	"github.com/ethereum/go-ethereum/accounts/abi/bind"
	"github.com/ethereum/go-ethereum/common"
	"github.com/ethereum/go-ethereum/core/types"
	"github.com/ethereum/go-ethereum/event"
)

// Reference imports to suppress errors if they are not otherwise used.
var (
	_ = errors.New
	_ = big.NewInt
	_ = strings.NewReader
	_ = ethereum.NotFound
	_ = bind.Bind
	_ = common.Big1
	_ = types.BloomLookup
	_ = event.NewSubscription
	_ = abi.ConvertType
)

// PointsTokenMetaData contains all meta data concerning the PointsToken contract.
var PointsTokenMetaData = &bind.MetaData{
	ABI: "[{\"inputs\":[],\"stateMutability\":\"nonpayable\",\"type\":\"constructor\"},{\"inputs\":[],\"name\":\"InsufficientAllowance\",\"type\":\"error\"},{\"inputs\":[],\"name\":\"InsufficientBalance\",\"type\":\"error\"},{\"inputs\":[],\"name\":\"NotMinter\",\"type\":\"error\"},{\"anonymous\":false,\"inputs\":[{\"indexed\":true,\"internalType\":\"address\",\"name\":\"owner\",\"type\":\"address\"},{\"indexed\":true,\"internalType\":\"address\",\"name\":\"spender\",\"type\":\"address\"},{\"indexed\":false,\"internalType\":\"uint256\",\"name\":\"value\",\"type\":\"uint256\"}],\"name\":\"Approval\",\"type\":\"event\"},{\"anonymous\":false,\"inputs\":[{\"indexed\":true,\"internalType\":\"address\",\"name\":\"from\",\"type\":\"address\"},{\"indexed\":true,\"internalType\":\"address\",\"name\":\"to\",\"type\":\"address\"},{\"indexed\":false,\"internalType\":\"uint256\",\"name\":\"value\",\"type\":\"uint256\"}],\"name\":\"Transfer\",\"type\":\"event\"},{\"inputs\":[{\"internalType\":\"address\",\"name\":\"\",\"type\":\"address\"},{\"internalType\":\"address\",\"name\":\"\",\"type\":\"address\"}],\"name\":\"allowance\",\"outputs\":[{\"internalType\":\"uint256\",\"name\":\"\",\"type\":\"uint256\"}],\"stateMutability\":\"view\",\"type\":\"function\"},{\"inputs\":[{\"internalType\":\"address\",\"name\":\"spender\",\"type\":\"address\"},{\"internalType\":\"uint256\",\"name\":\"amount\",\"type\":\"uint256\"}],\"name\":\"approve\",\"outputs\":[{\"internalType\":\"bool\",\"name\":\"\",\"type\":\"bool\"}],\"stateMutability\":\"nonpayable\",\"type\":\"function\"},{\"inputs\":[{\"internalType\":\"address\",\"name\":\"\",\"type\":\"address\"}],\"name\":\"balanceOf\",\"outputs\":[{\"internalType\":\"uint256\",\"name\":\"\",\"type\":\"uint256\"}],\"stateMutability\":\"view\",\"type\":\"function\"},{\"inputs\":[],\"name\":\"decimals\",\"outputs\":[{\"internalType\":\"uint8\",\"name\":\"\",\"type\":\"uint8\"}],\"stateMutability\":\"view\",\"type\":\"function\"},{\"inputs\":[{\"internalType\":\"address\",\"name\":\"to\",\"type\":\"address\"},{\"internalType\":\"uint256\",\"name\":\"amount\",\"type\":\"uint256\"}],\"name\":\"mint\",\"outputs\":[],\"stateMutability\":\"nonpayable\",\"type\":\"function\"},{\"inputs\":[{\"internalType\":\"address[]\",\"name\":\"to\",\"type\":\"address[]\"},{\"internalType\":\"uint256\",\"name\":\"amount\",\"type\":\"uint256\"}],\"name\":\"mintBatch\",\"outputs\":[],\"stateMutability\":\"nonpayable\",\"type\":\"function\"},{\"inputs\":[],\"name\":\"minter\",\"outputs\":[{\"internalType\":\"address\",\"name\":\"\",\"type\":\"address\"}],\"stateMutability\":\"view\",\"type\":\"function\"},{\"inputs\":[],\"name\":\"name\",\"outputs\":[{\"internalType\":\"string\",\"name\":\"\",\"type\":\"string\"}],\"stateMutability\":\"view\",\"type\":\"function\"},{\"inputs\":[],\"name\":\"symbol\",\"outputs\":[{\"internalType\":\"string\",\"name\":\"\",\"type\":\"string\"}],\"stateMutability\":\"view\",\"type\":\"function\"},{\"inputs\":[],\"name\":\"totalSupply\",\"outputs\":[{\"internalType\":\"uint256\",\"name\":\"\",\"type\":\"uint256\"}],\"stateMutability\":\"view\",\"type\":\"function\"},{\"inputs\":[{\"internalType\":\"address\",\"name\":\"to\",\"type\":\"address\"},{\"internalType\":\"uint256\",\"name\":\"amount\",\"type\":\"uint256\"}],\"name\":\"transfer\",\"outputs\":[{\"internalType\":\"bool\",\"name\":\"\",\"type\":\"bool\"}],\"stateMutability\":\"nonpayable\",\"type\":\"function\"},{\"inputs\":[{\"internalType\":\"address\",\"name\":\"from\",\"type\":\"address\"},{\"internalType\":\"address\",\"name\":\"to\",\"type\":\"address\"},{\"internalType\":\"uint256\",\"name\":\"amount\",\"type\":\"uint256\"}],\"name\":\"transferFrom\",\"outputs\":[{\"internalType\":\"bool\",\"name\":\"\",\"type\":\"bool\"}],\"stateMutability\":\"nonpayable\",\"type\":\"function\"}]",
	Bin: "0x60a0604052348015600e575f5ffd5b50336080526080516107866100385f395f818160f50152818161034101526103ad01526107865ff3fe608060405234801561000f575f5ffd5b50600436106100b1575f3560e01c806340c10f191161006e57806340c10f191461019457806370a08231146101a957806395d89b41146101c8578063a9059cbb146101ea578063b6afc4dc146101fd578063dd62ed3e14610210575f5ffd5b806306fdde03146100b557806307546172146100f0578063095ea7b31461012f57806318160ddd1461015257806323b872dd14610168578063313ce5671461017b575b5f5ffd5b6100da60405180604001604052806006815260200165506f696e747360d01b81525081565b6040516100e7919061058a565b60405180910390f35b6101177f000000000000000000000000000000000000000000000000000000000000000081565b6040516001600160a01b0390911681526020016100e7565b61014261013d3660046105da565b61023a565b60405190151581526020016100e7565b61015a5f5481565b6040519081526020016100e7565b610142610176366004610602565b6102a6565b6101825f81565b60405160ff90911681526020016100e7565b6101a76101a23660046105da565b610336565b005b61015a6101b736600461063c565b60016020525f908152604090205481565b6100da6040518060400160405280600381526020016250545360e81b81525081565b6101426101f83660046105da565b61038d565b6101a761020b36600461065c565b6103a2565b61015a61021e3660046106d1565b600260209081525f928352604080842090915290825290205481565b335f8181526002602090815260408083206001600160a01b038716808552925280832085905551919290917f8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925906102949086815260200190565b60405180910390a35060015b92915050565b6001600160a01b0383165f9081526002602090815260408083203384529091528120545f19811461032057828110156102f2576040516313be252b60e01b815260040160405180910390fd5b6102fc8382610716565b6001600160a01b0386165f9081526002602090815260408083203384529091529020555b61032b858585610433565b506001949350505050565b336001600160a01b037f0000000000000000000000000000000000000000000000000000000000000000161461037f57604051633e34a41b60e21b815260040160405180910390fd5b6103898282610505565b5050565b5f610399338484610433565b50600192915050565b336001600160a01b037f000000000000000000000000000000000000000000000000000000000000000016146103eb57604051633e34a41b60e21b815260040160405180910390fd5b5f5b8281101561042d5761042584848381811061040a5761040a610729565b905060200201602081019061041f919061063c565b83610505565b6001016103ed565b50505050565b6001600160a01b0383165f908152600160205260409020548181101561046c57604051631e9acf1760e31b815260040160405180910390fd5b6104768282610716565b6001600160a01b038086165f9081526001602052604080822093909355908516815290812080548492906104ab90849061073d565b92505081905550826001600160a01b0316846001600160a01b03167fddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef846040516104f791815260200190565b60405180910390a350505050565b805f5f828254610515919061073d565b90915550506001600160a01b0382165f908152600160205260408120805483929061054190849061073d565b90915550506040518181526001600160a01b038316905f907fddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef9060200160405180910390a35050565b602081525f82518060208401528060208501604085015e5f604082850101526040601f19601f83011684010191505092915050565b80356001600160a01b03811681146105d5575f5ffd5b919050565b5f5f604083850312156105eb575f5ffd5b6105f4836105bf565b946020939093013593505050565b5f5f5f60608486031215610614575f5ffd5b61061d846105bf565b925061062b602085016105bf565b929592945050506040919091013590565b5f6020828403121561064c575f5ffd5b610655826105bf565b9392505050565b5f5f5f6040848603121561066e575f5ffd5b833567ffffffffffffffff811115610684575f5ffd5b8401601f81018613610694575f5ffd5b803567ffffffffffffffff8111156106aa575f5ffd5b8660208260051b84010111156106be575f5ffd5b6020918201979096509401359392505050565b5f5f604083850312156106e2575f5ffd5b6106eb836105bf565b91506106f9602084016105bf565b90509250929050565b634e487b7160e01b5f52601160045260245ffd5b818103818111156102a0576102a0610702565b634e487b7160e01b5f52603260045260245ffd5b808201808211156102a0576102a061070256fea26469706673582212205b417a30f4dad12d1ba93cf783686d60ad44b6bebf96f19f2ffc418864ab23df64736f6c634300081e0033",
}

// PointsTokenABI is the input ABI used to generate the binding from.
// Deprecated: Use PointsTokenMetaData.ABI instead.
var PointsTokenABI = PointsTokenMetaData.ABI

// PointsTokenBin is the compiled bytecode used for deploying new contracts.
// Deprecated: Use PointsTokenMetaData.Bin instead.
var PointsTokenBin = PointsTokenMetaData.Bin

// DeployPointsToken deploys a new Ethereum contract, binding an instance of PointsToken to it.
func DeployPointsToken(auth *bind.TransactOpts, backend bind.ContractBackend) (common.Address, *types.Transaction, *PointsToken, error) {
	parsed, err := PointsTokenMetaData.GetAbi()
	if err != nil {
		return common.Address{}, nil, nil, err
	}
	if parsed == nil {
		return common.Address{}, nil, nil, errors.New("GetABI returned nil")
	}

	address, tx, contract, err := bind.DeployContract(auth, *parsed, common.FromHex(PointsTokenBin), backend)
	if err != nil {
		return common.Address{}, nil, nil, err
	}
	return address, tx, &PointsToken{PointsTokenCaller: PointsTokenCaller{contract: contract}, PointsTokenTransactor: PointsTokenTransactor{contract: contract}, PointsTokenFilterer: PointsTokenFilterer{contract: contract}}, nil
}

// PointsToken is an auto generated Go binding around an Ethereum contract.
type PointsToken struct {
	PointsTokenCaller     // Read-only binding to the contract
	PointsTokenTransactor // Write-only binding to the contract
	PointsTokenFilterer   // Log filterer for contract events
}

// PointsTokenCaller is an auto generated read-only Go binding around an Ethereum contract.
type PointsTokenCaller struct {
	contract *bind.BoundContract // Generic contract wrapper for the low level calls
}

// PointsTokenTransactor is an auto generated write-only Go binding around an Ethereum contract.
type PointsTokenTransactor struct {
	contract *bind.BoundContract // Generic contract wrapper for the low level calls
}

// PointsTokenFilterer is an auto generated log filtering Go binding around an Ethereum contract events.
type PointsTokenFilterer struct {
	contract *bind.BoundContract // Generic contract wrapper for the low level calls
}

// PointsTokenSession is an auto generated Go binding around an Ethereum contract,
// with pre-set call and transact options.
type PointsTokenSession struct {
	Contract     *PointsToken      // Generic contract binding to set the session for
	CallOpts     bind.CallOpts     // Call options to use throughout this session
	TransactOpts bind.TransactOpts // Transaction auth options to use throughout this session
}

// PointsTokenCallerSession is an auto generated read-only Go binding around an Ethereum contract,
// with pre-set call options.
type PointsTokenCallerSession struct {
	Contract *PointsTokenCaller // Generic contract caller binding to set the session for
	CallOpts bind.CallOpts      // Call options to use throughout this session
}

// PointsTokenTransactorSession is an auto generated write-only Go binding around an Ethereum contract,
// with pre-set transact options.
type PointsTokenTransactorSession struct {
	Contract     *PointsTokenTransactor // Generic contract transactor binding to set the session for
	TransactOpts bind.TransactOpts      // Transaction auth options to use throughout this session
}

// PointsTokenRaw is an auto generated low-level Go binding around an Ethereum contract.
type PointsTokenRaw struct {
	Contract *PointsToken // Generic contract binding to access the raw methods on
}

// PointsTokenCallerRaw is an auto generated low-level read-only Go binding around an Ethereum contract.
type PointsTokenCallerRaw struct {
	Contract *PointsTokenCaller // Generic read-only contract binding to access the raw methods on
}

// PointsTokenTransactorRaw is an auto generated low-level write-only Go binding around an Ethereum contract.
type PointsTokenTransactorRaw struct {
	Contract *PointsTokenTransactor // Generic write-only contract binding to access the raw methods on
}

// NewPointsToken creates a new instance of PointsToken, bound to a specific deployed contract.
func NewPointsToken(address common.Address, backend bind.ContractBackend) (*PointsToken, error) {
	contract, err := bindPointsToken(address, backend, backend, backend)
	if err != nil {
		return nil, err
	}
	return &PointsToken{PointsTokenCaller: PointsTokenCaller{contract: contract}, PointsTokenTransactor: PointsTokenTransactor{contract: contract}, PointsTokenFilterer: PointsTokenFilterer{contract: contract}}, nil
}

// NewPointsTokenCaller creates a new read-only instance of PointsToken, bound to a specific deployed contract.
func NewPointsTokenCaller(address common.Address, caller bind.ContractCaller) (*PointsTokenCaller, error) {
	contract, err := bindPointsToken(address, caller, nil, nil)
	if err != nil {
		return nil, err
	}
	return &PointsTokenCaller{contract: contract}, nil
}

// NewPointsTokenTransactor creates a new write-only instance of PointsToken, bound to a specific deployed contract.
func NewPointsTokenTransactor(address common.Address, transactor bind.ContractTransactor) (*PointsTokenTransactor, error) {
	contract, err := bindPointsToken(address, nil, transactor, nil)
	if err != nil {
		return nil, err
	}
	return &PointsTokenTransactor{contract: contract}, nil
}

// NewPointsTokenFilterer creates a new log filterer instance of PointsToken, bound to a specific deployed contract.
func NewPointsTokenFilterer(address common.Address, filterer bind.ContractFilterer) (*PointsTokenFilterer, error) {
	contract, err := bindPointsToken(address, nil, nil, filterer)
	if err != nil {
		return nil, err
	}
	return &PointsTokenFilterer{contract: contract}, nil
}

// bindPointsToken binds a generic wrapper to an already deployed contract.
func bindPointsToken(address common.Address, caller bind.ContractCaller, transactor bind.ContractTransactor, filterer bind.ContractFilterer) (*bind.BoundContract, error) {
	parsed, err := PointsTokenMetaData.GetAbi()
	if err != nil {
		return nil, err
	}
	return bind.NewBoundContract(address, *parsed, caller, transactor, filterer), nil
}

// Call invokes the (constant) contract method with params as input values and
// sets the output to result. The result type might be a single field for simple
// returns, a slice of interfaces for anonymous returns and a struct for named
// returns.
func (_PointsToken *PointsTokenRaw) Call(opts *bind.CallOpts, result *[]interface{}, method string, params ...interface{}) error {
	return _PointsToken.Contract.PointsTokenCaller.contract.Call(opts, result, method, params...)
}

// Transfer initiates a plain transaction to move funds to the contract, calling
// its default method if one is available.
func (_PointsToken *PointsTokenRaw) Transfer(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _PointsToken.Contract.PointsTokenTransactor.contract.Transfer(opts)
}

// Transact invokes the (paid) contract method with params as input values.
func (_PointsToken *PointsTokenRaw) Transact(opts *bind.TransactOpts, method string, params ...interface{}) (*types.Transaction, error) {
	return _PointsToken.Contract.PointsTokenTransactor.contract.Transact(opts, method, params...)
}

// Call invokes the (constant) contract method with params as input values and
// sets the output to result. The result type might be a single field for simple
// returns, a slice of interfaces for anonymous returns and a struct for named
// returns.
func (_PointsToken *PointsTokenCallerRaw) Call(opts *bind.CallOpts, result *[]interface{}, method string, params ...interface{}) error {
	return _PointsToken.Contract.contract.Call(opts, result, method, params...)
}

// Transfer initiates a plain transaction to move funds to the contract, calling
// its default method if one is available.
func (_PointsToken *PointsTokenTransactorRaw) Transfer(opts *bind.TransactOpts) (*types.Transaction, error) {
	return _PointsToken.Contract.contract.Transfer(opts)
}

// Transact invokes the (paid) contract method with params as input values.
func (_PointsToken *PointsTokenTransactorRaw) Transact(opts *bind.TransactOpts, method string, params ...interface{}) (*types.Transaction, error) {
	return _PointsToken.Contract.contract.Transact(opts, method, params...)
}

// Allowance is a free data retrieval call binding the contract method 0xdd62ed3e.
//
// Solidity: function allowance(address , address ) view returns(uint256)
func (_PointsToken *PointsTokenCaller) Allowance(opts *bind.CallOpts, arg0 common.Address, arg1 common.Address) (*big.Int, error) {
	var out []interface{}
	err := _PointsToken.contract.Call(opts, &out, "allowance", arg0, arg1)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// Allowance is a free data retrieval call binding the contract method 0xdd62ed3e.
//
// Solidity: function allowance(address , address ) view returns(uint256)
func (_PointsToken *PointsTokenSession) Allowance(arg0 common.Address, arg1 common.Address) (*big.Int, error) {
	return _PointsToken.Contract.Allowance(&_PointsToken.CallOpts, arg0, arg1)
}

// Allowance is a free data retrieval call binding the contract method 0xdd62ed3e.
//
// Solidity: function allowance(address , address ) view returns(uint256)
func (_PointsToken *PointsTokenCallerSession) Allowance(arg0 common.Address, arg1 common.Address) (*big.Int, error) {
	return _PointsToken.Contract.Allowance(&_PointsToken.CallOpts, arg0, arg1)
}

// BalanceOf is a free data retrieval call binding the contract method 0x70a08231.
//
// Solidity: function balanceOf(address ) view returns(uint256)
func (_PointsToken *PointsTokenCaller) BalanceOf(opts *bind.CallOpts, arg0 common.Address) (*big.Int, error) {
	var out []interface{}
	err := _PointsToken.contract.Call(opts, &out, "balanceOf", arg0)

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// BalanceOf is a free data retrieval call binding the contract method 0x70a08231.
//
// Solidity: function balanceOf(address ) view returns(uint256)
func (_PointsToken *PointsTokenSession) BalanceOf(arg0 common.Address) (*big.Int, error) {
	return _PointsToken.Contract.BalanceOf(&_PointsToken.CallOpts, arg0)
}

// BalanceOf is a free data retrieval call binding the contract method 0x70a08231.
//
// Solidity: function balanceOf(address ) view returns(uint256)
func (_PointsToken *PointsTokenCallerSession) BalanceOf(arg0 common.Address) (*big.Int, error) {
	return _PointsToken.Contract.BalanceOf(&_PointsToken.CallOpts, arg0)
}

// Decimals is a free data retrieval call binding the contract method 0x313ce567.
//
// Solidity: function decimals() view returns(uint8)
func (_PointsToken *PointsTokenCaller) Decimals(opts *bind.CallOpts) (uint8, error) {
	var out []interface{}
	err := _PointsToken.contract.Call(opts, &out, "decimals")

	if err != nil {
		return *new(uint8), err
	}

	out0 := *abi.ConvertType(out[0], new(uint8)).(*uint8)

	return out0, err

}

// Decimals is a free data retrieval call binding the contract method 0x313ce567.
//
// Solidity: function decimals() view returns(uint8)
func (_PointsToken *PointsTokenSession) Decimals() (uint8, error) {
	return _PointsToken.Contract.Decimals(&_PointsToken.CallOpts)
}

// Decimals is a free data retrieval call binding the contract method 0x313ce567.
//
// Solidity: function decimals() view returns(uint8)
func (_PointsToken *PointsTokenCallerSession) Decimals() (uint8, error) {
	return _PointsToken.Contract.Decimals(&_PointsToken.CallOpts)
}

// Minter is a free data retrieval call binding the contract method 0x07546172.
//
// Solidity: function minter() view returns(address)
func (_PointsToken *PointsTokenCaller) Minter(opts *bind.CallOpts) (common.Address, error) {
	var out []interface{}
	err := _PointsToken.contract.Call(opts, &out, "minter")

	if err != nil {
		return *new(common.Address), err
	}

	out0 := *abi.ConvertType(out[0], new(common.Address)).(*common.Address)

	return out0, err

}

// Minter is a free data retrieval call binding the contract method 0x07546172.
//
// Solidity: function minter() view returns(address)
func (_PointsToken *PointsTokenSession) Minter() (common.Address, error) {
	return _PointsToken.Contract.Minter(&_PointsToken.CallOpts)
}

// Minter is a free data retrieval call binding the contract method 0x07546172.
//
// Solidity: function minter() view returns(address)
func (_PointsToken *PointsTokenCallerSession) Minter() (common.Address, error) {
	return _PointsToken.Contract.Minter(&_PointsToken.CallOpts)
}

// Name is a free data retrieval call binding the contract method 0x06fdde03.
//
// Solidity: function name() view returns(string)
func (_PointsToken *PointsTokenCaller) Name(opts *bind.CallOpts) (string, error) {
	var out []interface{}
	err := _PointsToken.contract.Call(opts, &out, "name")

	if err != nil {
		return *new(string), err
	}

	out0 := *abi.ConvertType(out[0], new(string)).(*string)

	return out0, err

}

// Name is a free data retrieval call binding the contract method 0x06fdde03.
//
// Solidity: function name() view returns(string)
func (_PointsToken *PointsTokenSession) Name() (string, error) {
	return _PointsToken.Contract.Name(&_PointsToken.CallOpts)
}

// Name is a free data retrieval call binding the contract method 0x06fdde03.
//
// Solidity: function name() view returns(string)
func (_PointsToken *PointsTokenCallerSession) Name() (string, error) {
	return _PointsToken.Contract.Name(&_PointsToken.CallOpts)
}

// Symbol is a free data retrieval call binding the contract method 0x95d89b41.
//
// Solidity: function symbol() view returns(string)
func (_PointsToken *PointsTokenCaller) Symbol(opts *bind.CallOpts) (string, error) {
	var out []interface{}
	err := _PointsToken.contract.Call(opts, &out, "symbol")

	if err != nil {
		return *new(string), err
	}

	out0 := *abi.ConvertType(out[0], new(string)).(*string)

	return out0, err

}

// Symbol is a free data retrieval call binding the contract method 0x95d89b41.
//
// Solidity: function symbol() view returns(string)
func (_PointsToken *PointsTokenSession) Symbol() (string, error) {
	return _PointsToken.Contract.Symbol(&_PointsToken.CallOpts)
}

// Symbol is a free data retrieval call binding the contract method 0x95d89b41.
//
// Solidity: function symbol() view returns(string)
func (_PointsToken *PointsTokenCallerSession) Symbol() (string, error) {
	return _PointsToken.Contract.Symbol(&_PointsToken.CallOpts)
}

// TotalSupply is a free data retrieval call binding the contract method 0x18160ddd.
//
// Solidity: function totalSupply() view returns(uint256)
func (_PointsToken *PointsTokenCaller) TotalSupply(opts *bind.CallOpts) (*big.Int, error) {
	var out []interface{}
	err := _PointsToken.contract.Call(opts, &out, "totalSupply")

	if err != nil {
		return *new(*big.Int), err
	}

	out0 := *abi.ConvertType(out[0], new(*big.Int)).(**big.Int)

	return out0, err

}

// TotalSupply is a free data retrieval call binding the contract method 0x18160ddd.
//
// Solidity: function totalSupply() view returns(uint256)
func (_PointsToken *PointsTokenSession) TotalSupply() (*big.Int, error) {
	return _PointsToken.Contract.TotalSupply(&_PointsToken.CallOpts)
}

// TotalSupply is a free data retrieval call binding the contract method 0x18160ddd.
//
// Solidity: function totalSupply() view returns(uint256)
func (_PointsToken *PointsTokenCallerSession) TotalSupply() (*big.Int, error) {
	return _PointsToken.Contract.TotalSupply(&_PointsToken.CallOpts)
}

// Approve is a paid mutator transaction binding the contract method 0x095ea7b3.
//
// Solidity: function approve(address spender, uint256 amount) returns(bool)
func (_PointsToken *PointsTokenTransactor) Approve(opts *bind.TransactOpts, spender common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.contract.Transact(opts, "approve", spender, amount)
}

// Approve is a paid mutator transaction binding the contract method 0x095ea7b3.
//
// Solidity: function approve(address spender, uint256 amount) returns(bool)
func (_PointsToken *PointsTokenSession) Approve(spender common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.Approve(&_PointsToken.TransactOpts, spender, amount)
}

// Approve is a paid mutator transaction binding the contract method 0x095ea7b3.
//
// Solidity: function approve(address spender, uint256 amount) returns(bool)
func (_PointsToken *PointsTokenTransactorSession) Approve(spender common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.Approve(&_PointsToken.TransactOpts, spender, amount)
}

// Mint is a paid mutator transaction binding the contract method 0x40c10f19.
//
// Solidity: function mint(address to, uint256 amount) returns()
func (_PointsToken *PointsTokenTransactor) Mint(opts *bind.TransactOpts, to common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.contract.Transact(opts, "mint", to, amount)
}

// Mint is a paid mutator transaction binding the contract method 0x40c10f19.
//
// Solidity: function mint(address to, uint256 amount) returns()
func (_PointsToken *PointsTokenSession) Mint(to common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.Mint(&_PointsToken.TransactOpts, to, amount)
}

// Mint is a paid mutator transaction binding the contract method 0x40c10f19.
//
// Solidity: function mint(address to, uint256 amount) returns()
func (_PointsToken *PointsTokenTransactorSession) Mint(to common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.Mint(&_PointsToken.TransactOpts, to, amount)
}

// MintBatch is a paid mutator transaction binding the contract method 0xb6afc4dc.
//
// Solidity: function mintBatch(address[] to, uint256 amount) returns()
func (_PointsToken *PointsTokenTransactor) MintBatch(opts *bind.TransactOpts, to []common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.contract.Transact(opts, "mintBatch", to, amount)
}

// MintBatch is a paid mutator transaction binding the contract method 0xb6afc4dc.
//
// Solidity: function mintBatch(address[] to, uint256 amount) returns()
func (_PointsToken *PointsTokenSession) MintBatch(to []common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.MintBatch(&_PointsToken.TransactOpts, to, amount)
}

// MintBatch is a paid mutator transaction binding the contract method 0xb6afc4dc.
//
// Solidity: function mintBatch(address[] to, uint256 amount) returns()
func (_PointsToken *PointsTokenTransactorSession) MintBatch(to []common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.MintBatch(&_PointsToken.TransactOpts, to, amount)
}

// Transfer is a paid mutator transaction binding the contract method 0xa9059cbb.
//
// Solidity: function transfer(address to, uint256 amount) returns(bool)
func (_PointsToken *PointsTokenTransactor) Transfer(opts *bind.TransactOpts, to common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.contract.Transact(opts, "transfer", to, amount)
}

// Transfer is a paid mutator transaction binding the contract method 0xa9059cbb.
//
// Solidity: function transfer(address to, uint256 amount) returns(bool)
func (_PointsToken *PointsTokenSession) Transfer(to common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.Transfer(&_PointsToken.TransactOpts, to, amount)
}

// Transfer is a paid mutator transaction binding the contract method 0xa9059cbb.
//
// Solidity: function transfer(address to, uint256 amount) returns(bool)
func (_PointsToken *PointsTokenTransactorSession) Transfer(to common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.Transfer(&_PointsToken.TransactOpts, to, amount)
}

// TransferFrom is a paid mutator transaction binding the contract method 0x23b872dd.
//
// Solidity: function transferFrom(address from, address to, uint256 amount) returns(bool)
func (_PointsToken *PointsTokenTransactor) TransferFrom(opts *bind.TransactOpts, from common.Address, to common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.contract.Transact(opts, "transferFrom", from, to, amount)
}

// TransferFrom is a paid mutator transaction binding the contract method 0x23b872dd.
//
// Solidity: function transferFrom(address from, address to, uint256 amount) returns(bool)
func (_PointsToken *PointsTokenSession) TransferFrom(from common.Address, to common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.TransferFrom(&_PointsToken.TransactOpts, from, to, amount)
}

// TransferFrom is a paid mutator transaction binding the contract method 0x23b872dd.
//
// Solidity: function transferFrom(address from, address to, uint256 amount) returns(bool)
func (_PointsToken *PointsTokenTransactorSession) TransferFrom(from common.Address, to common.Address, amount *big.Int) (*types.Transaction, error) {
	return _PointsToken.Contract.TransferFrom(&_PointsToken.TransactOpts, from, to, amount)
}

// PointsTokenApprovalIterator is returned from FilterApproval and is used to iterate over the raw logs and unpacked data for Approval events raised by the PointsToken contract.
type PointsTokenApprovalIterator struct {
	Event *PointsTokenApproval // Event containing the contract specifics and raw log

	contract *bind.BoundContract // Generic contract to use for unpacking event data
	event    string              // Event name to use for unpacking event data

	logs chan types.Log        // Log channel receiving the found contract events
	sub  ethereum.Subscription // Subscription for errors, completion and termination
	done bool                  // Whether the subscription completed delivering logs
	fail error                 // Occurred error to stop iteration
}

// Next advances the iterator to the subsequent event, returning whether there
// are any more events found. In case of a retrieval or parsing error, false is
// returned and Error() can be queried for the exact failure.
func (it *PointsTokenApprovalIterator) Next() bool {
	// If the iterator failed, stop iterating
	if it.fail != nil {
		return false
	}
	// If the iterator completed, deliver directly whatever's available
	if it.done {
		select {
		case log := <-it.logs:
			it.Event = new(PointsTokenApproval)
			if err := it.contract.UnpackLog(it.Event, it.event, log); err != nil {
				it.fail = err
				return false
			}
			it.Event.Raw = log
			return true

		default:
			return false
		}
	}
	// Iterator still in progress, wait for either a data or an error event
	select {
	case log := <-it.logs:
		it.Event = new(PointsTokenApproval)
		if err := it.contract.UnpackLog(it.Event, it.event, log); err != nil {
			it.fail = err
			return false
		}
		it.Event.Raw = log
		return true

	case err := <-it.sub.Err():
		it.done = true
		it.fail = err
		return it.Next()
	}
}

// Error returns any retrieval or parsing error occurred during filtering.
func (it *PointsTokenApprovalIterator) Error() error {
	return it.fail
}

// Close terminates the iteration process, releasing any pending underlying
// resources.
func (it *PointsTokenApprovalIterator) Close() error {
	it.sub.Unsubscribe()
	return nil
}

// PointsTokenApproval represents a Approval event raised by the PointsToken contract.
type PointsTokenApproval struct {
	Owner   common.Address
	Spender common.Address
	Value   *big.Int
	Raw     types.Log // Blockchain specific contextual infos
}

// FilterApproval is a free log retrieval operation binding the contract event 0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925.
//
// Solidity: event Approval(address indexed owner, address indexed spender, uint256 value)
func (_PointsToken *PointsTokenFilterer) FilterApproval(opts *bind.FilterOpts, owner []common.Address, spender []common.Address) (*PointsTokenApprovalIterator, error) {

	var ownerRule []interface{}
	for _, ownerItem := range owner {
		ownerRule = append(ownerRule, ownerItem)
	}
	var spenderRule []interface{}
	for _, spenderItem := range spender {
		spenderRule = append(spenderRule, spenderItem)
	}

	logs, sub, err := _PointsToken.contract.FilterLogs(opts, "Approval", ownerRule, spenderRule)
	if err != nil {
		return nil, err
	}
	return &PointsTokenApprovalIterator{contract: _PointsToken.contract, event: "Approval", logs: logs, sub: sub}, nil
}

// WatchApproval is a free log subscription operation binding the contract event 0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925.
//
// Solidity: event Approval(address indexed owner, address indexed spender, uint256 value)
func (_PointsToken *PointsTokenFilterer) WatchApproval(opts *bind.WatchOpts, sink chan<- *PointsTokenApproval, owner []common.Address, spender []common.Address) (event.Subscription, error) {

	var ownerRule []interface{}
	for _, ownerItem := range owner {
		ownerRule = append(ownerRule, ownerItem)
	}
	var spenderRule []interface{}
	for _, spenderItem := range spender {
		spenderRule = append(spenderRule, spenderItem)
	}

	logs, sub, err := _PointsToken.contract.WatchLogs(opts, "Approval", ownerRule, spenderRule)
	if err != nil {
		return nil, err
	}
	return event.NewSubscription(func(quit <-chan struct{}) error {
		defer sub.Unsubscribe()
		for {
			select {
			case log := <-logs:
				// New log arrived, parse the event and forward to the user
				event := new(PointsTokenApproval)
				if err := _PointsToken.contract.UnpackLog(event, "Approval", log); err != nil {
					return err
				}
				event.Raw = log

				select {
				case sink <- event:
				case err := <-sub.Err():
					return err
				case <-quit:
					return nil
				}
			case err := <-sub.Err():
				return err
			case <-quit:
				return nil
			}
		}
	}), nil
}

// ParseApproval is a log parse operation binding the contract event 0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925.
//
// Solidity: event Approval(address indexed owner, address indexed spender, uint256 value)
func (_PointsToken *PointsTokenFilterer) ParseApproval(log types.Log) (*PointsTokenApproval, error) {
	event := new(PointsTokenApproval)
	if err := _PointsToken.contract.UnpackLog(event, "Approval", log); err != nil {
		return nil, err
	}
	event.Raw = log
	return event, nil
}

// PointsTokenTransferIterator is returned from FilterTransfer and is used to iterate over the raw logs and unpacked data for Transfer events raised by the PointsToken contract.
type PointsTokenTransferIterator struct {
	Event *PointsTokenTransfer // Event containing the contract specifics and raw log

	contract *bind.BoundContract // Generic contract to use for unpacking event data
	event    string              // Event name to use for unpacking event data

	logs chan types.Log        // Log channel receiving the found contract events
	sub  ethereum.Subscription // Subscription for errors, completion and termination
	done bool                  // Whether the subscription completed delivering logs
	fail error                 // Occurred error to stop iteration
}

// Next advances the iterator to the subsequent event, returning whether there
// are any more events found. In case of a retrieval or parsing error, false is
// returned and Error() can be queried for the exact failure.
func (it *PointsTokenTransferIterator) Next() bool {
	// If the iterator failed, stop iterating
	if it.fail != nil {
		return false
	}
	// If the iterator completed, deliver directly whatever's available
	if it.done {
		select {
		case log := <-it.logs:
			it.Event = new(PointsTokenTransfer)
			if err := it.contract.UnpackLog(it.Event, it.event, log); err != nil {
				it.fail = err
				return false
			}
			it.Event.Raw = log
			return true

		default:
			return false
		}
	}
	// Iterator still in progress, wait for either a data or an error event
	select {
	case log := <-it.logs:
		it.Event = new(PointsTokenTransfer)
		if err := it.contract.UnpackLog(it.Event, it.event, log); err != nil {
			it.fail = err
			return false
		}
		it.Event.Raw = log
		return true

	case err := <-it.sub.Err():
		it.done = true
		it.fail = err
		return it.Next()
	}
}

// Error returns any retrieval or parsing error occurred during filtering.
func (it *PointsTokenTransferIterator) Error() error {
	return it.fail
}

// Close terminates the iteration process, releasing any pending underlying
// resources.
func (it *PointsTokenTransferIterator) Close() error {
	it.sub.Unsubscribe()
	return nil
}

// PointsTokenTransfer represents a Transfer event raised by the PointsToken contract.
type PointsTokenTransfer struct {
	From  common.Address
	To    common.Address
	Value *big.Int
	Raw   types.Log // Blockchain specific contextual infos
}

// FilterTransfer is a free log retrieval operation binding the contract event 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef.
//
// Solidity: event Transfer(address indexed from, address indexed to, uint256 value)
func (_PointsToken *PointsTokenFilterer) FilterTransfer(opts *bind.FilterOpts, from []common.Address, to []common.Address) (*PointsTokenTransferIterator, error) {

	var fromRule []interface{}
	for _, fromItem := range from {
		fromRule = append(fromRule, fromItem)
	}
	var toRule []interface{}
	for _, toItem := range to {
		toRule = append(toRule, toItem)
	}

	logs, sub, err := _PointsToken.contract.FilterLogs(opts, "Transfer", fromRule, toRule)
	if err != nil {
		return nil, err
	}
	return &PointsTokenTransferIterator{contract: _PointsToken.contract, event: "Transfer", logs: logs, sub: sub}, nil
}

// WatchTransfer is a free log subscription operation binding the contract event 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef.
//
// Solidity: event Transfer(address indexed from, address indexed to, uint256 value)
func (_PointsToken *PointsTokenFilterer) WatchTransfer(opts *bind.WatchOpts, sink chan<- *PointsTokenTransfer, from []common.Address, to []common.Address) (event.Subscription, error) {

	var fromRule []interface{}
	for _, fromItem := range from {
		fromRule = append(fromRule, fromItem)
	}
	var toRule []interface{}
	for _, toItem := range to {
		toRule = append(toRule, toItem)
	}

	logs, sub, err := _PointsToken.contract.WatchLogs(opts, "Transfer", fromRule, toRule)
	if err != nil {
		return nil, err
	}
	return event.NewSubscription(func(quit <-chan struct{}) error {
		defer sub.Unsubscribe()
		for {
			select {
			case log := <-logs:
				// New log arrived, parse the event and forward to the user
				event := new(PointsTokenTransfer)
				if err := _PointsToken.contract.UnpackLog(event, "Transfer", log); err != nil {
					return err
				}
				event.Raw = log

				select {
				case sink <- event:
				case err := <-sub.Err():
					return err
				case <-quit:
					return nil
				}
			case err := <-sub.Err():
				return err
			case <-quit:
				return nil
			}
		}
	}), nil
}

// ParseTransfer is a log parse operation binding the contract event 0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef.
//
// Solidity: event Transfer(address indexed from, address indexed to, uint256 value)
func (_PointsToken *PointsTokenFilterer) ParseTransfer(log types.Log) (*PointsTokenTransfer, error) {
	event := new(PointsTokenTransfer)
	if err := _PointsToken.contract.UnpackLog(event, "Transfer", log); err != nil {
		return nil, err
	}
	event.Raw = log
	return event, nil
}
