#!/usr/bin/env python 

# Copyright (C) 2015 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

import logging
import re, os, math, socket, struct, json
import time, ast, threading
import netifaces as ni
import networkx as nx

from subprocess import Popen

import clickGraph
from pymongo import MongoClient
from magi.util.agent import agentmethod, DispatchAgent
from magi.util.cidr import CIDR
from magi.testbed import testbed
from magi.util import database, execl
from magi.db import Collection
from one_hop_neighbors import OneHopNeighbors
from click_config_parser import ClickConfigParser, ClickConfigParserException


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
        self.UDPRunning = False
        self.isFlapping = False

        self.cg = clickGraph.clickGraph(self.click_config)
        # assumes clicks installed, should we install?

        self._clickProc = None
        self._confPath = '/click'   # handle to click's runtime configuration.

    @agentmethod()
    def updateVisualization(self, msg):
        return True

    @agentmethod()
    def startClick(self, msg, userMode=False, DPDK=True):
        click_running = False
        # Check if click configuration exists      
        if not os.path.isfile(self.click_config):
            self.log.error("Click: no such configuration file %s" % self.click_config)
            return False
       
        if not userMode and not DPDK:
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

        else:  # not in kernel - does not daemonize itself, so we handle it as a proc.
            if DPDK:
                cmd = 'click  --dpdk -c 0xffffff -n 4 -- -u /click {}'.format(self.click_config)
            elif userMode:
                cmd = 'click {} -u /click'.format(self.click_config)
            else:
                log.error('startClick must be one of kernel, userMode, or DPDK')

            self.log.info('Running cmd: {}'.format(cmd))
            self._clickProc = Popen(cmd.split())
            time.sleep(1)   # give it sec to fail...
            if self._clickProc.poll():
                self.log.error("Error starting click in user space. exit={}".format(self._clickProc.poll()))
                self._clickProc = None
                return False

            self.log.info('user space click started. pid={}'.format(self._clickProc.pid))

        return True
        
        
    @agentmethod()
    def stopClick(self, msg):
        if not self._clickProc:
            (output, err) = execl.execAndRead("sudo click-uninstall")
            if err != "":
                self.log.error("Click: %s" % err)
                return False

            os.rmdir('/click')   # process does not clean up the soket when killed.

        else:
            self._clickProc.kill()
            self._clickProc.wait()   # GTL may not want this.
            self._clickProc = None
            os.remove('/click')   # process does not clean up the soket when killed.

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
                self.log.error("Click: must specify loss probibility for each link or specify only 1 or 0")
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
    def updateClickConfig(self, msg, node, key, value):
        '''If you know the exact click node and key you can update teh value directly.'''
        retVal = False
        try:
            ccp = ClickConfigParser()
            ccp.parse(self._confPath)
            retVal = ccp.set_value(node, key, value)
        except ClickConfigParserException as e:
            self.log.error(e)

        return retVal

    @agentmethod()
    def updateDelay(self, msg, link="", delay="0.0ms"):
        # this config can be 'delay' or 'latency'
        ret = [False]
        for key in ['latency', 'delay']:
            ret.append(self.updateClickConfig(msg, '{}_bw'.format(link), key, delay))

        return any(ret)

    @agentmethod()
    def updateCapacity(self, msg, link="", capacity="1Gbps"):
        # Older versions of click use 'rate'. So we set both rate and bandwidth
        ret = [False]
        for key in ['bandwidth', 'rate']:
            ret.append(self.updateClickConfig(msg, '{}_bw'.format(link), key, capacity))

        return any(ret)

    @agentmethod()
    def updateLossProbability(self, msg, link="", loss="0.0"):
        return self.updateClickConfig(msg, '{}_loss'.format(link), 'drop_prob', loss)

    @agentmethod()
    def updateTargetedLoss(self, msg, link=None, prefix=None, destination=None, source=None, clear_drops=None,
                         burst=None, drop_prob=None, active=True):
        node = '{}_TL'.format(link)  # TL hardcoded here and in template as a TargetedLoss node.
        try:
            ccp = ClickConfigParser()
            ccp.parse(self._confPath)
            # set all values given. return False if any fail.
            args = { 'prefix': prefix, 'dest': destination, 'source': source, 'clear_drops': clear_drops,
                     'burst': burst, 'drop_prob': drop_prob }
            for key, value in args.iteritems():
                if value is not None:   # can be zero or ''!
                    if not ccp.set_value(node, key, value):
                        self.log.info('Error setting {} --> {} in updateTargetedLoss'.format(key, value))
                        return False

            if not ccp.set_value(node, 'active', str(active).lower()):  # lower case bool in click
                self.log.info('Error setting targeted loss link active to {}'.format(active))
                return False

        except ClickConfigParserException as e:
            self.log.error(e)
            return False

        return True

    @agentmethod()
    def updateSimpleReorder(self, msg, link=None, timeout=None, packets=None, sampling_prob=None, active=True):
        node = '{}_SR'.format(link)
        try:
            ccp = ClickConfigParser()
            ccp.parse(self._confPath)
            args = { 'timeout': timeout, 'packets': packets, 'sampling_prob': sampling_prob }
            for key, value in args.iteritems():
                if value is not None:
                    if not ccp.set_value(node, key, value):
                        self.log.info('Error setting {} --> {} in updateSimpleReorder'.format(key, value))
                        return False

            if not ccp.set_value(node, 'active', str(active).lower()):  # lower case bool in click
                self.log.info('Error setting simple reorder link active to {}'.format(active))
                return False

        except ClickConfigParserException as e:
            self.log.error(e)
            return False

        return True

    @agentmethod()
    def updateRoute(self, msg, router="", ip="", port="", next_hop=""):
        m = re.match("[0-9]+", router)
        if m:
            router = "router%s" % m.group(0)

        if port == "":
            m = re.match("[0-9]+", next_hop)
            if m:
                next_hop = "router%s" % m.group(0)
            port = self.cg.getPort(router, next_hop)
            if not port:
                self.log.error("Click: Cannot find link between %s and %s\n" % (router, next_hop))
                return False
            
        return self.updateClickConfig(msg, router, 'set', '{} {}'.format(ip, port))

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
        self.UDPRunning = self.updateClickConfig(msg, node, 'active', 'true')
        return self.UDPRunning

    @agentmethod()
    def stopUDPTraffic(self, msg, node="source"):
        self.UDPRunning = self.updateClickConfig(msg, node, 'active', 'false')
        return self.UDPRunning

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

        wasRunning = self.UDPRunning
        if self.UDPRunning:
            self.stopUDPTraffic(msg, node)

        self.updateClickConfig(msg, node, 'rate', rate_in_pps)

        if wasRunning:
            self.startUDPTraffic(msg, node)

        return True
