#!/usr/bin/env python

import logging
import re
import socket
from collections import defaultdict
from os import path
from netaddr import IPNetwork, IPAddress
from netaddr.core import AddrFormatError

from magi.testbed import testbed
from magi.util.execl import execAndRead
from click_graph import ClickGraph, ClickGraphException

log = logging.getLogger(__name__)

class RouteDataException(Exception):
    pass

class RouteData(object):
    def __init__(self):
        super(RouteData, self).__init__()
        # control nets *should* be set externally to this file.
        self.control_nets = [
            IPNetwork('192.168.0.0', '255.255.0.0'),
            IPNetwork('172.16.0.0', '255.240.0.0')
        ]
        self._clickGraph = None if not 'click' in self.get_node_types() else ClickGraph()
        self._known_hosts = self._get_known_hosts()

        if self._clickGraph:
            self._clickGraph.set_known_hosts(self._known_hosts)

    def get_route_tables(self):
        '''Return all route tables this node knows about. For physical nodes, this is one table. For Click
        nodes this will be all the click router tables on this node. Return format is a dict:
        
        {
            hostname: [{
                'dst': destination
                'netmask': net mask
                'gw': ip address of gateway if there is one
                'iface': if available (may not be in click)
                }, 
                ... 
            ]},
            hostname:
                ....
        }
        '''
        if self._clickGraph:
            tables = {}
            try:
                tables = self._clickGraph.get_route_tables()
            except ClickGraphException as e:
                raise RouteDataException(e)

            return tables

        return {testbed.nodename: self._get_rt_std()}   # just a single table for physical nodes.

    def get_point2point(self):
        if self._clickGraph:
            return self._clickGraph.get_point2point()

        return self._get_p2p_std()

    def get_topology_updates(self):
        if self._clickGraph:
            p2p = self._clickGraph.get_point2point()
            updates = defaultdict(list)
            updates['remove'].append(testbed.nodename)
            for host, entries in p2p.iteritems():
                for entry in entries:
                    if entry['next_hop'] and (host, entry['next_hop']) not in updates['add']:
                        updates['add'].append((host, entry['next_hop']))

            return updates

    def get_node_types(self):
        '''Return the type(s) of node this is as a list.'''
        ret_val = []
        p = path.join('/', 'click')   # support for Click on Windows!!!
        if path.exists(p) and path.isdir(p):
            ret_val.append('click')

        p = path.join('/', 'var', 'containers')
        if path.exists(p) and path.isdir(p):
            ret_val.append('container')

        if not 'container' in ret_val:
            ret_val.append('physical')    # Hmm. 

        return ret_val

    def _is_datanet(self, addr):
        if addr.is_multicast() or addr.is_loopback():
            log.debug('ignoring multicast address {}'.format(addr))
            return True

        return any(addr in n for n in self.control_nets)    # True if addr is in any of the networks.

    def _get_rt_std(self):
        '''return a list of 4-tuples of dst, mask, gw, iface for each route found in the route table.'''
        table = []
        cmd = 'netstat -rn'    # I believe this is the most portable way to get the routing table. Is this right?
        sout, serr = execAndRead(cmd)
        log.debug('netstat -rn stdout:\n{}'.format(sout))
        if sout:
            # example output:
            #   Destination     Gateway         Genmask         Flags   MSS Window  irtt Iface
            #   0.0.0.0         192.168.1.254   0.0.0.0         UG        0 0          0 eth0
            #   10.1.1.0        0.0.0.0         255.255.255.0   U         0 0          0 eth5
            #   192.168.0.0     0.0.0.0         255.255.252.0   U         0 0          0 eth0 
            for line in sout.split('\n'):
                m = re.search('^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.'
                              '\d{1,3})\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+.+\s+(\w+)$', line)
                if m:
                    dst, gw, netmask, iface = m.group(1, 2, 3, 4)
                    if not self._is_datanet(IPNetwork(dst, netmask)):
                        log.debug('Found route: {}: {}/{}/{}'.format(dst, gw, netmask, iface))
                        table.append({
                            'dst': dst,
                            'netmask': netmask,
                            'gw': gw,
                            'iface': iface})

        return table

    def _get_known_hosts(self):
        lines = []
        hosts = []
        with open(path.join('/', 'etc', 'hosts')) as fd:
            lines = [l.strip() for l in fd.readlines()]

        if not lines:
            return []

        for line in lines:
            log.debug('reading line: {}'.format(line))
            m = re.match('(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})', line)
            if not m:
                continue
            
            try:
                addr = IPAddress(m.group(1))
            except AddrFormatError:
                log.warn('Bad format in /etc/hosts: {}'.format(m.group(1)))
                continue

            if self._is_datanet(addr):
                # skip control addresses, we only care about data routes.
                continue
            
            hosts.append(addr)

        return hosts

    def _get_p2p_std(self):
        # this is a single node, so there is only one entry in the table.
        routes = []
        for addr in self._known_hosts:
            cmd = 'ip -r route get {}'.format(addr)
            cmdout, cmderr = execAndRead(cmd)

            if not cmdout:
                continue

            # passible output format from ip get route:
            # local net:
            #       10.0.2.2 dev eth2  src 10.0.2.1
            #       local 10.0.2.1 dev lo  src 10.0.2.1
            # remote net:
            #       10.0.5.2 via 10.0.4.2 dev eth1  src 10.0.4.1 realm 1

            # we trust the output of route for some stupid reason, like I'm lazy.
            cmdout = cmdout.splitlines()[0]
            log.debug('parsing cmd out: {}'.format(cmdout))
            if 'via' in cmdout:
                route = cmdout.split()
                if len(route) == 9:
                    dst, _, next_hop, _, dev, _, src, _, _ = route
                else:
                    dst, _, next_hop, _, dev, _, src = route  # no realm specified.
            else:
                if cmdout.startswith('local'):
                    _, dst, _, dev, _, src, = cmdout.split()
                else:
                    dst, _, dev, _, src, = cmdout.split()
                next_hop = None

            log.debug('route found: {} --> {} via {}'.format(src, dst, next_hop))
            routes.append({'dst': dst, 'iface': dev, 'src': src, 'next_hop': next_hop})

        return {testbed.nodename: routes}


if __name__ == "__main__":
    from sys import argv
    import pprint

    if '-v' in argv:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    rd = RouteData()
    print('routes:')
    pprint.pprint(rd.get_route_tables())
    print('point to point tables:')
    pprint.pprint(rd.get_point2point())
    print('topo updates:')
    pprint.pprint(rd.get_topology_updates())

    exit(0)
