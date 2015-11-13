#!/usr/bin/env python

# Copyright (C) 2013 University of Southern California.
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

from magi.util.agent import TrafficClientAgent
from magi.util.processAgent import initializeProcessAgent
from magi.util.distributions import *
from magi.util import database
from magi.util.execl import execAndRead

import logging
import random
import sys

log = logging.getLogger(__name__)

class CurlAgent(TrafficClientAgent):
    """
		The wget http generator controls a set of wget clients that make HTTP requests to a set HTTP servers.
		Also look at TrafficClientAgent
	"""
    def __init__(self):
        TrafficClientAgent.__init__(self)

        # Can be support distribution function (look magi.util.distributions)
        self.sizes = '1000'
        self.url = "http://%s/gettext/%d"

        # SOCKS support
        self.useSocks = False
        self.socksServer = "localhost"
        self.socksPort = 5010
        self.socksVersion = 4

        self.db_configured = False

    def _write_data_to_collection(self, output):
        # the output line is:
        # "http://buffy/getsize.py?length=128,0.506,0.506,128,252.000\n"
        # numbers after length=XXX,N1,N2,N3,N4 are:
        # %{time_total},%{time_starttransfer},%{size_download},%{speed_download}
        # see curl man page or google for details.
        # splitting the result by comma gives you:
        # ['data=http://buffy/getsize.py?length=128', '0.506', '0.506', '128', '252.000\n']
        # so split[4] gets you speed_download, etc.

        # regex might be more reliable, but slower?
        results = output.split(',')
        self.collection.insert({
            'server': results[0].split('/')[2],
            'time': float(results[1]),
            'size': float(results[3]),
            'speed': float(results[4].strip())
        })

    def oneClient(self):
        """ Called when the next client should fire (after interval time) 
        We "overload" it here so we can write the results to our collection.
        """
        if not self.db_configured:
            self.collection = database.getCollection(self.name)

            # set up visualization server to show our metrics.
            # we do this here as self.name exists here but not in __init__()
            viz_collection = database.getCollection('viz_data')
            for unit in ['time', 'size', 'speed']:
                viz_collection.insert({
                    'datatype': 'horizon_chart',
                    'display': 'Http Client',
                    'table': self.name,
                    'node_key': 'host',
                    'data_key': unit})

            self.db_configured = True

        if len(self.servers) < 1:
            log.warning("no servers to contact, nothing to do")
            return
        # fp = open(self.logfile, 'a')
        dst = self.servers[random.randint(0, len(self.servers) - 1)]
        try:
            (output, err) = execAndRead(self.getCmd(dst))
            # fp.write(str(time.time()) + "\t" + output)
            if self.collection:
                self._write_data_to_collection(output)
        except OSError as e:
            log.error("can't execute command: %s", e)
            fp.close()

    def getCmd(self, dst):
        cmd = 'curl -o /dev/null -s -S -w data=%{url_effective},%{time_total},%{time_starttransfer},%{size_download},%{speed_download}\\n ' + self.url % (dst, eval(self.sizes))
        if self.useSocks:
            socks_cmd = "--proxy socks%d://%s:%d" % (int(self.socksVersion), self.socksServer, int(self.socksPort))
            cmd += socks_cmd
        return cmd	
        
    def increaseTraffic(self, msg, stepsize):
        self.sizes = eval(self.sizes) + stepsize
        self.sizes = str(self.sizes)

    def reduceTraffic(self, msg, stepsize):
        self.sizes = eval(self.sizes) - stepsize
        if(self.sizes < 0):
            self.sizes = 0
        self.sizes = str(self.sizes)
               
    def changeTraffic(self, msg, stepsize):
        prob = random.randint(0, 100)
        if prob in range(10):
            self.sizes = eval(self.sizes) + int(stepsize * random.random())
        elif prob in range(10, 20):
            self.sizes = eval(self.sizes) - int(stepsize * random.random())
            if(self.sizes < 0):
                self.sizes = 0
        self.sizes = str(self.sizes)
    
def getAgent(**kwargs):
    agent = CurlAgent()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    agent = CurlAgent()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()

            
