#!/usr/bin/env python3
"""Public TCP listener: copies bytes between internet clients and the web Unix socket.

It parses nothing. The listening socket opens first, then seccomp forbids creating
any IP socket, so this process can accept connections but never initiate one.
"""
import argparse
import asyncio
import logging
import socket

import sandbox

MAX_CONNECTIONS = 256
MAX_PER_CLIENT = 32


async def pump(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except (ConnectionError, OSError):
            pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--listen", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--upstream", required=True)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s edge %(levelname)s %(message)s")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.listen, args.port))
    listener.listen(128)
    listener.setblocking(False)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    sandbox.lock_network(allow_unix_connect=True, allow_listen=True)
    logging.info("listening on %s:%d; outbound IP sockets are blocked", args.listen, args.port)

    active = {}

    async def handle(reader, writer):
        client = (writer.get_extra_info("peername") or ("?",))[0]
        if sum(active.values()) >= MAX_CONNECTIONS or active.get(client, 0) >= MAX_PER_CLIENT:
            writer.close()
            return
        active[client] = active.get(client, 0) + 1
        try:
            up_reader, up_writer = await asyncio.open_unix_connection(args.upstream)
            await asyncio.gather(pump(reader, up_writer), pump(up_reader, writer))
        except OSError:
            writer.close()
        finally:
            active[client] -= 1
            if not active[client]:
                del active[client]

    loop.run_until_complete(asyncio.start_server(handle, sock=listener))
    loop.run_forever()


if __name__ == "__main__":
    main()
