#!/usr/bin/env python3
"""Manages the router's port-forward rule for MaxCamera Web through UPnP.

  router-forward.py check            read-only: router, WAN address, existing rules
  router-forward.py add [--port 80]  adds external TCP port -> this computer:8080
  router-forward.py remove [--port 80]

Only rules whose description is "MaxCamera Web" are ever removed by this tool.
"""
import argparse
import re
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

INTERNAL_PORT = 8080
DESCRIPTION = "MaxCamera Web"
SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:2",
            "urn:schemas-upnp-org:service:WANIPConnection:1",
            "urn:schemas-upnp-org:service:WANPPPConnection:1")


def discover():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    search = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: \"ssdp:discover\"\r\nMX: 2\r\n"
              "ST: urn:schemas-upnp-org:device:InternetGatewayDevice:1\r\n\r\n").encode()
    sock.sendto(search, ("239.255.255.250", 1900))
    try:
        while True:
            data, _ = sock.recvfrom(4096)
            match = re.search(rb"(?im)^location:\s*(\S+)", data)
            if match:
                return match.group(1).decode()
    except socket.timeout:
        return None
    finally:
        sock.close()


def find_service(location):
    root = ET.fromstring(urllib.request.urlopen(location, timeout=6).read())
    found = {}
    for service in root.iter():
        if service.tag.split("}")[-1] != "service":
            continue
        fields = {c.tag.split("}")[-1]: (c.text or "").strip() for c in service}
        found[fields.get("serviceType")] = fields.get("controlURL")
    for kind in SERVICES:
        if found.get(kind):
            return kind, urllib.parse.urljoin(location, found[kind])
    return None, None


class Upnp(Exception):
    pass


def soap(url, kind, action, **args):
    body = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in args.items())
    envelope = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{kind}">{body}</u:{action}></s:Body></s:Envelope>').encode()
    request = urllib.request.Request(url, envelope, {"Content-Type": 'text/xml; charset="utf-8"',
                                                     "SOAPAction": f'"{kind}#{action}"'})
    try:
        reply = urllib.request.urlopen(request, timeout=6).read()
    except urllib.error.HTTPError as error:
        text = error.read().decode("utf-8", "replace")
        code = re.search(r"<errorCode>(\d+)</errorCode>", text)
        name = re.search(r"<errorDescription>([^<]*)</errorDescription>", text)
        raise Upnp(f"{code.group(1) if code else error.code} {name.group(1) if name else ''}".strip())
    return {e.tag.split("}")[-1]: (e.text or "") for e in ET.fromstring(reply).iter()}


def mappings(url, kind):
    out = []
    for index in range(64):
        try:
            out.append(soap(url, kind, "GetGenericPortMappingEntry", NewPortMappingIndex=index))
        except Upnp:
            break
    return out


def local_ip(router_host):
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.connect((router_host, 9))
    address = probe.getsockname()[0]
    probe.close()
    return address


def is_private(ip):
    a, b = (int(x) for x in ip.split(".")[:2])
    return a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168) or (a == 100 and 64 <= b <= 127)


def show(rule):
    return (f"  {rule.get('NewProtocol')} external {rule.get('NewExternalPort')} -> "
            f"{rule.get('NewInternalClient')}:{rule.get('NewInternalPort')}  ({rule.get('NewPortMappingDescription')})")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["check", "add", "remove"])
    parser.add_argument("--port", type=int, default=80, help="external TCP port (default 80)")
    args = parser.parse_args()

    location = discover()
    if not location:
        sys.exit("The router did not answer UPnP discovery. Turn on UPnP on the router, or add the rule by hand.")
    kind, url = find_service(location)
    if not url:
        sys.exit(f"The router at {location} has no port-mapping service. Add the rule by hand.")
    router = urllib.parse.urlsplit(location).hostname
    host = local_ip(router)
    print(f"Router {router}, service {kind.split(':')[-2]}, this computer {host}")

    try:
        wan = soap(url, kind, "GetExternalIPAddress").get("NewExternalIPAddress", "")
        print(f"Router's internet address: {wan or 'unknown'}")
        if wan and is_private(wan):
            print("This is a private address: the provider uses carrier-grade NAT, and port forwarding "
                  "cannot reach this router from the internet. Use the HTTPS tunnel from the user guide.")
    except Upnp as error:
        wan = ""
        print(f"The router did not report its internet address ({error})")

    if args.action == "add":
        try:
            soap(url, kind, "AddPortMapping", NewRemoteHost="", NewExternalPort=args.port, NewProtocol="TCP",
                 NewInternalPort=INTERNAL_PORT, NewInternalClient=host, NewEnabled=1,
                 NewPortMappingDescription=DESCRIPTION, NewLeaseDuration=0)
        except Upnp as error:
            sys.exit(f"The router refused the rule: {error}\nTry another port, for example: --port 8088")
        suffix = "" if args.port == 80 else f":{args.port}"
        print(f"Added: external TCP {args.port} -> {host}:{INTERNAL_PORT}")
        print(f"Address: http://{wan or '<your public IP>'}{suffix}/")
        print("Test it from outside this network, for example from a phone on mobile data.")
    elif args.action == "remove":
        current = [r for r in mappings(url, kind) if r.get("NewExternalPort") == str(args.port)
                   and r.get("NewProtocol") == "TCP"]
        if not current:
            print(f"No TCP rule on external port {args.port}.")
        elif current[0].get("NewPortMappingDescription") != DESCRIPTION:
            sys.exit(f"The rule on port {args.port} belongs to something else; left in place:\n{show(current[0])}")
        else:
            soap(url, kind, "DeletePortMapping", NewRemoteHost="", NewExternalPort=args.port, NewProtocol="TCP")
            print(f"Removed the rule on external port {args.port}.")

    rules = mappings(url, kind)
    print(f"Port-forward rules visible through UPnP: {len(rules)}")
    for rule in rules:
        print(show(rule))


if __name__ == "__main__":
    main()
