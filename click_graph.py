#!/usr/bin/env python

# Copyright (C) 2015 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

import os
import os.path
import re
import struct
import socket
from collections import defaultdict

from netaddr import IPNetwork, IPAddress

import networkx as nx
import netifaces as ni
from networkx.readwrite import json_graph

from magi.testbed import testbed   # only used for phy node name. If possible, remove this dependency.

import logging

log = logging.getLogger(__name__)

class ClickGraphException(Exception):
    pass

class ClickNode(object):
    def __init__(self):
        super(ClickNode, self).__init__()
        self.name = None
        self.node_class = None
        self.config = []
        self.table = []

    def parse(self, attr, lines):
        '''Given a list of text lines and an attribute name, parse and set teh attribute on this instance. 
        The lines passed are from files found in /click/*/.'''
        if attr == 'name':
            self.name = lines[0]
        elif attr == 'class':
            self.node_class = lines[0]
        elif attr == 'config':
            # config is a list of strings that were separated by newlines and commas.
            self.config = [token.strip() for token in ''.join(lines).split(',') if token]
        elif attr == 'table':
           self._parse_table(lines)
        else:
            raise ClickGraphException('bad attr given to ClickNode: {}'.format(attr))

    def _parse_table(self, lines):
        # lines are of the form:
        # 10.1.10.2/32            -               3
        # addr/net  gw  port
        for l in lines:
            tokens = l.split()
            if len(tokens) == 3:
                net, gw, port = tokens
                gw = IPAddress(gw) if gw != '-' else None
                net = IPNetwork(net)
                self.table.append({'dst': net, 'gw': gw, 'port': port, 'link': '{}-{}'.format(self.name, port)})

    def __repr__(self):
        return '{}/{}'.format(self.name, self.node_class)


