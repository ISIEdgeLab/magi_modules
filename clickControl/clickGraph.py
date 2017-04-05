#!/usr/bin/env/ python

# Copyright (C) 2015 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

import networkx as nx
import netifaces as ni
import re, struct, socket, os
import logging, json

from random import shuffle
from collections import deque

class clickGraph():
    """
    Represent the topology graph
    """

    def __init__(self, infile):
        self.click_config = infile
        self.g = nx.Graph()
        self.log = logging.getLogger(__name__)
        self.isDPDK = False
        self.DPDKInfo = []
        self.IPtoIfaceMap = {}

        
        if os.path.exists('/tmp/ifconfig.json'):
            self.isDPDK = True
            self.getDPDKInfo()

        self.buildIPtoIfaceMap()
            
        self.parseConfig()

    def getDPDKInfo(self):
        fh = open('/tmp/ifconfig.json')
        self.DPDKInfo = json.load(fh)
        fh.close()
        
    def parseConfig(self):
        ''' Parse the click config to build the graph'''
        if not os.path.isfile(self.click_config):
            self.log.error("Click: no such configuration file %s" % self.click_config)
            return False
        
        conf = open(self.click_config, "r")
        ifacemap = {}
        links = []
        bad_links = []

        # process each line
        for line in conf:
            tokens = line.strip("\n").split()
            if len(tokens) > 1:
                
                # Look for router definitions (router1 :: ...)
                if re.match("router[0-9]+", tokens[0]):
                    if tokens[1] == "::":
                        self.g.add_node(tokens[0])

                # Look for links (router1[0] ->)
                if re.match("router[0-9]+\[[0-9]+\]", tokens[0]):
                    if tokens[1] == "->":
                        # find the router name and the output port
                        
                        rtr = re.search("router[0-9]+", tokens[0]).group(0)
                        port = re.search("\[[0-9]+\]", tokens[0]).group(0).strip('[]')
                        
                        # check to see if the link is going to an arp querier or output chain
                        # indicating a path to a physical interface
                        
                        m = re.search("arpq[0-9]+", tokens[-1])
                        m2 = re.search("out[0-9]+", tokens[-1])
                        if m or m2:
                            if not m:
                                m = m2
                            last_element = tokens[-1].strip(';')
                            neighbor = ifacemap[last_element]
                            ports = {'%s_port' % rtr: port, '%s_port' % neighbor: '-1'}
                            self.g.add_edge(rtr, neighbor, ports)

                        # Otherwise, confirm that the path does not go to host                        
                        elif (not re.match("toh", tokens[-1]) and
                              not re.match("Discard", tokens[-1])):
                            
                            # This is an actual link
                            other_rtr = tokens[-1].strip(';')
                            
                            if self.g.has_edge(rtr, other_rtr):
                                edge = self.g[rtr][other_rtr]
                                edge['%s_port' % rtr] = port
                            else:
                                ports = {'%s_port' % rtr: port}
                                self.g.add_edge(rtr, other_rtr, ports)
                                
                # Look for output chains (either arps or outs)
                if re.match("arpq[0-9]+", tokens[0]):
                    for token in tokens:
                        # Find which interface this output path is going too.
                        if re.match("ARPQuerier.*", token):
                            m = re.search("eth[0-9]+", token)
                            iface = ""
                            node = ""
                            if m:
                                iface = m.group(0)
                            else:
                                m = re.search("vlan[0-9]+", token)
                                if m:
                                    iface = m.group(0)
                                else:
                                    ip = re.search("[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", token).group(0)
                                    iface = self.IPtoIfaceMap[ip]
                                    node = self.findNeighbor(ip)
                                    
                            if node == "":
                                node = self.mapInterfaceToNeighbor(iface)
                            ifacemap[tokens[0]] = node
                            self.g.add_node(node)
                            break

                            
                elif re.match("out[0-9]+", tokens[0]):
                    for token in tokens:
                        if re.match("ToDevice.*", token):
                            m = re.search("eth[0-9]+", token)
                            iface = ""
                            node = ""
                            if m:
                                iface = m.group(0)
                            else:
                                m = re.search("vlan[0-9]+", token)
                                if m:
                                    iface = m.group(0)
                                else:
                                    ip = re.search("[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+", token).group(0)
                                    iface = self.IPtoIfaceMap[ip]
                                    node = self.findNeighbor(ip)

                            if node == "":
                                node = self.mapInterfaceToNeighbor(iface)
                            ifacemap[tokens[0]] = node
                            self.g.add_node(node)
                            break
                                
        return True

    def buildIPtoIfaceMap(self):
        if not self.isDPDK:
            for interface in ni.interfaces():
                addrs = ni.ifaddresses(interface)
                if ni.AF_INET in addrs:
                    self.IPtoIfaceMap[addrs[ni.AF_INET][0]['addr']] = interface
        else:
            for entry in self.DPDKInfo:
                if 'ip' in entry:
                    self.IPtoIfaceMap[entry['ip']] = entry['interface']
    
    def mapInterfaceToNeighbor(self, iface):
        '''
        Map Interface names to Neighbors
        '''
        addrs = ni.ifaddresses(iface)
        addr = addrs[ni.AF_INET][0]['addr']
        return self.findNeighbor(addr)
    
    def findNeighbor(self, addr):
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
        return "unknown"

    def dottedQuadToLong(self, ip):
        return struct.unpack('I',socket.inet_aton(ip))[0]

    def writeGraph(self, filename):
        nx.write_edgelist(self.g, filename)

    def getPort(self, router, neighbor):
        if self.g.has_edge(router, neighbor):
            return self.g[router][neighbor]['%s_port' % router]
        return None

    def anycastSPF(self, prefix, advertisers, randomStart = False):
        '''
        Use SPF to advertise prefix from advertisers and update associated routing tables
        '''

        # make sure the nodes exists
        if advertisers == []:
            return None
        for ader in advertisers:
            if not self.g.has_node(ader):
                return None

        # randomize who to start with if desired
        if randomStart:
            shuffle(advertisers)

        # standard BFS elements
        visited = set()
        successors = {}
        to_visit = deque()

        # Start by finding the BFS successors for each advertiser
        # add the advertisers of nodes to visit
        for ader in advertisers:
            successors[ader] = nx.bfs_successors(self.g, ader)
            to_visit.append((ader, ader, ader))

        # Clear the to_visit list.  Don't really need strong visited checking
        # As BFS successors ensure that each node is only visited once
        # per BFS  (One BFS per advertiser).
        while len(to_visit) > 0:
            (node, next_hop, ader) = to_visit.popleft()
            if node not in visited:
                visited.add(node)
                if node != ader:
                    self.g.node[node][prefix] = next_hop
                else:
                    self.g.node[node][prefix] = None
                    
                if node in successors[ader]:
                    for next_node in successors[ader][node]:
                        to_visit.append((next_node, node, ader))

        # Generate the updates
        hops = []
        for node in self.g.nodes():
            next_hop = self.g.node[node][prefix]
            if prefix != None:
                hops.append((node, next_hop))
            
        return hops
        

if __name__ == "__main__":
    cg = clickGraph("/tmp/vrouter.click")
    cg.writeGraph('/tmp/banana.output')
    print cg.getPort('router1', 'router8')
