#!/usr/bin/env python

# Copyright (C) 2012 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

from magi.testbed import testbed
from magi.util.agent import agentmethod, DispatchAgent
from magi.util.processAgent import initializeProcessAgent
from subprocess import Popen, PIPE
import logging
import os
import signal
import sys
import time


log = logging.getLogger(__name__)

class TcpdumpAgent(DispatchAgent):
    '''
    '''
    def __init__(self):
        DispatchAgent.__init__(self)
        self.dumpfile = "/tmp/tcpdump.cap"
        self.destination = None
        self.pid = None
        
    @agentmethod()
    def startCollection(self, msg):
        log.info("starting collection")
        if not self.destination:
            raise AttributeError("Destination not set")
        log.info("destination: %s" %(self.destination))
        interface = self.node2Intf(self.destination)
        log.info("interface: %s" %(interface))
        tcpdumpCmd = ['tcpdump', '-i', interface, '-w', self.dumpfile]
        log.info('running: %s', ' '.join(tcpdumpCmd))
        p = Popen(tcpdumpCmd, stdout=PIPE, stderr=PIPE)
        time.sleep(1)
        if p.poll():
            log.info('Could not start tcpdump')
            return False
        self.pid = p.pid
        log.info('tcpdump started with process id %d' %(self.pid))
        return True
    
    @agentmethod()
    def stopCollection(self, msg):
        log.info("stopping collection")
        log.info("killing tcpdump process %d", self.pid)
        if self.pid:
            os.kill(self.pid, signal.SIGTERM)
        return True
    
    def node2Intf(self, destination):
        topoGraph = testbed.getTopoGraph()
        src = testbed.getNodeName()
        linkname = topoGraph[src][destination]['linkName']
        srcLinks = topoGraph.node[src]['links']
        try:
            ip = srcLinks[linkname]['ip']
            return testbed.getInterfaceInfo(ip).name
        except Exception:
            raise Exception("Invalid information. Mostly no direct link to destination '%s'." %(destination))
                

def getAgent(**kwargs):
    agent = TcpdumpAgent()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    agent = TcpdumpAgent()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
