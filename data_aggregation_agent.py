#!/usr/bin/env python

import logging
import time

from magi.util.agent import ReportingDispatchAgent, agentmethod
from magi.util.processAgent import initializeProcessAgent
from magi.util import database
from magi_db import MagiDatabase
from libdeterdash import DeterDashboard

log = logging.getLogger(__name__)

class DataAggregationAgentException(Exception):
    pass

class DataAggregationAgent(ReportingDispatchAgent):
    """
	"""
    def __init__(self):
        ReportingDispatchAgent.__init__(self)

        # set enclaves for testing. Production should set this via AAL.
        self.enclaves = None

        # A valid python expression/function. "data_list" will be the 
        # data to aggregate (as a list) and available in the global
        # "namespace" of the expression.
        self.reduce_method = 'sum(L)/len(L)'  # average

        self.agent_key = None           # may be a single value or a list.
        self.data_key = None
        self.node_key = 'host'          # only change if your node is under a different key.
        self.aggregation_period = 1     # period across which to aggregate the data (in seconds).

        self.lag = 10.0    # look 10 seconds in teh past for data.

        # "private" variables. 
        self._active = False

        # will raise exception on error.
        # best to only call this once as it reads/parses a file. (slow)
        self._db = MagiDatabase().db()
        
        self._viz_configured = False

    def periodic(self, now):
        if not self._active:
            return self.aggregation_period+now-time.time()

        # GTL - self.name is not set until this thread is run().   
        if not self.name:
            raise DataAggregationAgentException('self.name is empty, unable to continue.')
        
        # have to wastefully do this here as self.name is not set before this. Ugh.
        if not self._viz_configured:
            # tell the GUI we want to display this data.
            dashboard = DeterDashboard()
            units = [{'data_key': self.data_key,
                      'display': 'Bytes',          # requires knowledge of the aggregated agent.
                      'unit': 'bytes/sec'}]        # need to abstract this somehow...
            dashboard.add_horizon_chart('Aggregated', self.name, 'enclave', units)
            self._viz_configured = True

        self._collection = database.getCollection(self.name)

        # get access to the agent's collection for reading.
        log.debug('Checking for aggregatable data in {}/{}.'.format(self.agent_key, self.data_key))

        # find new data using given table and key and timestamp.
        for name, nodes in self.enclaves.iteritems():
            # log.debug('Looking for aggregatable data for enclave {} with nodes {}.'.format(name, nodes))
            # log.debug('Searching for {} within {} around {} for nodes in key {}'.format(
            #     self.data_key, self.agent_key, now, self.node_key))
            args = { 
                'agent': {'$in': self.agent_key},
                self.node_key: {'$in': nodes},
                'created': {'$gte': float(now-self.aggregation_period-self.lag), '$lt': float(now-self.lag)}
            }

            filter_ = {
                '_id': False,
                self.data_key: True 
            }
            log.debug('search: {}\nfilter: {}'.format(args, filter_))
            cursor = self._db.experiment_data.find(args, filter_)
            agg_data = []
            for entry in cursor:
                if self.data_key in entry:   # sometimes there is not a matching key. Dunno why.
                    agg_data.append(entry[self.data_key])

            log.debug('adding data to {} aggregate: {}'.format(name, agg_data))

            if agg_data:
                # apply reduction to the list of aggregated data.
                try:
                    agg_value = eval(self.reduce_method, {'L': agg_data})
                except Exception as e:   # eval is dangerous
                    log.error('Error evaluating aggregation method: {}'.format(e))

                log.debug('adding aggregated value to {}'.format(name, agg_value))
            else:
                agg_value = 0.0

            # ...and insert into our collection.
            self._collection.insert({
                self.data_key: agg_value,
                'enclave': name
            })

        # call this again after period has elapsed. 
        ret = self.aggregation_period+now-time.time()
        return ret if ret > 0 else 0


    @agentmethod()
    def confirmConfiguration(self):
        for key in ['enclaves', 'agent_key', 'data_key']:
            if not getattr(self, key, None):
                log.critical('No "{}" given in AAL. Unable to continue.'.format(key))
                return False

        # listify the agent key as we expect a list later.
        self.agent_key = self.agent_key if type(self.agent_key) == list else [self.agent_key]

        return True

    def startCollection(self, msg):
        self._active = True

    def stopCollection(self, msg):
        self._active = False

def getAgent(**kwargs):
    agent = DataAggregationAgent()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    from sys import argv
    agent = DataAggregationAgent()
    kwargs = initializeProcessAgent(agent, argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
