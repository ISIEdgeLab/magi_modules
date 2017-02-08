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

def click_except(msg):
    log.critical(msg)
    raise ClickGraphException(msg)

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
        '''Each entry in the table is a route with dst, gw, port, and link. 
            dst is an IPNetwork instance
            gw in an IPAddress instance
            port is an int and is the index into the click list of out ports
            link is a unique id for this iface/port
        '''
        # lines are of the form:
        # 10.1.10.2/32            -               3
        # addr/net  gw  port
        for l in lines:
            tokens = l.split()
            if len(tokens) == 3:
                net, gw, port = tokens
                gw = IPAddress(gw) if gw != '-' else None
                net = IPNetwork(net)
                self.table.append({
                    'dst': net,
                    'gw': gw,
                    'port': port,
                    'link': '{}-{}'.format(self.name, port)
                })

    def __repr__(self):
        return '{}/{}'.format(self.name, self.node_class)


class ClickConfigParser(object):
    '''
        Given a path to click configuration, extract queried data. Supports proc-like fs and unix socket API.
    '''
    def __init__(self, confpath='/click'):
        super(ClickConfigParser, self).__init__()
        self._confpath = confpath

        if not os.path.isdir(self._confpath):
            self._socket = self._open_control_socket(confpath)
            self._read = self._read_socket
            self._write = self.set_value
        else:
            self._read = self._read_file
            self._write = self.set_value

    def get_value(self, key):
        '''Given the path or API message, return the value in the file or API response.'''
        # the only difference between proc and socket API is the delimiter char: '/' or '.' 
        return self._read(key)

    def set_value(self, key):
        click_except('set_value() not yet implemented.')
        pass

    def _read_socket(self, key):
        '''Send msg to the connected click control socket and return the parsed response.'''
        # for protocol details see: http://read.cs.ucla.edu/click/elements/controlsocket
        # Basic protocol response is like:
        # XXX: <msg>
        # DATA NNN
        # ...
        # Where XXX is 200 success; not 200 error and NNN is len of DATA in bytes.
        msg = key.replace(os.sep, '.')
        self._socket.sendall('READ ' + msg + '\r\n')   # CRLF is expected.
        datasize = self._read_socket_response(self._socket)
        if datasize > 0:
            log.debug('reading {} bytes'.format(datasize))
            buf = self._socket.recv(datasize)
            # Not sure why, but "list" puts the number of items first. So remove that
            # if this is a 'list' command.
            lines = [t for t in buf.split('\n') if t]  # remove empty lines and split on \n
            if msg.lower() == 'list':
                return lines[1:]
            return lines

        return []

    def _read_socket_response(self, s):
        '''Read the click response. Return response and amounf of data to read.'''
        def _readline(s):
            buf = ''
            while True:
                c = s.recv(1)
                if c == '\r':
                    c = s.recv(1)
                    if c == '\n':
                        break
                
                buf += c
            return buf

        resp_line = _readline(s)
        code, _ = resp_line.split(' ', 1)
        if code != '200':
            return -1

        line = _readline(s)
        _, bytecnt = line.split()
        try:
            int(bytecnt)
        except TypeError:
            return -1

        return int(bytecnt)

    def _read_file(self, key):
        subpath = key.replace('.', os.sep)   # . --> / 
        path = os.path.join(self._confpath, subpath)
        lines = []
        try:
            log.debug('Reading file {}'.format(path))
            with open(path, 'r') as fd:
                lines = [l.strip() for l in fd.readlines()]
                # Not sure why, but "list" puts the number of items first. So remove that
                # if this is a 'list' command.
                if key.lower() == 'list':
                    lines = lines[1:]
        except IOError as e:
            log.warn('Error opening file: {}. Path built from key {}.'.format(path, key))
            # not an error. some nodes don't have all keys.

        return lines

    def _open_control_socket(self, path):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(path)
        except Exception as e:
            click_except('Unable to open click control UNIX socket {}: {}'.format(self._confpath, e))

        s.settimeout(1)  # should be very quick as it's local.

        # read proto header and version.
        buf = s.recv(64)
        # should be like "Click::ControlSocket/1.3"
        if not buf.startswith('Click::ControlSocket'):
            click_except('Bad protocol on click control socket, exiting.')

        try:
            _, v = buf.strip().split('/')
            if float(v) < 1.3:
                click_except('Click control protocol too old at {}'.format(v))
        except (ValueError, TypeError):
            click_except('Error in click control protocol.')

        return s

