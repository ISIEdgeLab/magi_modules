#!/usr/bin/env/ python

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

import logging

log = logging.getLogger(__name__)

def isClickNode():
    p = os.path.join('/', 'click')   # support for Click on Windows!!!
    return os.path.exists(p) and os.path.isdir(p)

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
                # port is unique to click router. iface is for pnodes. In C, this would be a union.
                self.table.append({'dst': net, 'gw': gw, 'port': int(port)})

    def __repr__(self):
        return '{}/{}'.format(self.name, self.node_class)


class ClickGraph(object):
    """
    Represent the topology graph and various bits. Read the live /click dir for initial graph configuration and
    live updates. 
    """
    def __init__(self, confdir='/click', conffile='/tmp/vrouter.click'):
        super(ClickGraph, self).__init__()

        self._conffile = conffile               # The conf file that click was started with.
        self._confdir = confdir                 # The /click dir which contains live info.
        self._router_class = 'RadixIPLookup'    # anything that has this class is a leaf in the graph.
        self._physical_class = 'ToDevice'       # anything of this class is a link to a NIC and a physical node.
        self._localhost_class = 'ToHost'        # anything of this class points to the localhost.

        self._click_graph = nx.DiGraph(name='click')    # This is the "full" click graph built from /click.
        self._build_graph_from_filesystem()

        self._router_graph = nx.DiGraph(name='routers') # This is the router + physical node graph
        self._build_router_graph_from_click_graph()

    def _build_router_graph_from_click_graph(self):
        for node in self._click_graph.nodes():
            data = self._click_graph.node[node]['data']
            if data.node_class == self._router_class:
                self._router_graph.add_node(node, data=data)
                for nbr in self._get_router_nbrs(node):
                    if nbr['name'] not in self._click_graph:
                        # This is a physical node. Add it "by hand" as it's not a click node as click knows
                        # nothing about it really.
                        cn = ClickNode()
                        cn.name = nbr['name']
                        cn.node_class = self._physical_class
                        self._router_graph.add_node(nbr['name'], data=cn)
                    
                    self._router_graph.add_edge(node, nbr['name'], port=nbr['port'])


    def get_route_tables(self):
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
                        'iface': 'eth{}'.format(route['port']),         # no iface for a click router.
                    })

                tables[n] = routes

        return tables

    def get_point2point(self, known_hosts):
        tables = defaultdict(list)
        for addr in known_hosts:
            for node in self._router_graph.nodes():
                route_table = self._router_graph.node[node]['data'].table
                for route in route_table:
                    if addr in route['dst']:   # is this address on this route?
                        gw = str(route['gw']) if route['gw'] else None

                        # get the next hop name by looking through the edges and matching the out port of the edge.
                        next_hop = None
                        for nbr, edge_data in self._router_graph[node].iteritems():
                            if edge_data['port'] == route['port']:
                                next_hop = nbr
                                break
                                
                        tables[node].append({
                            'dst': str(addr),
                            'gw': gw,
                            'iface': 'eth{}'.format(route['port']),   # GTL - what else to put here really?
                            'src': node,
                            'next_hop': next_hop})

                        break    # GTL - order is important. What order does click put the routes in?

        return dict(tables)

    def _findNeighbor(self, addr):
        '''
        Map IP addresses to neighbors (based on /etc/hosts)
        '''
        hosts = open('/etc/hosts', "r")
        nmask = self.dottedQuadToLong("255.255.255.0")
        net = nmask & self.dottedQuadToLong(addr)
        for host in hosts:
            tokens = host.strip("\n").split()
            if len(tokens) > 1:
                if(tokens[0] != addr):
                    v = (self.dottedQuadToLong(tokens[0]) & nmask) == net
                    if v:
                        if len(tokens) > 3:
                            return tokens[-1]
                        else:
                            k = tokens[-1].rfind("-")
                            return tokens[-1][:k]
        return None

    def _mapInterfaceToNeighbor(self, iface):
        '''
        Map Interface names to Neighbors
        '''
        addrs = ni.ifaddresses(iface)
        addr = addrs[ni.AF_INET][0]['addr']
        return self._findNeighbor(addr)
    
    def dottedQuadToLong(self, ip):
        return struct.unpack('I',socket.inet_aton(ip))[0]

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

    def _find_classes_in_subtree(self, node, nbr, classes):
        '''
        Given a nbr of a node, follow the nodes links until you hit a node with that class
        is in the given class list. The idea here is to trace the edges until we find a router or
        a physical node.

        NOTE: this only works when the nbr links directly to another node with the given classes
        via a single chain of unidirectional links. You can make click graphs that do not have
        this property. When you do, this function will break.
        '''
        if nbr == node:
            return [None] # looped back to orig node. Ignore.

        node_data = self._click_graph.node[nbr]['data']
        if node_data.node_class in classes:
            return [nbr]    # found one, return it and ignore subgraph below this node.

        next_nbrs = self._click_graph.successors(nbr)
        if not next_nbrs:
            return [None]    # dead end. toh or something.

        therest = []
        for next_nbr in next_nbrs:
            therest += self._find_classes_in_subtree(node, next_nbr, classes)

        return therest


    def _get_router_nbrs(self, node):
        '''return a list of routers that this node is connected to via a chain of edges.'''
        router_nbrs = []
        classes = [self._router_class, self._physical_class]
        for nbr in self._click_graph.neighbors(node):
            subtree_nodes = self._find_classes_in_subtree(node, nbr, classes)
            # remove dead ends (which will be None)
            found_node = [n for n in subtree_nodes if n]
            if found_node:    # did not find a dead end.
                # Only one path should lead to a class we want.
                if len(found_node) != 1:
                    msg = 'click graph is broken somewhere in subgraph from {}'.format(node)
                    log.critical(msg)
                    raise ClickGraphException(msg)

                n = found_node[0]
                if self._click_graph.node[n]['data'].node_class == self._physical_class:
                    n = self._mapInterfaceToNeighbor(self._click_graph.node[n]['data'].config[0])

                router_nbrs.append({'name': n, 'port': self._click_graph[node][nbr]['port']})

        return router_nbrs

    def __repr__(self):
        return '{}\nTree: {}\n{}\nTree: {}'.format(
            nx.info(self._router_graph),
            self._router_graph.adj,
            nx.info(self._click_graph),
            self._click_graph.adj)

