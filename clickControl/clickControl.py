#!/usr/bin/env python 

# Copyright (C) 2015 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

import logging
import re, os, math, socket, struct, json
import time, ast, threading
import netifaces as ni
import networkx as nx

import clickGraph
from pymongo import MongoClient
from magi.util.agent import agentmethod, DispatchAgent
from magi.util.cidr import CIDR
from magi.testbed import testbed
from magi.util import database, execl
from magi.db import Collection
from one_hop_neighbors import OneHopNeighbors


def getAgent(**kwargs):
   agent = clickControlAgent()
   agent.setConfiguration(None, **kwargs)
   return agent

class clickControlAgent(DispatchAgent):
    """
    This click control agent allows the dynamic control of click-based network emulation
    """
    def __init__(self):
        DispatchAgent.__init__(self)
        self.log = logging.getLogger(__name__)
        self.click_config = "/tmp/vrouter.click"
        self.topology = database.getCollection('TopologyServer')
        self.UDPRunning = False
        self.isFlapping = False

        self.cg = clickGraph.clickGraph(self.click_config)
        # assumes clicks installed, should we install?
        
    @agentmethod()
    def updateVisualization(self, msg):
        return True
        '''
        This entire function needs to be redone
        (nodes, links, bad_links) = self.parseConfig()
        nodes = list(nodes)
        
        old_data = database.getData('topo_agent', dbHost=database.getCollector(), filters={'host': 'vrouter'})
        data = old_data[0]
        old_nodes = json.loads(data['nodes'])
        old_links = json.loads(data['edges'])
        old_nodes.remove(testbed.nodename)
        nodes.extend(old_nodes)

        fixed_links = [x for x in old_links if x not in bad_links]
        links.extend(fixed_links)
        
        data['nodes'] = json.dumps(nodes)
        data['edges'] = json.dumps(links)
        data.pop('agent', None)
        data.pop('created', None)
        data.pop('host', None)
        data.pop('_id', None)

        
        collection =  database.getCollection('topo_agent', dbHost=database.getCollector())
        collection.insert(data)
        '''

    @agentmethod()
    def startClick(self, msg):
        click_running = False
        # Check if click configuration exists      
        if not os.path.isfile(self.click_config):
            self.log.error("Click: no such configuration file %s" % self.click_config)
            return False
        
        # Check if the module is loaded.  If so, uninstall click first and reinstall
        (output, err) = execl.execAndRead("lsmod")
        if err != "":
            self.log.error("Click: %s" % err)
            return False
        
        tokens = output.split()
        
        for token in tokens:
            if token == "click":
                click_running = True
                break

        if click_running:
            self.stopClick(msg)

        (output, err) = execl.execAndRead("sudo click-install -j 2 %s" % self.click_config)
        if err != "":
            self.log.error("Click: %s" % err)
            return False

        return True
        
        
    @agentmethod()
    def stopClick(self, msg):
        (output, err) = execl.execAndRead("sudo click-uninstall")
        if err != "":
            self.log.error("Click: %s" % err)
            return False
        return True
        
    @agentmethod()
    def updateLinks(self, msg, links=[], delays=[], capacities=[], losses=[]):
        if len(links) != len(delays):
            if len(delays) != 1 and len(delays) != 0:
                self.log.error("Click: must specify delay for each link or specify only 1 or 0")
                return False
        if len(links) != len(capacities):
            if len(capacities) != 1 and len(capacities) != 0:
                self.log.error("Click: must specify capacity for each link or specify only 1 or 0")
                return False
        if len(links) != len(losses):
            if len(losses) != 1 and len(losses) != 0:
                self.log.error("Click: must specify loss probability for each link or specify only 1 or 0")
                return False
            
        c = 0
        skipDelay = False
        skipCapacity = False
        skipLoss = False
        if len(delays) == 0:
            skipDelay = True
        if len(capacities) == 0:
            skipCapacity = True
        if len(losses) == 0:
            skipLoss = True
            
        for c in range(len(links)):
            c_link = links[c]
            if not skipDelay:
                c_delay = ""
                if len(delays) == 1:
                    c_delay = delays[0]
                else:
                    c_delay = delays[c]

                ret = self.updateDelay(msg, link = c_link, delay = c_delay)
                if not ret:
                    return False
                
            if not skipCapacity:
                c_cap = ""
                if len(capacities) == 1:
                    c_cap = capacities[0]
                else:
                    c_cap = capacities[c]

                ret = self.updateCapacity(msg, link = c_link, capacity = c_cap)
                if not ret:
                    return False

            if not skipLoss:
                c_loss = ""
                if len(losses) == 1:
                    c_loss = losses[0]
                else:
                    c_loss = losses[c]

                ret = self.updateLossProbability(msg, link = c_link, loss = c_loss)
                if not ret:
                    return False
                    
        return True

    @agentmethod()
    def updateDelay(self, msg, link="", delay="0.0ms"):
        delay_path = "/proc/click/%s_bw/latency" % link
        if not os.path.exists(delay_path):
            delay_path = "/proc/click/%s_delay/delay" % link
            if not os.path.exists(delay_path):
                self.log.error("Click: no such link %s" % link)
                return False

        fh = None
        try:
            fh = open(delay_path, "w")
        except IOError as e:
            self.log.error("Click: %s" % e)
            return False

        fh.write(delay)
        fh.close()
            
        return True

    @agentmethod()
    def updateCapacity(self, msg, link="", capacity="1Gbps"):
        cap_path = "/proc/click/%s_bw/rate" % link
        if not os.path.exists(cap_path):
            cap_path = "/proc/click/%s_bw/bandwidth" % link
            if not os.path.exists(cap_path):
                self.log.error("Click: no such link %s" % link)
                return False

        fh = None
        try:
            fh = open(cap_path, "w")
        except IOError as e:
            self.log.error("Click: %s" % e)
            return False

        fh.write(capacity)
        fh.close()
            
        return True

    @agentmethod()
    def updateLossProbability(self, msg, link="", loss="0.0"):
        loss_path = "/proc/click/%s_loss/drop_prob" % link
        if not os.path.exists(loss_path):
            self.log.error("Click: no such link %s" % link)
            return False

        fh = None
        try:
            fh = open(loss_path, "w")
        except IOError as e:
            self.log.error("Click: %s" % e)
            return False

        fh.write(loss)
        fh.close()
        return True


    @agentmethod()
    def updateRoute(self, msg, router="", ip="", port="", next_hop=""):
        m = re.match("[0-9]+", router)
        if m:
            router = "router%s" % m.group(0)
        route_path = "/proc/click/%s/set" % router
        if not os.path.exists(route_path):
            self.log.error("Click: no such router %s" % router)
            return False

        fh = None
        try:
            fh = open(route_path, "w")
        except IOError as e:
            self.log.error("Click: %s" % e)
            return False

        if port == "":
            m = re.match("[0-9]+", next_hop)
            if m:
                next_hop = "router%s" % m.group(0)
            port = self.cg.getPort(router, next_hop)
            if not port:
                self.log.error("Click: Cannot find link between %s and %s\n" % (router, next_hop))
                return False
            
        fh.write("%s %s" % (ip, port))
        fh.close()
        return True

    
    @agentmethod()
    def updateRoutes(self, msg, path=[], ip=""):
        c = 0
        for c in range(len(path) - 1):
            router = path[c]
            next_hop = path[c + 1]
            self.updateRoute(msg, router=router, ip=ip, next_hop=next_hop)
            
    @agentmethod()
    def anycastHijack(self, msg, prefix, advertisers, randomStart = False):
        updatedHops = self.cg.anycastSPF(prefix, advertisers, randomStart)
        if updatedHops == None:
            # need better error messages
            return False
        
        for hop in updatedHops:
            if os.path.exists("/proc/click/%s/set" % hop[0]):
                self.updateRoute(None, router=hop[0], ip=prefix, next_hop=hop[1])

                
    @agentmethod()
    def startRouteFlaps(self, msg, flaps, rate):
        if not self.isFlapping:
            self.isFlapping = True
        t = threading.Thread(target=self.routeFlap, args=(flaps, rate, -1))
        t.start()
        return True

    @agentmethod()
    def stopRouteFlaps(self, msg):
        self.isFlapping = False
        return True

    @agentmethod()
    def flapForDuration(self, msg, flaps, rate, duration):
        return True


    def routeFlap(self, flaps, rate, duration = -1):
      
        while self.isFlapping:
            for flap in flaps:
                self.log.warn("ip = %s, router = %s, next_hop = %s" % ( flap[0], flap[1], flap[2] ))
                self.updateRoute(None, ip=flap[0], router=flap[1], next_hop=flap[2]);
            time.sleep(rate)
            if not self.isFlapping:
                break
            for flap in flaps:
                self.updateRoute(None, ip=flap[0], router=flap[1], next_hop=flap[3]);
            time.sleep(rate)
        return True
    
    
    @agentmethod()
    def startUDPTraffic(self, msg, node="source"):
        udp_path = "/proc/click/%s/active" % node
        if not os.path.exists(udp_path):
            self.log.error("Click: no such node %s" % node)
            return False

        fh = None
        try:
            fh = open(udp_path, "w")
        except IOError as e:
            self.log.error("Click: %s" % e)
            return False

        fh.write("true")
        fh.close()
        self.UDPRunning = True
        return True

    @agentmethod()
    def stopUDPTraffic(self, msg, node="source"):
        udp_path = "/proc/click/%s/active" % node
        if not os.path.exists(udp_path):
            self.log.error("Click: no such node %s" % node)
            return False

        fh = None
        try:
            fh = open(udp_path, "w")
        except IOError as e:
            self.log.error("Click: %s" % e)
            return False

        fh.write("false")
        fh.close()
        self.UDPRunning = False
        return True

    @agentmethod()
    def setUDPRate(self, msg, rate="100Mbps", node="source"):
        packet_size = 8000 # 1 KB
        m = re.match("[1-9][0-9]*[ ]*[GKMgkm]?[Bb]ps", rate)
        if not m:
            self.log.error("Click: Invalid rate %s" % rate)
            return False

        rate = m.group(0)
        m = re.search("[1-9][0-9]*", rate)
        if not m:
            self.log.error("Click: Invalid rate %s" % rate)
            return False

        init_rate = int(m.group(0))
        
        m = re.search("[GKMgkm]", rate)
        factor = 1
        if m:
            if m.group(0) == "G" or m.group(0) == "g":
                factor = 1000000000
            elif m.group(0) == "M" or m.group(0) == "m":
                factor = 1000000
            elif m.group(0) == "K" or m.group(0) == "k":
                factor = 1000
            else:
                factor = 1
                
        m = re.search("[Bb]", rate)
        multiplier = 1
        if not m:
            self.log.error("Click: Invalid rate %s" % rate)
            return False
        if m.group(0) == "B":
            multiplier = 8

        rate_in_pps = math.floor(init_rate * factor * multiplier / packet_size)

        udp_path = "/proc/click/%s/rate" % node
        if not os.path.exists(udp_path):
            self.log.error("Click: no such node %s" % node)
            return False

        fh = None
        try:
            fh = open(udp_path, "w")
        except IOError as e:
            self.log.error("Click: %s" % e)
            return False

        wasRunning = self.UDPRunning
        if self.UDPRunning:
            self.stopUDPTraffic(msg, node)

        fh.write("%d" % rate_in_pps)
        fh.close()

        if wasRunning:
            self.startUDPTraffic(msg, node)

        return True
