// Package lanwire provides a small, persistent wire identity for the
// distributed hub-demo. It deliberately uses the Ethereum wire backend ID so
// that a go-perun client has one stable address map for both channel proposals
// and the network bus. The address itself is an RSA public key plus a logical
// node name; it is not an Ethereum account.
package lanwire

import (
	"crypto"
	"crypto/rand"
	"crypto/rsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/binary"
	"encoding/pem"
	"fmt"
	"os"
	"path/filepath"

	ethwallet "github.com/perun-network/perun-eth-backend/wallet"
	"perun.network/go-perun/wallet"
	"perun.network/go-perun/wire"
)

// Address is the authenticated, persistent address used by the TCP wire bus.
// It implements wire.Address and is serialised in channel proposals.
type Address struct {
	Name string
	Key  *rsa.PublicKey
}

// Backend returns the Ethereum backend ID, matching the wallet backend used by
// this demo. A Perun wire-address map may therefore be decoded consistently.
func (*Address) Backend() wallet.BackendID { return ethwallet.BackendID }

func (a *Address) Equal(b wire.Address) bool {
	o, ok := b.(*Address)
	return ok && a.Name == o.Name && a.Key != nil && o.Key != nil && a.Key.Equal(o.Key)
}

func (a *Address) Cmp(b wire.Address) int {
	o, ok := b.(*Address)
	if !ok {
		panic("lanwire: compared with a different address type")
	}
	ab, err := a.MarshalBinary()
	if err != nil {
		panic(err)
	}
	bb, err := o.MarshalBinary()
	if err != nil {
		panic(err)
	}
	if string(ab) < string(bb) {
		return -1
	}
	if string(ab) > string(bb) {
		return 1
	}
	return 0
}

func (a *Address) String() string { return a.Name }

// MarshalBinary stores the logical name and PKIX public key.
func (a *Address) MarshalBinary() ([]byte, error) {
	if a.Key == nil {
		return nil, fmt.Errorf("lanwire: nil public key")
	}
	der, err := x509.MarshalPKIXPublicKey(a.Key)
	if err != nil {
		return nil, err
	}
	if len(a.Name) > 65535 || len(der) > 65535 {
		return nil, fmt.Errorf("lanwire: address too large")
	}
	b := make([]byte, 4+len(a.Name)+len(der))
	binary.BigEndian.PutUint16(b[:2], uint16(len(a.Name)))
	copy(b[2:], a.Name)
	off := 2 + len(a.Name)
	binary.BigEndian.PutUint16(b[off:off+2], uint16(len(der)))
	copy(b[off+2:], der)
	return b, nil
}

func (a *Address) UnmarshalBinary(b []byte) error {
	if len(b) < 4 {
		return fmt.Errorf("lanwire: malformed address")
	}
	n := int(binary.BigEndian.Uint16(b[:2]))
	if len(b) < 2+n+2 {
		return fmt.Errorf("lanwire: malformed name")
	}
	a.Name = string(b[2 : 2+n])
	off := 2 + n
	k := int(binary.BigEndian.Uint16(b[off : off+2]))
	if len(b) != off+2+k {
		return fmt.Errorf("lanwire: malformed key")
	}
	pub, err := x509.ParsePKIXPublicKey(b[off+2:])
	if err != nil {
		return err
	}
	key, ok := pub.(*rsa.PublicKey)
	if !ok {
		return fmt.Errorf("lanwire: expected RSA public key")
	}
	a.Key = key
	return nil
}

func (a *Address) Verify(message, sig []byte) error {
	if a.Key == nil {
		return fmt.Errorf("lanwire: nil public key")
	}
	h := sha256.Sum256(message)
	return rsa.VerifyPKCS1v15(a.Key, crypto.SHA256, h[:], sig)
}

// Account owns an Address and signs Perun wire-authentication messages.
type Account struct {
	key  *rsa.PrivateKey
	addr *Address
}

func (a *Account) Address() wire.Address { return a.addr }
func (a *Account) Sign(message []byte) ([]byte, error) {
	h := sha256.Sum256(message)
	return rsa.SignPKCS1v15(rand.Reader, a.key, crypto.SHA256, h[:])
}

// Identity is a persistent account plus its public address.
type Identity struct {
	Account *Account
	Address *Address
}

// RegisterDecoder installs this address type for channel/wire decoding. The
// hub-demo uses lanwire addresses exclusively, so one global decoder is safe.
func RegisterDecoder() { wire.SetNewAddressFunc(func() wire.Address { return &Address{} }) }

// ParseAddress decodes an address previously returned by MarshalBinary.
func ParseAddress(data []byte) (*Address, error) {
	a := &Address{}
	return a, a.UnmarshalBinary(data)
}

// NewEphemeral creates an in-memory identity for the original single-process
// benchmark and tests. Distributed nodes use LoadOrCreate instead.
func NewEphemeral(name string) (*Identity, error) {
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, err
	}
	addr := &Address{Name: name, Key: &key.PublicKey}
	return &Identity{Account: &Account{key: key, addr: addr}, Address: addr}, nil
}

// LoadOrCreate loads an RSA wire identity or creates one under dir. The private
// key is intentionally local runtime state and must never be committed.
func LoadOrCreate(dir, name string) (*Identity, error) {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, "wire-key.pem")
	data, err := os.ReadFile(path)
	var key *rsa.PrivateKey
	if err == nil {
		block, _ := pem.Decode(data)
		if block == nil {
			return nil, fmt.Errorf("lanwire: invalid key file %s", path)
		}
		key, err = x509.ParsePKCS1PrivateKey(block.Bytes)
		if err != nil {
			return nil, err
		}
	} else if os.IsNotExist(err) {
		key, err = rsa.GenerateKey(rand.Reader, 2048)
		if err != nil {
			return nil, err
		}
		encoded := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
		if err := os.WriteFile(path, encoded, 0600); err != nil {
			return nil, err
		}
	} else {
		return nil, err
	}
	addr := &Address{Name: name, Key: &key.PublicKey}
	return &Identity{Account: &Account{key: key, addr: addr}, Address: addr}, nil
}