if __name__ == "__main__":
    from sys import argv
    
    if '-d' in argv:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    def draw_graph(g):
        nx.draw_spring(g)
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
                    print('{} --> {} via {} ({})'.format(
                        table['src'],
                        table['dst'],
                        table['nbr'],
                        table['iface']))

            else:
                print('{} routing table: {}'.format(node, rtables[node]))

        else:
            ttype = 'p2p' if tohosts else 'routing'
            print('{} table: {}'.format(ttype, rtables))

    def dump_router_nbrs(g, r):
        print('-' * 80)
        r_nbrs = g._get_router_nbrs(r)
        for nbr in r_nbrs:
            print('{} connected to {} via port {} ("eth{}")'.format(
                r, nbr['name'], nbr['port'], nbr['port']))

    cg = ClickGraph()
    print(cg)
    print(nx.info(cg._click_graph))
    print(nx.info(cg._router_graph))

    dump_router_nbrs(cg, 'router1')
    dump_router_nbrs(cg, 'router5')

    dump_rtable(cg, 'router1')
    dump_rtable(cg, 'router1', tohosts=['10.3.1.1', '10.2.1.1', '10.1.1.1'])

    # import matplotlib as mpl
    # mpl.use('Agg')
    # import matplotlib.pyplot as plt
    # from networkx.drawing.nx_pydot import write_dot
    # draw_graph(cg._click_graph)
    # draw_graph(cg._router_graph)
