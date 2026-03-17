import socket, threading, time
from config import log, UDP_PORT, GOSSIP_EVERY, BROADCAST_IP, MAGIC
from auth import _signed_post, my_addr


# ─── Estado compartido de peers ───────────────────────────────────────────────
peers: set       = set()
_lock_peers      = threading.Lock()


def register_remote_peer(peer: str, app):
    resp = _signed_post(f"http://{peer}/peers",
                        {"peer": my_addr(app), "peers": sorted(peers)})
    if resp:
        new_peers = []
        with _lock_peers:
            for p in resp.get("peers", []):
                if p != my_addr(app) and p not in peers:
                    peers.add(p); new_peers.append(p)
        for p in new_peers:
            threading.Thread(target=register_remote_peer,
                             args=(p, app), daemon=True).start()
        return True
    return False


def _udp_announce(app):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    while True:
        try:
            sock.sendto(f"{MAGIC}:{my_addr(app)}".encode(), (BROADCAST_IP, UDP_PORT))
        except Exception:
            pass
        time.sleep(GOSSIP_EVERY)


def _udp_listen(app):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", UDP_PORT))
    except OSError:
        log.warning(f"UDP port {UDP_PORT} in use — UDP discovery disabled")
        return
    log.info(f"Listening UDP broadcast on port {UDP_PORT}")
    while True:
        try:
            data, _ = sock.recvfrom(256)
            msg = data.decode().strip()
            if not msg.startswith(MAGIC + ":"): continue
            peer_addr = msg[len(MAGIC) + 1:]
            if peer_addr == my_addr(app): continue
            with _lock_peers:
                known = peer_addr in peers
            if not known:
                log.info(f"Node discovered via UDP: {peer_addr}")
                with _lock_peers:
                    peers.add(peer_addr)
                threading.Thread(target=register_remote_peer,
                                 args=(peer_addr, app), daemon=True).start()
        except Exception:
            pass


def _gossip_loop(app):
    time.sleep(10)
    while True:
        with _lock_peers:
            snapshot = list(peers)
        for peer in snapshot:
            resp = _signed_post(f"http://{peer}/peers",
                                {"peer": my_addr(app), "peers": sorted(peers)})
            if resp:
                with _lock_peers:
                    for p in resp.get("peers", []):
                        if p != my_addr(app) and p not in peers:
                            peers.add(p)
                            log.info(f"Gossip: new peer: {p}")
        time.sleep(GOSSIP_EVERY)


def start_threads(app):
    threading.Thread(target=_udp_listen,   args=(app,), daemon=True).start()
    threading.Thread(target=_udp_announce, args=(app,), daemon=True).start()
    threading.Thread(target=_gossip_loop,  args=(app,), daemon=True).start()
    log.info("UDP broadcast active — searching for nodes on LAN...")
    log.info(f"Gossip active (every {GOSSIP_EVERY}s)")