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
            updates = defaultdict(list)
            updates['remove'].append(testbed.nodename)  # node name should be from returned data.
            updates['add'] = self._clickGraph.get_click_topology()

            return dict(updates)

    def get_node_types(self):
        '''Return the type(s) of node this is as a list.'''
        ret_val = []
        p = path.join('/', 'click')   # support for Click on Windows!!!
        if path.exists(p):  # /click is a dir if in kernel mode, else a UNIX socket!
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
            cmd = 'ip route get {}'.format(addr)
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
                    dst_addr, _, next_hop_addr, _, dev, _, src_addr, _, _ = route
                else:
                    dst_addr, _, next_hop_addr, _, dev, _, src_addr = route  # no realm specified.
            else:
                if cmdout.startswith('local'):
                    _, dst_addr, _, dev, _, src_addr, = cmdout.split()
                else:
                    dst_addr, _, dev, _, src_addr, = cmdout.split()
                next_hop_addr = dst_addr    # same subnet, so next hop is direct to dst.

            dst_aliases = socket.gethostbyaddr(dst_addr)
            dst_link = dst_aliases[0]
            dst_name = dst_aliases[1][-1]   # GTL very DETER specific. VERY.
            dst_name = dst_name if '-' not in dst_name else dst_name.split('-')[0]

            src_aliases = socket.gethostbyaddr(src_addr)
            src_link = src_aliases[0]
            src_name = src_aliases[1][-1]   # GTL very DETER specific. VERY.
            src_name = src_name if '-' not in src_name else src_name.split('-')[0]

            if next_hop_addr:
                nh_aliases = socket.gethostbyaddr(next_hop_addr)
                nh_link = nh_aliases[0]
                nh_name = nh_aliases[1][-1]   # GTL very DETER specific. VERY.
                nh_name = nh_name if '-' not in nh_name else nh_name.split('-')[0]
            else:
                nh_name = None
                nh_link = None

            log.debug('p2p route found: {}/{}/{} --> {}/{}/{}'.format(
                src_addr, src_name, src_link, dst_addr, dst_name, dst_link))
            routes.append({
                'next_hop_addr': next_hop_addr,
                'next_hop_link': nh_link,
                'next_hop_name': nh_name,
                'dst_addr': dst_addr,       # dst addr
                'dst_name': dst_name,       # canonical DETER host name, aka the node name.
                'dst_link': dst_link,       # The DETER name for the address on that interface.
                'src_addr': src_addr, 
                'src_name': src_name,
                'src_link': src_link,
                'src_iface': dev,
            })

        return {testbed.nodename: routes}

    def get_network_edges(self):
        if self._clickGraph:
            return self._clickGraph.get_network_edge_map()

        return None

    def init_visualization(self, dash, agent):
        if self._clickGraph:
            self._clickGraph.init_visualization(dash, agent)
    
    def insert_stats(self, collection):
        if self._clickGraph:
            self._clickGraph.insert_stats(collection)

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
    print('network edge map')
    pprint.pprint(rd.get_network_edges())

    exit(0)
