#!/usr/bin/env python

# Copyright (C) 2013 University of Southern California.
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

# also copyright others 2016. 

import time
import pycurl
import logging
import random
import sys
import socket

from magi.util.distributions import *
from magi.util.agent import TrafficClientAgent
from magi.util.processAgent import initializeProcessAgent
from magi.util import database
from magi.util.execl import execAndRead
from libdeterdash import DeterDashboard

log = logging.getLogger(__name__)

class PyCurlAgent(TrafficClientAgent):
    """
        This agent uses the pycurl module to retrieve data via HTTP. It writes
        a number of metrics about the connection to the database.
	"""
    def __init__(self):
        TrafficClientAgent.__init__(self)

        # Can be support distribution function (look magi.util.distributions)
        self.sizes = '1000'

        # bind to specific local port
        self.localPort = None

        self.rateLimit = 0

        # SOCKS support
        self.useSocks = False
        self.socksServer = "localhost"
        self.socksPort = 5010
        self.socksVersion = 4

        # how often to write metrics about ongoing download to the 
        # database. Should probably be kept at 1.0. 
        self.metric_period = 1.0

        # internal vars below here.
        self._db_configured = False
        self._prev_time = time.time()
        self._prev_bytes = 0
        self._collection = None
        self._collection_progress = None
        self._collection_error = None
        self._progress_interval = 0
        self._url = "http://{}/gettext/{}"

    def _configure_database(self):
        # we keep data in two collections. One for post run, one for 
        # during run. This is mostly because we have more metrics
        # post run and don't want neraly empty entries for the 
        # during run entries. 
        self._collection = database.getCollection(self.name)
        self._collection_progress = database.getCollection(self.name + '_progress')
        self._collection_error = database.getCollection(self.name + '_error')

        # set up visualization server to show our metrics.
        # we do this here as self.name exists here but not in __init__()
        dashboard = DeterDashboard()
        units = [
            {'data_key': 'total_time', 'display': 'Total Time', 'unit': 'ms'},
            {'data_key': 'size', 'display': 'Transfer Size', 'unit': 'bytes'},
            {'data_key': 'speed', 'display': 'Throughput', 'unit': 'bytes/sec'}
        ]
        dashboard.add_time_plot('PyCurl Client', self.name, 'host', units)

        units = [{'data_key': 'dl_interval', 'display': 'Throughput', 'unit': 'bytes/sec'}]
        dashboard.add_time_plot('PyCurl', self.name + '_progress', 'host', units)

        self._db_configured = True

    def _save_post_metrics(self, dst, c):  # c==pycurl instance.
        self._collection.insert({
            # protocol checkpoint times in order
            # see: http://curl.haxx.se/libcurl/c/curl_easy_getinfo.html
            'name_lookup_time': c.getinfo(c.NAMELOOKUP_TIME),
            'connect_time': c.getinfo(c.CONNECT_TIME),
            'app_connect_time': c.getinfo(c.APPCONNECT_TIME), 
            'pre_transfer_time': c.getinfo(c.PRETRANSFER_TIME),
            'start_transfer_time': c.getinfo(c.STARTTRANSFER_TIME),
            'total_time': c.getinfo(c.TOTAL_TIME),
            'redirect_time': c.getinfo(c.REDIRECT_TIME),
            # other metrics/data
            'server': dst,
            'size': c.getinfo(c.SIZE_DOWNLOAD),
            'speed': c.getinfo(c.SPEED_DOWNLOAD),
            'server_addr': c.getinfo(c.PRIMARY_IP),
            'num_connects': c.getinfo(c.NUM_CONNECTS),
            'header_size': c.getinfo(c.HEADER_SIZE),
            'redirect_count': c.getinfo(c.REDIRECT_COUNT),
            'effective_url': c.getinfo(c.EFFECTIVE_URL)
        })

    def _save_progress_metrics(self, interval, dl_interval, dl_sofar, dl_total, ul_sofar, ul_total):
        # log.info('prog metrics: {}/{}/{}/{}/{}/{}'.format(
        #     interval, dl_interval, dl_sofar, dl_total, ul_sofar, ul_total))
        self._collection_progress.insert({
            'interval': interval,
            'dl_interval': dl_interval,
            'dl_sofar': dl_sofar,
            'dl_total': dl_total,
            'ul_sofar': ul_sofar,
            'ul_total': ul_total
        })

    def _progress_callback(self, dl_total, dl_sofar, ul_total, ul_sofar):
        # this function is invoked by pycurl many many times a second
        # during a connection. We may want to do as little computationally
        # expensive stuff here as possible. We may want to write results to
        # a queue, which is then evaluated once a second. 
        now = time.time()    
        self._progress_interval += now-self._prev_time
        if self._progress_interval >= self.metric_period:
            dl_interval = dl_sofar-self._prev_bytes
            self._save_progress_metrics(self._progress_interval, dl_interval, dl_sofar,
                                        dl_total, ul_sofar, ul_total)
            self._progress_interval = 0.0
            self._prev_bytes = dl_sofar

        self._prev_time = now
        return 0  # everything is OK.

    def oneClient(self):
        """ 
            Called when the next client should fire (after interval time) 
            We "overload" it here so we can write the results to our collection 
            as the base class does not give us access to the command output.
        """
        if not self._db_configured:
            self._configure_database()

        if len(self.servers) < 1:
            log.warning("no servers to contact, nothing to do")
            return

        dst = self.servers[random.randint(0, len(self.servers) - 1)]
        c = pycurl.Curl()
        url = self._url.format(dst, int(eval(self.sizes)))
        log.info('curl url: {}'.format(url))
        c.setopt(c.URL, url)
        c.setopt(c.NOPROGRESS, 0)
        c.setopt(c.PROGRESSFUNCTION, self._progress_callback)
        c.setopt(c.WRITEFUNCTION, lambda s: None) # Do nothing with received data.
        c.setopt(c.FOLLOWLOCATION, True)   # do we want this? Shouldn't come up in current setup.
        if self.localPort:
            c.setopt(c.LOCALPORT, self.localPort)

        if self.rateLimit:
            c.setopt(c.MAX_RECV_SPEED_LARGE, self.rateLimit)

        if self.useSocks:
            c.setopt(c.PROXY, '')
            c.setopt(c.PROXYPORT, self.socksPort)
            # version is only 4 or 5 as we check for this in confirmConfiguration()
            if self.socksVersion == 4:
                c.setopt(c.PROXYTYPE, c.PROXYTYPE_SOCKS4)
            else:
                c.setopt(c.PROXYTYPE, c.PROXYTYPE_SOCKS5)

        self._prev_time = time.time() # seed the time for the callback function.
        self._prev_bytes = 0
        try:
            c.perform()
        except pycurl.error as e:
            log.error('Error running pycurl: {}'.format(e))
            self._collection_error.insert({'exception': str(e)})
            c.close()
            return

        if c.getinfo(c.RESPONSE_CODE) != 200:
            log.error('Error with pycurl connection. Got response info/code: {} {}'.format(
                c.getinfo(c.RESPONSE_CODE),
                c.RESPONSE_CODE))

        if self._collection:
            self._save_post_metrics(dst, c)

        c.close()

    def increaseTraffic(self, msg, stepsize):
        self.sizes = int(eval(self.sizes)) + stepsize
        self.sizes = str(self.sizes)

    def reduceTraffic(self, msg, stepsize):
        self.sizes = int(eval(self.sizes)) - stepsize
        if(self.sizes < 0):
            self.sizes = 0
        self.sizes = str(self.sizes)
               
    def changeTraffic(self, msg, stepsize):
        prob = random.randint(0, 100)
        if prob in range(10):
            self.sizes = int(eval(self.sizes)) + int(stepsize * random.random())
        elif prob in range(10, 20):
            self.sizes = int(eval(self.sizes)) - int(stepsize * random.random())
            if(self.sizes < 0):
                self.sizes = 0
        self.sizes = str(self.sizes)

    def confirmConfiguration(self):
        try: 
            self.socksVersion = int(self.socksVersion)
        except ValueError:
            log.error('incorrect type for socks version ("{}"). Must be int.'.format(
                self.socksVersion))
            return False

        if self.socksVersion not in [4, 5]:
            return False

        return True
    
def getAgent(**kwargs):
    agent = PyCurlAgent()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    agent = PyCurlAgent()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
