#!/usr/bin/env python

# Copyright (C) 2012 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

from magi.testbed import testbed
from magi.util import database, config
from magi.util.execl import execAndRead
from magi.util.agent import agentmethod, ReportingDispatchAgent
from magi.util.processAgent import initializeProcessAgent
from runtimeStatsCollector import getRuntimeStatsCollector, PlatformNotSupportedException
from libdeterdash import DeterDashboard

import logging
import os
import sys
import time

log = logging.getLogger(__name__)

class NodeStatsReporter(ReportingDispatchAgent):
    """
        Agent which records general information about a node. There is a single table which is staic information
        and a dynamic table of CPU information. 
    """
    
    def __init__(self):
        ReportingDispatchAgent.__init__(self)
        self.runtime_info_collector = None
        self.active = False
        self.interval = 1
        self.truncate = True
        self.recordLimit = 0
        self.visualize = True
        self.collection = None

    def periodic(self, now):
        if self.active:
            log.debug("running periodic")

            (cpu_p, cpu_j) = self.runtime_info_collector.getCpuUsage()
            la1, la5, la15, latotal = self.runtime_info_collector.getLoadAverage()
            self.collection.insert({
                'table_type': 'ns_node',
                'cpu_usage' : cpu_p, 
                'cpu_jiffies' :cpu_j,
                'load_average_1' : la1, 
                'load_average_5' : la5, 
                'load_average_15' : la15, 
                'load_average_total' : latotal})
            
            # TODO: get daemon's process id
            #processId = os.getpid() 
            with open(config.getMagiPidFile()) as f:
                processId = int(f.read())
            
            log.debug("processId: %s" %(processId))
            
            childThreadIds = os.listdir("/proc/%s/task/" %(processId))
            childThreadIds = [int(x) for x in childThreadIds[0:]]
            childProcessIds = execAndRead('ps --ppid %d -o pid h' %processId)[0].split('\n')
            childProcessIds = [int(x) for x in childProcessIds[0:] if x]
            
            log.debug("childThreadIds: %s" %(childThreadIds))
            log.debug("childProcessIds: %s" %(childProcessIds))
           
            try: 
                uptime, users, create, swapper = self.runtime_info_collector.getUsersUptime()
                self.collection.insert({
                    'table_type': 'ns_users',
                    'uptime': uptime,
                    'users': users,
                    'creator': create,
                    'swapper': swapper
                })
                log.debug('Inserted uptime: {}, users: {}'.format(uptime, ', '.join(users)))
            except Exception as e:
                log.debug('error getting users/uptime: {}'.format(e))

            try:
                ports = self.runtime_info_collector.getOpenPorts()
                self.collection.insert({'table_type': 'ns_ports', 'ports': ports})
            except Exception as e:
                log.error('Error reading open ports: {}'.format(e))


            try:
                (cpu_p, cpu_j) = self.runtime_info_collector.getCpuUsage_process(processId)
                self.collection.insert({
                    'table_type': 'ns_process',
                    'process_id' : processId,
                    'cpu_usage' : cpu_p,
                    'cpu_jiffies': cpu_j})
            except:
                pass

            for threadId in childThreadIds:
                try:
                    (cpu_p, cpu_j) = self.runtime_info_collector.getCpuUsage_process(processId, threadId)
                    self.collection.insert({
                        'table_type': 'ns_process',
                        'process_id' : processId,
                        'thread_id' : threadId,
                        'cpu_usage' : cpu_p,
                        'cpu_jiffies': cpu_j})
                except:
                    pass

            for processId in childProcessIds:
                try:
                    (cpu_p, cpu_j) = self.runtime_info_collector.getCpuUsage_process(processId)
                    self.collection.insert({
                        'table_type': 'ns_process',
                        'process_id' : processId,
                        'thread_id' : -1,
                        'cpu_usage' : cpu_p,
                        'cpu_jiffies': cpu_j})
                except:
                    pass
            
            if self.recordLimit:
                # Keep only the latest
                self.collection.remove({'created': {'$lt': time.time() - (self.recordLimit * self.interval)}})
              
        else:
            log.debug("not active")

        ret = now + int(self.interval) - time.time()
        return ret if ret > 0 else 0

    def is_number(self, s):
        try:
            float(s)
            return True
        except ValueError:
            return False
            
    @agentmethod()
    def startCollection(self, msg):
        if not self.active:
            # for table_type in ['process', 'node', 'nodeinfo', 'users', 'ports']:
            self.collection = database.getCollection(self.name)

            if not self.runtime_info_collector:
                try:
                    self.runtime_info_collector = getRuntimeStatsCollector()
                except PlatformNotSupportedException:
                    log.critical('This platform is not supported for gather runtime stats')
                    return False
                
            if self.truncate:
                log.debug('truncating old records')
                self.collection.remove()

            self.collection.insert({
                "table_type" : 'ns_nodeinfo',
                "experiment" : testbed.getExperiment(), 
                "project" : testbed.getProject(), 
                "is_container" : testbed.amAVirtualNode()})

            # setup visualiztion. We only setup the 'node' table. We could set up the others if we wanted. 
            if self.visualize:
                dashboard = DeterDashboard()
                units = [
                    {'data_key': 'cpu_usage', 'display': 'CPU Usage', 'unit': None},
                    {'data_key': 'cpu_jiffies', 'display': 'CPU Jiffies', 'unit': None},
                    {'data_key': 'load_average_1', 'display': 'Load Avg. 1 Min', 'unit': None},
                    {'data_key': 'load_average_5', 'display': 'Load Avg. 5 Min', 'unit': None},
                    {'data_key': 'load_average_15', 'display': 'Load Avg. 15 Min', 'unit': None},
                    {'data_key': 'load_average_total', 'display': 'Load Avg. Total', 'unit': None}
                ]
                dashboard.add_time_plot('Node Stats', self.name, 'host', units)

            log.info('runtime stats collection started')
            
            self.active = True
        else:
            log.warning('start collection requested, but collection is already active')
            
        # return True so that any defined trigger gets sent back to the orchestrator
        return True

    @agentmethod()
    def stopCollection(self, msg):
        self.active = False
        log.info('runtime stats collection stopped')
        # return True so that any defined trigger gets sent back to the orchestrator
        return True


    def confirmConfiguration(self):
        try:
            self.interval= int(self.interval)
        except ValueError:
            log.error('Unable to convert integer value to int: %s', self.interval)
            return False

        return True

def getAgent(**kwargs):
    agent = NodeStatsReporter()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    agent = NodeStatsReporter()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()

#if __name__ == "__main__":
#    a = getAgent()
#    stats = getRuntimeStatsCollector()
#    while True:
#        print 'time: %s' % time.ctime(time.time())
#        print 'load average: %f, %f, %f, %d' % stats.getLoadAverage()
#        print 'cpu usage: %s%%' % stats.getCpuUsage()
#        time.sleep(2.5)