class ClickGraph(object):
    """
    Represent the topology graph and various bits. Read the live /click dir for initial graph configuration and
    live updates. 
    """
    def __init__(self, confdir='/click'):
        super(ClickGraph, self).__init__()

        self._confdir = confdir                 # The /click dir which contains live info.

        # These classes define which nodes are routers and other types of nodes in teh click graph.
        self._router_class = 'RadixIPLookup'    # anything that has this class is a leaf in the graph.
        self._physical_class = 'ToDevice'       # anything of this class is a link to a NIC and a physical node.
        self._localhost_class = 'ToHost'        # anything of this class points to the localhost.

        self._click_graph = nx.DiGraph(name='click')    # This is the "full" click graph built from /click.
        self._build_graph_from_filesystem()

        self._router_graph = nx.DiGraph(name='routers') # This is the router + physical node subgraph.
        self._build_router_graph_from_click_graph()     # the router graph is built from the existing /click graph.

        self._known_hosts = None

    def set_known_hosts(self, kh):
        self._known_hosts = kh

    def get_route_tables(self):
        '''Return the current routing tables in the expected format which is 
        a router name indexed dictionary of a dicts of dst, mask, gw, and iface, one
        entry per route.'''
        tables = {}
        for n in self._router_graph.nodes():
            rt = self._router_graph.node[n]['data'].table
            if rt:              # physical nodes don't have routing tables, but are in the graph.
                routes = []
                for route in rt:
                    gwaddr = None if not route['gw'] else str(route['gw'])
                    routes.append({
                        'dst': str(route['dst']),
                        'netmask': str(route['dst'].netmask),
                        'gw': gwaddr,
                        'iface': route['link']
                    })

                tables[n] = routes

        return tables

    def get_point2point(self, known_hosts=None):
        '''
            The return value is a dict of dicts indexed by the hostname for all hosts this node knows about.
            In the case of a click router node, this will be an entry for each "router" node that exists
            on the physcical node. For non-click nodes (physical or container), there will be on entry for the
            node itself. 

            The dict for each node will be a list of dicts point to point entries. The key/value pairs are:

                dst, iface, src, and next_hop

            For click routers the iface is the click port prepended with node name. Need to call it something.
            
            The next_nop may be empty (currently) if the next hop is into a container pnode. i.e. this agent
            does not understand pnode->containers routing. The agent does understand container->container and 
            container->pnode routing though so all the bits are there, but asymmetric in certain cases.

            If known_hosts is given, ClickGraph will use that list to find the dst point, else the
            list set via ClickGraph.set_known_hosts() will be used.
        '''
        if not known_hosts:
            known_hosts = self._known_hosts

        tables = defaultdict(list)
        for dst_addr in known_hosts:
            for node in self._router_graph.nodes():
                route_table = self._router_graph.node[node]['data'].table
                possible_routes = []
                for route in route_table:
                    if dst_addr in route['dst']:   # is this address on this route?
                        possible_routes.append(route)

                if possible_routes:
                    route = max(possible_routes)    # find the narrowest route that fits the address.
                    next_hop_link, next_hop_name, next_hop_addr = None, None, None
                    for nbr, edge_data in self._router_graph[node].iteritems():
                        if edge_data['to'] == route['link']:
                            next_hop_link = edge_data['frm']
                            next_hop_name = nbr
                            next_hop_addr = None
                            break

                    # p2p table uses link names. (hostnames which ID a link/iface.)
                    # next_hop = self._router_graph[node][nbr]['frm']

                    dst_aliases = socket.gethostbyaddr(str(dst_addr))  # dst_addr is never a click router
                    dst_link = dst_aliases[0]
                    dst_name = dst_aliases[1][-1]   # GTL very DETER specific. VERY.
                    dst_name = dst_name if '-' not in dst_name else dst_name.split('-')[0]

                    src_addr = str(route['dst'].ip)   # The "address" of this interface.
                    src_name = node
                    src_link = '{}-{}'.format(node, route['port'])  # fake but unique link name

                    log.debug('p2p route found: {}/{}/{} --> {}/{}/{}'.format(
                        src_addr, src_name, src_link, dst_addr, dst_name, dst_link))
                    tables[node].append({
                        'next_hop_addr': next_hop_addr,
                        'next_hop_link': next_hop_link,
                        'next_hop_name': next_hop_name,
                        'dst_addr': str(dst_addr),  # dst addr
                        'dst_name': dst_name,       # canonical DETER host name, aka the node name.
                        'dst_link': dst_link,       # The DETER name for the address on that interface.
                        'src_addr': src_addr, 
                        'src_name': src_name,
                        'src_link': src_link,
                        'src_iface': src_link,
                    })

        return dict(tables)

    def get_click_topology(self):
        '''Return a list of neighbor tuples this node knows about. Format [(host1, host2), ... ].'''
        return [(a, b) for a, b in self._router_graph.edges()]

    def get_network_edge_map(self):
        '''Return a node mapping of phy nodes to virtual routers. The map is phy node indexed dict of
        three tuples: (phy node link name, router name, click node name).'''
        network_map = {}
        for node in self._router_graph.nodes():
            if self._router_graph.node[node]['data'].node_class == self._physical_class: # phy node, add the links.
                log.debug('adding {} edges to network edge map.'.format(node))
                network_map[node] = []
                for nbr, edge_data in self._router_graph[node].iteritems():
                    network_map[node].append({
                        'to_link': edge_data['to'], 
                        'nbr': nbr,
                        'nbr_host': testbed.nodename})

        return network_map


    def _build_router_graph_from_click_graph(self):
        for node in self._click_graph.nodes():
            data = self._click_graph.node[node]['data']
            if data.node_class == self._router_class:
                self._router_graph.add_node(node, data=data)
                log.debug('added router node: {}'.format(node))
                for nbr in self._get_router_nbrs(node):
                    if nbr['name'] not in self._click_graph:
                        # This is a physical node. Add it "by hand" as it's not a click node as click knows
                        # nothing about it really.
                        cn = ClickNode()
                        cn.name = nbr['name']
                        cn.node_class = self._physical_class
                        self._router_graph.add_node(nbr['name'], data=cn)
                        self._router_graph.add_edge(nbr['name'], node, frm=nbr['to'], to=nbr['frm'])
                        log.debug('added phy node: {}'.format(nbr['name']))
                   
                    # add the edge between these neighbors, keeping track of the 
                    # port over which they are connected. This is the port from 
                    # node to nbr. (The graph is not symmetric.) 
                    log.debug('added edge: {} --> {}'.format(node, nbr['name']))
                    self._router_graph.add_edge(node, nbr['name'], frm=nbr['frm'], to=nbr['to'])

    def _findNeighbor(self, ifaddr, mask):
        '''
        Map IP addresses to neighbors (based on /etc/hosts)
        '''
        with open(os.path.join('/', 'etc', 'hosts')) as hosts:
            net = IPNetwork('{}/{}'.format(ifaddr, mask))
            for host in hosts:
                tokens = host.strip("\n").split()
                if len(tokens) > 1:
                    if(tokens[0] != str(ifaddr)):
                        nbr_addr = IPAddress(tokens[0])
                        if nbr_addr in net:
                            names = socket.gethostbyaddr(str(nbr_addr))
                            # GTL very DETER specific. Last alias is "real name". 
                            # GTL - depending how DETER assigns names to ifaces, we still may not have 
                            # the "canonical" DETER name here. 
                            name = names[1][-1] if not '-' in names[1][-1] else names[1][-1].split('-')[0]
                            return name, names[0]   
        return None

    def _mapInterfaceToNeighbor(self, iface):
        '''
        Map Interface names to Neighbors. Return hostname, linkname tuple of neighbor given an ip address. 
        '''
        addrs = ni.ifaddresses(iface)
        addr = addrs[ni.AF_INET][0]['addr']
        mask = addrs[ni.AF_INET][0]['netmask']
        return self._findNeighbor(addr, mask)

    def _build_graph_from_filesystem(self):
        ''' Parse the /click directory to build the unconnected nodes.'''

        if not os.path.isdir(self._confdir):
            log.critical('No such configuration directory {}'.format(self._confdir))
            raise ClickGraphException('{} is not there. Unable to do anything.'.format(self._confdir))
     
        # grab the top level dir and file names.
        root, dirs, files = os.walk(self._confdir, topdown=True).next()
        dirs = [d for d in dirs if not d.startswith('.')]   # filter the . dirs that click uses.
        for d in dirs:
            n = ClickNode()
            # name *must* come before talble as the name of the node is used in the link in the table!
            for f in ['class', 'name', 'table', 'config']:
                path = os.path.join(self._confdir, d, f)
                if not os.path.isfile(path):
                    continue

                with open(path) as fd:
                    lines = [l.strip() for l in fd.readlines()]

                n.parse(f, lines)

            # we keep the properties of the node in "data" rather than make ClickNode hashable. Dunno why.
            self._click_graph.add_node(n.name, data=n)

        # now all the nodes are built. Use the ports file to build the graph.
        for d in dirs:
            ports_file = os.path.join(self._confdir, d, 'ports')
            if os.path.isfile(ports_file):
                out_index = None
                with open(ports_file) as pfd:
                    lines = [l.strip() for l in pfd.readlines()]

                output_mode = False
                port_num = 0
                for l in lines:
                    if 'input' in l or 'inputs' in l:
                        continue
                    elif 'output' in l or 'outputs' in l:
                        output_mode = True
                        port_num = 0
                        continue

                    # push    -       [0] ThreadSafeQueue@93
                    buf = l.split('\t')[2]
                    # remove [..]
                    buf = re.sub('\[\d+\]', '', buf)
                    # split by possible commas and strip whitespace
                    tokens = [t.strip() for t in buf.split(',')]

                    # log.debug('tokens {}'.format(tokens))
                    for t in tokens:
                        if not output_mode:
                            # assumes name == dir, which may be incorrect?
                            self._click_graph.add_edge(t, d, port=port_num)
                        else:
                            # assumes name == dir, which may be incorrect?
                            self._click_graph.add_edge(d, t, port=port_num)

                        port_num += 1


        return True   # sure, why not?

    def _find_classes_in_subtree(self, node, nbr, prev_nbr, classes):
        '''
        Given a nbr of a node, follow the nodes links until you hit a node with that class
        is in the given class list. The idea here is to trace the edges until we find a router or
        a physical node.

        NOTE: this only works when the nbr links directly to another node with the given classes
        via a single chain of unidirectional links. You can make click graphs that do not have
        this property. When you do, this function will break.

        Return value is a (node name, edge) tuple of the found classes.
        '''
        if nbr == node:
            return [(None, None)] # looped back to orig node. Ignore.

        node_data = self._click_graph.node[nbr]['data']
        if node_data.node_class in classes:
            # found one, return it and ignore subgraph below this node.
            return [(nbr, self._click_graph[prev_nbr][nbr])]

        next_nbrs = self._click_graph.successors(nbr)
        if not next_nbrs:
            return [(None, None)]    # dead end. toh or something.

        therest = []
        for next_nbr in next_nbrs:
            therest += self._find_classes_in_subtree(node, next_nbr, nbr, classes)

        return therest

    def _get_router_nbrs(self, node):
        '''
        get a list of routers that this node is connected to via a chain of edges.
        return value is name of router nbr, click graph edge of router nbr, click graph 
        edge of node that leads to nbr. (where edge is a graph edge object.)
        '''
        router_nbrs = []
        classes = [self._router_class, self._physical_class]
        for nbr in self._click_graph.neighbors(node):
            subtree_nodes = self._find_classes_in_subtree(node, nbr, node, classes)
            # remove dead ends (which will be None)
            found_node = [n for n in subtree_nodes if n[0]]
            if found_node:    # did not find a dead end.
                # Only one path should lead to a class we want.
                if len(found_node) != 1:
                    msg = 'click graph is broken somewhere in subgraph from {}'.format(node)
                    log.critical(msg)
                    raise ClickGraphException(msg)

                n, e = found_node[0]
                if self._click_graph.node[n]['data'].node_class == self._physical_class:
                    name, frm = self._mapInterfaceToNeighbor(self._click_graph.node[n]['data'].config[0])
                else:
                    name = n
                    frm = '{}-{}'.format(name, e['port'])   # recreate the link name. 

                to = '{}-{}'.format(node, self._click_graph[node][nbr]['port'])
                router_nbrs.append({'name': name, 'frm': frm, 'to': to})

        return router_nbrs

    def __repr__(self):
        return '{}\nTree: {}\n{}\nTree: {}'.format(
            nx.info(self._router_graph),
            self._router_graph.adj,
            nx.info(self._click_graph),
            self._click_graph.adj)

