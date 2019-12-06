#!/usr/bin/env python

# Copyright (c) 2019 Cable Television Laboratories, Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import argparse
import logging
import random
import socket
import time
from logging import getLogger, basicConfig
from time import sleep

from scapy.all import get_if_list, get_if_hwaddr
from scapy.layers.inet import IP, UDP, TCP
from scapy.layers.l2 import Ether
from scapy.sendrecv import sendp

# Logger stuff
logger = getLogger('send_packets')

FORMAT = '%(levelname)s %(asctime)-15s %(filename)s %(message)s'


def get_first_if():
    for iface in get_if_list():
        if iface != 'lo':
            return iface
    raise Exception('No NIC to send packets to')


def get_args():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-d', '--continuous',
        help='When true, count disregarded and packets will send indefinitely',
        type=bool, required=False, default=False)
    parser.add_argument(
        '-c', '--count', help='Number of packets to send for each burst',
        type=int, default=1, required=False)
    parser.add_argument(
        '-i', '--interval',
        help='How often to send packets in seconds (Default = 1)',
        type=float, required=False, default=1)
    parser.add_argument(
        '-y', '--delay', help='Delay before starting run (Default = 0',
        type=int, required=False, default=0)
    parser.add_argument(
        '-r', '--destination', help='Destination IPv4 address', required=True)
    parser.add_argument(
        '-e', '--src-mac', help='Src MAC Address', required=False,
        default=None)
    parser.add_argument(
        '-sa', '--source-addr', help='Source IP Address', required=False,
        default=None)
    parser.add_argument(
        '-sp', '--source-port', type=int, default=random.randint(49152, 65535),
        help='Source port else it will be random')
    parser.add_argument(
        '-p', '--port', help='Destination port', type=int, required=True)
    parser.add_argument('-m', '--msg', help='Message to send', required=True)
    parser.add_argument(
        '-l', '--loglevel', required=False, default='INFO',
        help='Log Level <DEBUG|INFO|WARNING|ERROR> defaults to INFO')
    parser.add_argument(
        '-f', '--logfile', help='File to log to defaults to console',
        required=False, default=None)
    parser.add_argument(
        '-z', '--interface', required=False, default=None,
        help='Linux named ethernet device. Defaults to first one found')
    parser.add_argument(
        '-s', '--switch_ethernet',
        help='Switch Ethernet Interface. Defaults to ff:ff:ff:ff:ff:ff',
        required=False, default='ff:ff:ff:ff:ff:ff')
    parser.add_argument(
        '-it', '--iterations',
        help='Number of iterations of packet groups to send',
        required=False, default=1, type=int)
    parser.add_argument(
        '-itd', '--iter-delay',
        help='Seconds betweein iterations of packet groups to be sent',
        required=False, default=1, type=int)
    parser.add_argument('-t', '--tcp', dest='tcp', required=False)
    args = parser.parse_args()
    return args


def device_send(args):
    addr = socket.gethostbyname(args.destination)

    interface = args.interface
    if not interface:
        interface = get_first_if()
    logger.info('Delaying %d seconds' % args.delay)
    sleep(args.delay)

    src_mac = args.src_mac
    if not src_mac:
        src_mac = get_if_hwaddr(interface)

    pkt = Ether(src=src_mac, dst=args.switch_ethernet) / IP(
        dst=addr, src=args.source_addr)
    if args.tcp:
        pkt = pkt / TCP(dport=args.port, sport=args.source_port) / args.msg
    else:
        pkt = pkt / UDP(dport=args.port, sport=args.source_port) / args.msg

    pkt.show2()

    if args.continuous:
        logger.info('Sending a packet to %s every %s',
                    interface, args.interval)
        sendp(pkt, iface=interface, verbose=2, inter=args.interval, loop=1)
    else:
        logger.info('Starting iter loop for iterations %s', args.iterations)
        for i in range(0, args.iterations):
            logger.info('Iteration %s', i)
            logger.info('Sending %s packets to %s every %s',
                        args.count, interface, args.interval)
            sendp(pkt, iface=interface, verbose=2, count=args.count,
                  inter=args.interval)
            time.sleep(args.iter_delay)

    logger.info('Done')
    return


if __name__ == '__main__':
    cmd_args = get_args()
    numeric_level = getattr(logging, cmd_args.loglevel.upper(), None)
    basicConfig(format=FORMAT, level=numeric_level, filename=cmd_args.logfile)
    logger.info('Starting Send with args - [%s]', cmd_args)
    device_send(cmd_args)