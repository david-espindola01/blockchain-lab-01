import logging
from datetime import datetime, timezone
import secrets

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bc_node")
logging.getLogger("werkzeug").setLevel(logging.ERROR)

# ─── Constantes de red ────────────────────────────────────────────────────────
DIFFICULTY   = 3
UDP_PORT     = 6000
GOSSIP_EVERY = 30
BROADCAST_IP = "255.255.255.255"
MAGIC        = "BC_NODE_V1"

# ─── Constantes del bloque génesis ───────────────────────────────────────────
GENESIS_TIMESTAMP = datetime.now(timezone.utc).isoformat()
GENESIS_NONCE     = 0

# ─── Seguridad HMAC ───────────────────────────────────────────────────────────
# Clave compartida HMAC entre nodos (cámbiala en producción)


SHARED_SECRET = secrets.token_bytes(32)
