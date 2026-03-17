import threading, argparse
from datetime import datetime, timezone
from flask import Flask, request, jsonify, abort

from config import log, GOSSIP_EVERY
from blockchain import Blockchain
from auth import _extract_and_verify, _signed_post, _get, my_ip, my_addr, split_secret, recover_secret
from p2p import peers, _lock_peers, register_remote_peer, start_threads


app = Flask(__name__)
app.config["IS_GENESIS"] = False

blockchain = Blockchain()

# Share SSS almacenado en este nodo
_my_share: dict | None = None
_lock_share = threading.Lock()

# Configuración del génesis (se asigna en __main__)
_original_message: str = ""
_total_nodes:      int = 0
_threshold:        int = 0

_distributed     = False
_lock_dist       = threading.Lock()


# ─── Distribución y reconstrucción ───────────────────────────────────────────

def _distribute() -> list[dict]:
    with _lock_peers:
        peer_list = sorted(peers)

    shares  = split_secret(_original_message, _total_nodes, _threshold)
    summary = []

    for i, peer in enumerate(peer_list):
        if i >= len(shares):
            summary.append({"peer": peer, "sent": False,
                            "reason": "more peers than shares"})
            continue

        share = shares[i]
        resp = _signed_post(f"http://{peer}/receive-share", {
            "share":     share,
            "index":     i,
            "total":     _total_nodes,
            "threshold": _threshold,
            "from":      my_addr(app),
        })

        if resp is not None:
            block, info = blockchain.add_block([{
                "type":        "share_distributed",
                "share_index": i,
                "total":       _total_nodes,
                "threshold":   _threshold,
                "destination": peer,
            }])
            summary.append({
                "peer":        peer,
                "sent":        True,
                "share_index": i,
                "block":       block.index,
                "hash":        block.hash[:20] + "...",
                "mined":       info,
            })
            log.info(f"[OK] share #{i} -> {peer}  (block #{block.index})")
        else:
            summary.append({"peer": peer, "sent": False,
                            "reason": "no response"})
            log.warning(f"[FAIL] no response from {peer}")

    return summary


def _reconstruct() -> dict:
    with _lock_peers:
        peer_list = sorted(peers)

    collected = []
    detail    = []

    for peer in peer_list:
        resp = _get(f"http://{peer}/share")
        if resp and resp.get("has_share"):
            share = resp.get("share")
            if share:
                collected.append(share)
                detail.append({
                    "peer":        peer,
                    "active":      True,
                    "share_index": resp.get("index"),
                })
                log.info(f"[OK] {peer} -> share #{resp.get('index')}")
            else:
                detail.append({"peer": peer, "active": False,
                                "reason": "empty share"})
        else:
            detail.append({"peer": peer, "active": False})
            log.warning(f"[MISS] {peer} -> no share")

    enough    = len(collected) >= _threshold
    recovered = None
    error     = None

    if enough:
        try:
            recovered = recover_secret(collected[:_threshold])
        except Exception as e:
            error   = str(e)
            enough  = False

    return {
        "complete":         enough,
        "recovered":        recovered,
        "error":            error,
        "shares_collected": len(collected),
        "threshold":        _threshold,
        "total_nodes":      _total_nodes,
        "progress":         round(len(collected) / _threshold * 100, 1) if _threshold else 0,
        "detail":           detail,
    }


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/status")
def status():
    valid, msg_val = blockchain.is_valid()
    with _lock_share:
        share_info = None
        if _my_share:
            share_info = {
                "index":     _my_share["index"],
                "total":     _my_share["total"],
                "threshold": _my_share["threshold"],
            }
    return jsonify({
        "node":         my_addr(app),
        "role":         "genesis" if app.config["IS_GENESIS"] else "receiver",
        "blocks":       len(blockchain.chain),
        "peers":        sorted(peers),
        "total_peers":  len(peers),
        "chain_valid":  valid,
        "validation":   msg_val,
        "latest_hash":  blockchain.latest.hash,
        "genesis_hash": blockchain.chain[0].hash,
        "difficulty":   3,
        "my_share":     share_info,
        "distributed":  _distributed,
        "threshold":    _threshold,
        "total_nodes":  _total_nodes,
    })


@app.get("/peers")
def view_peers():
    return jsonify({"peers": sorted(peers), "total": len(peers)})


@app.post("/peers")
def register_peer():
    data = request.get_json(force=True, silent=True) or {}
    data.pop("_sig", None)
    peer        = data.get("peer", "").strip()
    their_peers = data.get("peers", [])
    added = []
    with _lock_peers:
        if peer and peer != my_addr(app) and peer not in peers:
            peers.add(peer); added.append(peer)
        for p in their_peers:
            if p and p != my_addr(app) and p not in peers:
                peers.add(p); added.append(p)
    for p in added:
        threading.Thread(target=register_remote_peer,
                         args=(p, app), daemon=True).start()
    return jsonify({"peers": sorted(peers)}), (201 if added else 200)


@app.get("/chain")
def view_chain():
    return jsonify(blockchain.to_list())


@app.get("/chain/<int:idx>")
def view_block(idx):
    if idx < 0 or idx >= len(blockchain.chain):
        abort(404, description=f"Block #{idx} does not exist")
    return jsonify(blockchain.chain[idx].to_dict())


@app.get("/validate")
def validate():
    valid, msg_val = blockchain.is_valid()
    return jsonify({"valid": valid, "message": msg_val,
                    "blocks": len(blockchain.chain)}), (200 if valid else 409)


