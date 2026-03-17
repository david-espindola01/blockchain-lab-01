import hashlib, json, time, threading
from datetime import datetime, timezone
from config import DIFFICULTY, GENESIS_TIMESTAMP, GENESIS_NONCE


class Block:
    def __init__(self, index, transactions, previous_hash,
                 timestamp=None, nonce=0):
        self.index         = index
        self.timestamp     = timestamp or datetime.now(timezone.utc).isoformat()
        self.transactions  = transactions
        self.previous_hash = previous_hash
        self.nonce         = nonce
        self.hash          = self.calculate_hash()

    def calculate_hash(self) -> str:
        raw = json.dumps({
            "index":         self.index,
            "timestamp":     self.timestamp,
            "transactions":  self.transactions,
            "previous_hash": self.previous_hash,
            "nonce":         self.nonce,
        }, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def mine(self) -> dict:
        target = "0" * DIFFICULTY
        t0 = time.time()
        while not self.hash.startswith(target):
            self.nonce += 1
            self.hash   = self.calculate_hash()
        return {"attempts": self.nonce, "seconds": round(time.time() - t0, 4)}

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in
                ("index", "timestamp", "transactions",
                 "previous_hash", "nonce", "hash")}

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        b = cls(d["index"], d["transactions"],
                d["previous_hash"], d["timestamp"], d["nonce"])
        b.hash = d["hash"]
        return b


class Blockchain:
    def __init__(self):
        self._lock = threading.Lock()
        self.chain = [self._genesis()]

    def _genesis(self) -> Block:
        b = Block(
            index         = 0,
            transactions  = [{"message": "Genesis Block"}],
            previous_hash = "0" * 64,
            timestamp     = GENESIS_TIMESTAMP,
            nonce         = GENESIS_NONCE,
        )
        b.mine()
        return b

    @property
    def latest(self) -> Block:
        return self.chain[-1]

    def add_block(self, txs: list) -> tuple[Block, dict]:
        with self._lock:
            b    = Block(len(self.chain), txs, self.latest.hash)
            info = b.mine()
            self.chain.append(b)
        return b, info

    def is_valid(self, chain: list | None = None) -> tuple[bool, str]:
        chain  = chain or self.chain
        target = "0" * DIFFICULTY

        if chain[0].hash != self._genesis().hash:
            return False, "Incompatible genesis"

        for i, cur in enumerate(chain):
            if not cur.hash.startswith(target):
                return False, f"Block #{i}: does not meet difficulty"
            if cur.hash != cur.calculate_hash():
                return False, f"Block #{i}: corrupted hash"
            if i > 0 and cur.previous_hash != chain[i - 1].hash:
                return False, f"Block #{i}: broken link"

        return True, "Chain integrity verified"

    def to_list(self) -> list:
        return [b.to_dict() for b in self.chain]