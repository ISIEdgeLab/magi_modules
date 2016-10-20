#!/usr/bin/env python

# Copyright (C) 2012 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

from magi.testbed import testbed
from magi.util import database
from magi.util.agent import agentmethod, ReportingDispatchAgent
from magi.util.processAgent import initializeProcessAgent
from route_data import RouteData, RouteDataException

import logging
import os
import time

log = logging.getLogger(__name__)

class RouteReportAgent(ReportingDispatchAgent):
    """
        An agent which periodically write routing table (and point to point route information) to 
        a database. 
    """
    def __init__(self):
        super(RouteReportAgent, self).__init__()
        # User configurable. 
        self.interval = 5
        self.truncate = True
        self.recordLimit = 0    # If True, only keep the newest data in the database. 

        # do not change these. 
        self.active = False
        self.collection = {}

        # Do we want to expose this API to let people modify "control nets"?
        self._routeData = RouteData()

    def periodic(self, now):
        if self.active:
            log.info("running periodic")

            for func, collection in [(self._routeData.get_route_tables, 'routes'), 
                                     (self._routeData.get_point2point, 'point2point')]:
                tables = None
                try:
                    tables = func()
                except RouteDataException as e:
                    log.error('Error getting {} tables: {}'.format(collection, e))

                if tables:
                    log.info('found {} routes for {} host'.format(collection, len(tables.keys())))
                    for host, entry in tables.iteritems():
                        for route in entry:
                            route['router'] = host      # this is needed as there are "hosts" that 
                                                        # only exist in Click's imagination. Magi sees
                                                        # "vrouter", click and the GUI see "router1" and
                                                        # "router2", etc. 
                            log.debug('Inserting {} route: {}'.format(collection, route))
                            self.collection[collection].insert(route)

        ret = now + int(self.interval) - time.time()
        return ret if ret > 0 else 0

    @agentmethod()
    def startCollection(self, msg):
        if not self.active:
            for table_type in ['routes', 'point2point']:
                log.info('Getting collection: {}'.format(self.name + '_' + table_type))
                self.collection[table_type] = database.getCollection(self.name + '_' + table_type)

            if self.truncate:
                log.debug('truncating old records')
                for c in self.collection.values():
                    log.info('Truncating collection: {}'.format(c))
                    c.remove()

            log.info('route recording started')
            self.active = True

        else:
            log.warning('start collection requested, but collection is already active')
            
        # return True so that any defined trigger gets sent back to the orchestrator
        return True

    @agentmethod()
    def stopCollection(self, msg):
        if self.active:
            log.info('stopping route recording')

        self.active = False
        # return True so that any defined trigger gets sent back to the orchestrator
        return True

    def confirmConfiguration(self):
        try:
            self.interval = int(self.interval)
        except ValueError:
            log.error('Unable to convert integer value to int: %s', self.interval)
            return False

        return True

def getAgent(**kwargs):
    agent = RouteReportAgent()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    from sys import argv 

    debug = False
    logging.basicConfig(level=logging.INFO)
    agent = RouteReportAgent()
    if debug:
        logging.basicConfig(level=logging.DEBUG)
        agent.active = True
        agent.periodic(time.time())
        exit(0)

    kwargs = initializeProcessAgent(agent, argv)
    agent.setConfiguration(None, **kwargs)

    agent.run()
