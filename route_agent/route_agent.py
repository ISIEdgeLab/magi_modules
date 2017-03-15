#!/usr/bin/env python

# Copyright (C) 2012 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

from magi.testbed import testbed
from magi.util import database
from magi.util.agent import agentmethod, ReportingDispatchAgent
from magi.util.processAgent import initializeProcessAgent
from route_data import RouteData, RouteDataException

from libdeterdash import DeterDashboard

import logging
import os
import time
import json

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
        self.recordLimit = 0            # If True, only keep the newest data in the database. 
        self.active_topology = True     # If True, update the topology as routes shift.
                                        # If False, update once during first periodic call.

        # do not change these. 
        self.active = False
        self._collection = {}
        self._update_topo = True
        self._viz_configured = False

        # Do we want to expose this API to let people modify "control nets"?
        self._routeData = RouteData()

    def periodic(self, now):
        '''Update the database with the newest routes found on the node.'''
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
                    for host, routes in tables.iteritems():
                        log.debug('Inserting {} routes: {}'.format(collection, routes))
                        # Need to specify 'router' as 'host' may be a "fake" click router node.
                        self._collection.insert({
                            'router': host,
                            'routes': routes,
                            'table_type': collection
                        })

            # handle updates to topology and network edges.
            self._update_topology()
            self._update_network_edges()

            # just let the route data update the db directly if it wants to.
            self._routeData.insert_stats(self._collection)
            
        ret = now + int(self.interval) - time.time()
        return ret if ret > 0 else 0

    @agentmethod()
    def startCollection(self, msg):
        if not self.active:
            for table_type in ['routes', 'point2point']:
                log.info('Getting collection: {}'.format(self.name))
                self._collection = database.getCollection(self.name)

            if self.truncate:
                log.debug('truncating old records')
                log.info('Truncating routing data in database.')
                self._collection.remove()

            log.info('route recording started')
            self.active = True

            self._collection.insert({'name': testbed.nodename, 'types': self._routeData.get_node_types()})
         
        else:
            log.warning('start collection requested, but collection is already active')
            
        # have to wastefully do this here as self.name is not set before this. Ugh.
        if not self._viz_configured:
            # tell the GUI we want to display this data using a (node and edges) graph.
            dashboard = DeterDashboard()
            # these, ugh, hard coded values can be found in self._update_topology. 
            dashboard.add_topology('Routing', self.name, 'nodes', 'edges',
                                   extra_keys={'table_type': 'topology'})
            self._routeData.init_visualization(dashboard, self.name)
            self._viz_configured = True

        # return True so that any defined trigger gets sent back to the orchestrator
        return True

    @agentmethod()
    def stopCollection(self, msg):
        if self.active:
            log.info('stopping route recording')

        self.active = False
        # return True so that any defined trigger gets sent back to the orchestrator
        return True

    @agentmethod()
    def confirmConfiguration(self):
        try:
            self.interval = int(self.interval)
        except ValueError:
            log.error('Unable to convert integer value to int: %s', self.interval)
            return False

        return True

    def _update_network_edges(self):
        '''There are areas of the topology which are virtual, click, and to a lesser extent, containers. 
        This is a hook to fix these edges as the physical edges no nothing about the virtual nodes. So 
        we ask the virtual networks for the node mapping between edges and fix them here, before they are 
        put into the database. Ugly, but whattya gonna do?'''
        edge_map = self._routeData.get_network_edges()
        if edge_map:
            collection = database.getCollection(self.name)
            collection.insert({
                'table_type': 'network_edge_map',
                'map': edge_map
            })

    def _update_topology(self):
        '''Click only. Replace 'vrouter' machine with click network in system topology.'''
        if self._update_topo:
            self._update_topo = self.active_topology

            topo_updates = self._routeData.get_topology_updates()
            if topo_updates:
                col =  database.getCollection('topo_agent')
                cursor = col.find()
                if cursor:
                    topo = cursor[0]
                    nodes = json.loads(topo['nodes'])  # nodes are kept as json list in db?!?
                    edges = json.loads(topo['edges'])  # ?!?! edges are list of 2 items lists.

                    # add/remove our updates to/from the topo_agent entries. 
                    for node in topo_updates['remove']:
                        # remove all entries for 'node'
                        nodes = [n for n in nodes if n != node]
                        edges = [e for e in edges if node not in e]

                    for node_a, node_b in topo_updates['add']:
                        # add new edge entry
                        if node_a not in nodes:
                            nodes.append(node_a)

                        if node_b not in nodes:
                            nodes.append(node_b)

                        if [node_a, node_b] not in edges:
                            edges.append([node_a, node_b])

                self._collection.insert({
                    'table_type': 'topology',
                    'nodes': nodes,
                    'edges': edges})   


def getAgent(**kwargs):
    agent = RouteReportAgent()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    from sys import argv 

    debug = True if '-d' in argv else False
    log_level = logging.DEBUG if '-v' in argv else logging.INFO

    logging.basicConfig(level=log_level)
    agent = RouteReportAgent()

    if debug:
        agent.periodic(time.time())
        exit(0)

    kwargs = initializeProcessAgent(agent, argv)
    agent.setConfiguration(None, **kwargs)

    agent.run()