@app.post("/receive-share")
def receive_share():
    global _my_share

    data, err = _extract_and_verify()
    if err:
        abort(401, description=f"Auth error: {err}")

    share     = data.get("share", "").strip()
    index     = data.get("index")
    total     = int(data.get("total", 1))
    threshold = int(data.get("threshold", 1))
    sender    = data.get("from", "unknown")

    if not share or index is None:
        abort(400, description="Missing share or index")

    with _lock_share:
        _my_share = {
            "share":     share,
            "index":     index,
            "total":     total,
            "threshold": threshold,
            "from":      sender,
            "received":  datetime.now(timezone.utc).isoformat(),
        }

    block, info = blockchain.add_block([{
        "type":        "share_custody",
        "share_index": index,
        "total":       total,
        "threshold":   threshold,
        "from":        sender,
    }])

    log.info(
        f"Share #{index}/{total} (threshold={threshold}) received from {sender}"
        f" | block=#{block.index} ({info['seconds']}s)"
    )

    return jsonify({
        "accepted":    True,
        "share_index": index,
        "block":       block.index,
        "hash":        block.hash[:20] + "...",
    }), 201


@app.get("/share")
def view_share():
    with _lock_share:
        if _my_share is None:
            return jsonify({"has_share": False, "node": my_addr(app)}), 200
        return jsonify({
            "has_share": True,
            "node":      my_addr(app),
            "share":     _my_share["share"],
            "index":     _my_share["index"],
            "total":     _my_share["total"],
            "threshold": _my_share["threshold"],
            "received":  _my_share.get("received"),
        }), 200


@app.post("/distribute")
def distribute():
    global _distributed

    if not app.config["IS_GENESIS"]:
        abort(403, description="Only genesis node can distribute shares")

    if not peers:
        return jsonify({
            "error":  "No peers connected yet.",
            "advice": f"Start {_total_nodes} receiver nodes and wait for UDP discovery (~{GOSSIP_EVERY}s)",
        }), 400

    with _lock_dist:
        if _distributed:
            return jsonify({
                "error":  "Already distributed. Restart genesis to redistribute.",
                "advice": "Use GET /reconstruct to recover the message.",
            }), 409
        _distributed = True

    with _lock_peers:
        n_peers = len(peers)

    if n_peers < _threshold:
        with _lock_dist:
            _distributed = False
        return jsonify({
            "error": f"Need at least {_threshold} peers (threshold), only {n_peers} connected.",
        }), 400

    log.info(f"Distributing '{_original_message}' "
             f"(N={_total_nodes}, K={_threshold}) -> {n_peers} peers")

    summary = _distribute()
    sent    = [r for r in summary if r.get("sent")]

    return jsonify({
        "message":          "SSS distribution completed",
        "total_nodes":      _total_nodes,
        "threshold":        _threshold,
        "peers_contacted":  n_peers,
        "shares_delivered": len(sent),
        "fault_tolerance":  f"Up to {_total_nodes - _threshold} node(s) can fail",
        "detail":           summary,
    }), 201


@app.get("/reconstruct")
def reconstruct():
    if not app.config["IS_GENESIS"]:
        abort(403, description="Only genesis node can reconstruct the message")

    log.info(f"Reconstructing from {len(peers)} peers (need {_threshold})...")
    result = _reconstruct()

    if result["complete"]:
        log.info(f"[OK] Recovered: '{result['recovered']}'")
    else:
        log.warning(
            f"[WARN] Only {result['shares_collected']}/{_threshold} shares available"
        )

    return jsonify(result)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Blockchain P2P con Shamir's Secret Sharing"
    )
    parser.add_argument("--port",      type=int, default=5000)
    parser.add_argument("--genesis",   action="store_true",
                        help="Iniciar como nodo génesis")
    parser.add_argument("--message",   type=str,
                        default="La blockchain distribuye informacion entre nodos",
                        help="Mensaje a distribuir (solo génesis)")
    parser.add_argument("--nodes",     type=int, default=3,
                        help="N: número total de nodos receptores")
    parser.add_argument("--threshold", type=int, default=2,
                        help="K: mínimo de shares para reconstruir (K <= N)")
    parser.add_argument("--host",      type=str, default=None,
                        help="Override de IP local")
    args = parser.parse_args()

    if args.genesis and args.threshold > args.nodes:
        parser.error(f"--threshold ({args.threshold}) no puede ser mayor que --nodes ({args.nodes})")

    app.config["PORT"]       = args.port
    app.config["IS_GENESIS"] = args.genesis
    if args.host:
        app.config["HOST_OVERRIDE"] = args.host

    _original_message = args.message
    _total_nodes      = args.nodes
    _threshold        = args.threshold

    role = "GENESIS" if app.config["IS_GENESIS"] else "RECEIVER"

    if app.config["IS_GENESIS"]:
        log.info(f"Message   : '{_original_message}'")
        log.info(f"Scheme    : {_threshold}-of-{_total_nodes} Shamir's Secret Sharing")
        log.info(f"When {_total_nodes} nodes connect, run:")
        log.info(f"  curl -X POST http://{my_ip()}:{args.port}/distribute")
        log.info(f"  curl         http://{my_ip()}:{args.port}/reconstruct")
    else:
        log.info("Waiting for SSS share from genesis node...")
        log.info(f"  curl http://{my_ip()}:{args.port}/share")

    threading.Timer(1.0, lambda: start_threads(app)).start()
    app.run(host="0.0.0.0", port=args.port, debug=False)