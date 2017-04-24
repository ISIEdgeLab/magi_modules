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
from click_config_parser import ClickConfigParser

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
        self.values = {}   # Generic name/value pairs found in click node configurations.

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
            self.values[attr] = lines[0]    # ...or do we want to write all data?

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

        self._build_graphs()

        self._known_hosts = None

        self.link_stat_units = [
                {'data_key': 'bandwidth', 'display': 'Bandwidth', 'unit': 'bytes/second'},
                {'data_key': 'latency', 'display': 'Latency', 'unit': 'ms'},
                {'data_key': 'drops', 'display': 'Packet Drops', 'unit': 'number'},
                {'data_key': 'drop_prob', 'display': 'Drop Probability', 'unit': '%'},
                {'data_key': 'capacity', 'display': 'Link Capacity', 'unit': 'ask Erik'}
            ]

    def _build_graphs(self):
        self._click_graph = nx.DiGraph(name='click')    # This is the "full" click graph built from /click.
        self._build_click_graph(self._confpath)

        self._router_graph = nx.DiGraph(name='routers') # This is the router + physical node subgraph.
        self._build_router_graph_from_click_graph()     # the router graph is built from the existing /click graph.

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
        # This function handles kernel-click, which shows up as an click node with class = self._physical_class
        # and DPDK which does not. The DPDK looks for gateway nodes in the route tables of the click nodes
        # and assumes they are physical nodes.
        network_map = {}
        # kernel click.
        for node in self._router_graph.nodes():
            if self._router_graph.node[node]['data'].node_class == self._physical_class: # phy node, add the links.
                log.debug('adding {} edges to network edge map.'.format(node))
                network_map[node] = []
                for nbr, edge_data in self._router_graph[node].iteritems():
                    network_map[node].append({
                        'to_link': edge_data['to'], 
                        'nbr': nbr,
                        'nbr_host': testbed.nodename})
        # DPDK click.
        for node in self._router_graph.nodes():
            for entry in self._router_graph.node[node]['data'].table:
                if entry['gw']:
                    if node not in network_map:
                        network_map[node] = []

                    names = socket.gethostbyaddr(str(entry['gw']))
                    network_map[node].append({
                        'to_link': names[0],
                        'nbr': min(names[1], key=len),
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
        p = ClickConfigParser()
        p.parse()
        conf = p.get_configuration()
        log.debug('click nodes: {}'.format(conf.keys()))
        for node, values in conf.iteritems():
            n = ClickNode()
            for key, values in values.iteritems():
                n.parse(key, values['lines'])

            # we keep the properties of the node in "data" rather than make ClickNode hashable. Dunno why.
            self._click_graph.add_node(n.name, data=n)

        # now all the nodes are built. Use the ports file to build the graph.
        for node, values in conf.iteritems():
            if 'ports' in values:
                self._add_click_port_edges(node, values['ports']['lines'])

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

    def _find_links_to_class(self, node, nbrs, node_classes):
        # given a node and a nbr, search that subtree for the single node of class 'node_class'
        # and return the link chain to the node of that class.
        # aka: find the click nodes between routers = given a router name, find the click node names
        # between that router and whichever router is in this subtree.
        if nbrs[-1] == node:
            return None   # loop

        node_data = self._click_graph.node[nbrs[-1]]['data']
        if node_data.node_class in node_classes:
            log.debug("found end of chain: {}".format(nbrs[-1]))
            # found it, collapse recursion.
            return nbrs
        
        for next_nbr in self._click_graph.successors(nbrs[-1]):
            log.debug('{} Looking {} -> {}'.format(node, nbrs[-1], next_nbr))
            links = self._find_links_to_class(node, nbrs + [next_nbr], node_classes)
            if links:
                return links

        return None

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
                    # there will be a physical class when we're in kernel mode.
                    name, frm = self._mapInterfaceToNeighbor(self._click_graph.node[n]['data'].config[0])
                else:
                    name = n
                    frm = None

                to = self._click_graph[node][nbr]['link']
                router_nbrs.append({'name': name, 'frm': frm, 'to': to})
            
        # we also have to find the physcial node in DPDK mode. In this mode there 
        # are no nodes of class physical type. We go through the routing tables looking
        # for gateways and assume those are physical nodes.
        for entry in self._router_graph.node[node]['data'].table:
            if entry['gw']:
                names = socket.gethostbyaddr(str(entry['gw']))
                router_nbrs.append({
                    'to': names[0],
                    'name': min(names[1], key=len),   # shortest name is canonical
                    'frm': node})

        return router_nbrs

    def init_visualization(self, dash, agent):
        dash.add_link_annotation('Click Configuration', agent, 'edge', self.link_stat_units)

    def _get_stats(self):
        # Iterate over the click graph, grabbing the newest stats/units. We
        # attach the stats to the routers though and not the click graph nodes
        # so we need to iterate from the router graph as well.

        # rebuild the world to get possibly updated stats.
        self._build_graphs()

        stats = {}
        router_nodes = [n for n in self._router_graph.nodes() if n in self._click_graph]
        for node in router_nodes:
            # for each router, traverse it's subtree collecting stats.
            for nbr in self._click_graph.neighbors(node):
                chain = self._find_links_to_class(node, [nbr], [self._router_class])
                log.debug('{} -> {} chain: {}'.format(node, nbr, chain))
                if not chain:
                    continue    # chain that does not go to another router. toh, or loops around.

                for click_node_name in chain:
                    # see if this click node has stats we're looking for.
                    click_node_data = self._click_graph.node[click_node_name]['data']
                    link = '["{}","{}"]'.format(node, chain[-1]) # we encode the link as mongo and JSON don't 
                                                             # do tuples. stupid, but true.
                    for stat in self.link_stat_units:
                        if stat['data_key'] in click_node_data.values:
                            if not link in stats:
                                stats[link] = {}

                            # i.e. stats["[router1,router4]"]['bandwidth'] = 1250000 
                            # hashtag ugh.
                            stats[link][stat['data_key']] = click_node_data.values[stat['data_key']]

        return stats

    def get_router_click_chain(self, node_a, node_b):
        for nbr in self._click_graph.neighbors(node_a):
            chain = self._find_links_to_class(node_a, [nbr], [self._router_class])
            if chain:
                if chain[-1] == node_b:
                    return chain

        return None

    def set_config(self, node_a, node_b, key, value):
        chain = self.get_router_click_chain(node_a, node_b)
        if not chain:
            log.error('Unable to traverse click links between {} and {}.'.format(node_a, node_b))
            return False

        for click_node in chain:
            if key in self._click_graph.node[click_node]['data'].values:
                ccp = ClickConfigParser()
                ccp.parse(self._confpath)
                ccp.set_value(click_node, key, value)
                return True

    def insert_stats(self, collection):
        click_stats = self._get_stats()
        log.info('inserting {} stats into the db.'.format(len(click_stats)))
        for link, stats in click_stats.iteritems():
            # stats is a list of dicts. the dict entry looks like:
            #       (nodeA, nodeB) : {stat_key: stat_value, stat_key: stat_value, ...}
            # the key is a directed link from nodeA to nodeB. The "stat_key"s are values
            # which match the units in self.link_stat_units.
            for unit_key, unit_value in stats.iteritems():
                collection.insert({'edge': link, unit_key: unit_value})

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
    # dump_router_nbrs(cg, 'router7')
    # dump_router_nbrs(cg, 'router8')
    # dump_router_nbrs(cg, 'router9')

    dump_rtable(cg, 'router1')
    dump_rtable(cg, 'router1', tohosts=['10.3.1.1', '10.2.1.1', '10.1.1.1'])

    edgemap = cg.get_network_edge_map()
    print('click network edges:')
    for node, edges in edgemap.iteritems():
        for e in edges:
            # {'to_link': 'crypto5-2-elink5-2', 'nbr_host': 'vrouter', 'nbr': 'router8'}
            print('\t{} connects to click node {} (on {}) via link {} '.format(
                node, e['nbr'], e['nbr_host'], e['to_link']))

    print('Current configuration:')
    import json   # keys are in JSON for not good reasons. 
    stats = cg._get_stats()
    print('{}\n\n'.format(stats))
    key = stats.keys()[0]
    nodes = json.loads(key)
    print('"stats" for {} : {}'.format(nodes, stats[key]))
    key = stats.keys()[-1]
    nodes = json.loads(key)
    print('"stats" for {} : {}'.format(nodes, stats[key]))

    if '-o' in argv:
        import matplotlib as mpl
        mpl.use('Agg')
        import matplotlib.pyplot as plt
        from networkx.drawing.nx_pydot import write_dot
        draw_graph(cg._router_graph)
        draw_graph(cg._click_graph)

    cg.set_config('router4', 'router1', 'latency', '500ms')