if __name__ == "__main__":
    '''Run tests on the ClickGraph object if this file is run alone.'''
    from sys import argv
    
    if '-d' in argv:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    def draw_graph(g):
        nx.draw_networkx(g, pos=nx.spring_layout(g), with_labels=True)
        plt.savefig('/users/glawler/tmp/{}.png'.format(g.name))
        log.info('wrote /users/glawler/tmp/{}.png'.format(g.name))
        write_dot(g, '/users/glawler/tmp/{}.dot'.format(g.name))
        log.info('wrote /users/glawler/tmp/{}.dot'.format(g.name))

    def dump_rtable(g, node=None, tohosts=None):
        print('-' * 80)
        if tohosts:
            hosts = [IPAddress(a) for a in tohosts]
            rtables = g.get_point2point(hosts)
        else:
            rtables = g.get_route_tables()

        if node:
            if tohosts:
                for table in rtables[node]:
                    print('p2p route : {}/{}/{}/({}) --> {}/{}/{} --> (cloud) --> {}/{}/{}'.format(
                        table['src_addr'],
                        table['src_name'],
                        table['src_link'],
                        table['src_iface'],
                        table['next_hop_addr'],
                        table['next_hop_name'],
                        table['next_hop_link'],
                        table['dst_addr'],
                        table['dst_name'],
                        table['dst_link']))

            else:
                print('{} routing table: {}'.format(node, rtables[node]))

        else:
            ttype = 'p2p' if tohosts else 'routing'
            print('{} table: {}'.format(ttype, rtables))

    def dump_router_nbrs(g, r):
        print('-' * 80)
        r_nbrs = g._get_router_nbrs(r)
        for nbr in r_nbrs:
            print('{} connected to {} via link {}/{}'.format(r, nbr['name'], nbr['to'], nbr['frm']))

    cg = ClickGraph()
    print(cg)
    print(nx.info(cg._click_graph))
    print(nx.info(cg._router_graph))

    dump_router_nbrs(cg, 'router1')
    dump_router_nbrs(cg, 'router5')

    dump_rtable(cg, 'router1')
    dump_rtable(cg, 'router1', tohosts=['10.3.1.1', '10.2.1.1', '10.1.1.1'])

    edgemap = cg.get_network_edge_map()
    print('click network edges:')
    for node, edges in edgemap.iteritems():
        for e in edges:
            print('\t{} connects to click node {} (on {}) via link {} '.format(node, e[1], e[2], e[0]))

    import matplotlib as mpl
    mpl.use('Agg')
    import matplotlib.pyplot as plt
    from networkx.drawing.nx_pydot import write_dot
    draw_graph(cg._router_graph)
    draw_graph(cg._click_graph)