class ClickGraph(object):
    """
    Represent the topology graph and various bits. Read the live /click dir for initial graph configuration and
    live updates. 
    """
    def __init__(self, confpath='/click'):
        super(ClickGraph, self).__init__()

        self._confpath = confpath                 # The /click dir which contains live info.

        # These classes define which nodes are routers and other types of nodes in teh click graph.
        self._router_class = 'RadixIPLookup'    # anything that has this class is a leaf in the graph.
        self._physical_class = 'ToDevice'       # anything of this class is a link to a NIC and a physical node.
        self._localhost_class = 'ToHost'        # anything of this class points to the localhost.

        self._click_graph = nx.DiGraph(name='click')    # This is the "full" click graph built from /click.
        self._build_click_graph(self._confpath)

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
                    # GTL VERY VERY VERY DETER specific. We need the canonical name for the nade and 
                    # we use DETER naming knowledge to get it from the aliases. BAD. 
                    dst_name = dst_aliases[1][0].rsplit('-', 1)[0]

                    src_addr = str(route['dst'].ip)   # The "address" of this interface.
                    src_name = node
                    src_link = route['link']

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
                            # GTL This is still VERY VERY DETER specific and is very bad. We use DETER naming
                            # knowledge to arbitrarily strip things from an alias and use that as a 
                            # canonical name!
                            name = names[1][0].rsplit('-', 1)[0]
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

    def _build_click_graph(self, confpath):
        '''Build a click graph given a configuration.'''
        p = ClickConfigParser(confpath)
        handles = p.get_value('list')
        log.debug('click handles: {}'.format(handles))
        for handle in handles:
            n = ClickNode()
            for f in ['class', 'name', 'table', 'config']:
                lines = p.get_value('{}.{}'.format(handle, f))
                n.parse(f, lines)

            # we keep the properties of the node in "data" rather than make ClickNode hashable. Dunno why.
            self._click_graph.add_node(n.name, data=n)

        # now all the nodes are built. Use the ports file to build the graph.
        for handle in handles:
            lines = p.get_value('{}.{}'.format(handle, 'ports'))
            if lines:
                self._add_click_port_edges(handle, lines)

    def _add_click_port_edges(self, handle, lines):
        output_mode = False
        out_port_num = 0
        for l in lines:
            if 'input' in l or 'inputs' in l:
                continue
            elif 'output' in l or 'outputs' in l:
                output_mode = True
                continue

            if not output_mode:
                # # input mode links are on one line and look like:
                # # push    -       link_8_7_bw [0], link_6_7_bw [0], ICMPError@110 [0], ICMPError@114 [0], ...
                # buf = l.split('\t')[2]
                # tokens = re.findall('([\w@]+) \[(\d+)\]', buf)  # find all (name, port) pairs.
                # for t in tokens:
                #     self._click_graph.add_edge(t[0], handle,
                #                                port=t[1],
                #                                link='{}-{}'.format(t[0], t[1]))
                pass  # the in edges will be done when we add the nbr's ports...
            else:
                # output mode links are on mulitple lines, where the nth line is the nth output port.
                # push    -       [0] ThreadSafeQueue@93
                buf = l.split('\t')[2]
                # remove [..]
                tokens = re.findall('\[(\d+)\] ([\w@]+)', buf)
                for t in tokens:
                    self._click_graph.add_edge(handle, t[1],
                                               port=str(out_port_num),
                                               link='{}-{}'.format(handle, out_port_num))
                    out_port_num += 1

    def _find_classes_in_subtree(self, node, nbr, classes):
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
            return [None] # looped back to orig node. Ignore.

        node_data = self._click_graph.node[nbr]['data']
        if node_data.node_class in classes:
            # found one, return it and ignore subgraph below this node.
            return [nbr]

        next_nbrs = self._click_graph.successors(nbr)
        if not next_nbrs:
            return [None]    # dead end. toh or something.

        therest = []
        for next_nbr in next_nbrs:
            therest += self._find_classes_in_subtree(node, next_nbr, classes)

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
            subtree_nodes = self._find_classes_in_subtree(node, nbr, classes)
            # remove dead ends (which will be None)
            found_node = [n for n in subtree_nodes if n]
            if found_node:    # did not find a dead end.
                # Only one path should lead to a class we want.
                if len(found_node) != 1:
                    click_except('click graph is broken somewhere in subgraph from {}'.format(node))

                n = found_node[0]
                if self._click_graph.node[n]['data'].node_class == self._physical_class:
                    name, frm = self._mapInterfaceToNeighbor(self._click_graph.node[n]['data'].config[0])
                else:
                    name = n
                    frm = None

                to = self._click_graph[node][nbr]['link']
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
    dump_router_nbrs(cg, 'router7')
    dump_router_nbrs(cg, 'router8')
    dump_router_nbrs(cg, 'router9')

    dump_rtable(cg, 'router1')
    dump_rtable(cg, 'router1', tohosts=['10.3.1.1', '10.2.1.1', '10.1.1.1'])

    edgemap = cg.get_network_edge_map()
    print('click network edges:')
    for node, edges in edgemap.iteritems():
        for e in edges:
            # {'to_link': 'crypto5-2-elink5-2', 'nbr_host': 'vrouter', 'nbr': 'router8'}
            print('\t{} connects to click node {} (on {}) via link {} '.format(
                node, e['nbr'], e['nbr_host'], e['to_link']))

    import matplotlib as mpl
    mpl.use('Agg')
    import matplotlib.pyplot as plt
    from networkx.drawing.nx_pydot import write_dot
    draw_graph(cg._router_graph)
    draw_graph(cg._click_graph)
