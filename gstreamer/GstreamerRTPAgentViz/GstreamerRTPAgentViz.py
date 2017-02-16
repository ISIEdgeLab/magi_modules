#!/usr/bin/env python

# Copyright (C) 2012 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

from magi.testbed import testbed
from magi.util import database, config
from magi.util.execl import execAndRead
from magi.util.agent import agentmethod, ReportingDispatchAgent
from magi.util.processAgent import initializeProcessAgent
#from runtimeStatsCollector import getRuntimeStatsCollector, PlatformNotSupportedException
from libdeterdash import DeterDashboard

import logging
import os
import sys
import time
import random

log = logging.getLogger(__name__)

class FileInfo(object):
    # Not many of these, so no need for defining __slots__ directly.
    file_name = ''
    last_report_time = -1
    fd = None

class GstreamerRTPAgentViz(ReportingDispatchAgent):
    """
        Since gstreamer tools are separate, individually runnable tools we cannot integrate
        MAGI and Viz tools directly. 
        
        This agent reads log files from VidGen tools to read data for other Viz and MAGI tools.
    """
    
    def __init__(self):
        ReportingDispatchAgent.__init__(self)
        self.active = False
        self.interval = 1
        self.name = 'RTPViz'
        self.dir_to_check = None
        # We ignore files older than watch_range minutes.
        self.watch_range = 15
        self._watched_files = {}
        self._db_configured = False
        self._loglevel = 'info' 

    def periodic(self, now):
        if self.active:
            if not self._db_configured:
                self._configure_database()
            log.info("Calling progress metric.")
            self._check_files()
        #else:
        #    log.info("Not reporting.")

        next_now = now + int(self.interval) - time.time()
        if next_now > 0:
            return next_now
        else:
            return 0
            
    def _check_files(self):
        if not self.active:
            return
        now = int(time.time())
        newlines = []
        fpses = []
        # See if any new files showed up that we should watch.
        for file in [f for f in os.listdir(self.dir_to_check) if os.path.isfile(os.path.join(self.dir_to_check, f))]:
            mt_time = int(os.path.getmtime(os.path.join(self.dir_to_check, file)))
            # If this file's been modified in the last self.watch_range, we should check this file.
            if mt_time + (self.watch_range * 60) >= now and file not in self._watched_files:
            #if file not in self._watched_files:
                log.info("Adding %s to list of watched files." % file)
                fi = FileInfo()
                try:
                    fi.fd = open(os.path.join(self.dir_to_check, file), 'r')
                    # Jump to the end of the file.
                    fi.fd.seek(0, 2)
                    fi.file_name = file
                    self._watched_files[file] = fi
                except Exception as e:
                    log.warn("Problem reading from %s/%s: %s" % (self.dir_to_check,f, e))
                    del fi
                    pass
                    
        log.info("Watching %d files." % (len(self._watched_files)))
        x=0
        try:
            for file in self._watched_files:
                x=x+1
                log.info("Watching %s" % self._watched_files[file].file_name)
                if self._watched_files[file].fd != None:
                    log.info("Have opened %s" % self._watched_files[file].file_name)
                    try:
                        line = None
                        line = self._watched_files[file].fd.readline()
                        if line:
                            newlines.append(line)
                    except Exception as e:
                        log.warn("Problem reading from %s. Could not get last new line:%s" % (self._watched_files[file], e))
                        pass
        except Exception as e:
            log.error("%s" % e)
            raise e
        for line in newlines:
            if 'FPS:' in line:
                try:
                    fps = line.split('FPS:')[-1].split()[0]
                    fpses.append(int(fps))
                except Exception as e:
                    log.warn("Problem extracting stats from %s. Could not parse line. :%s" % (line, e))
                    pass  
        if len(fpses) > 0:
            avgfps = float(sum(fpses))/float(len(fpses))
            log.info("Avg FPS is: %.02f" % avgfps)
            self._save_metrics(avgfps)
        else:
            log.info("No stats to report.")
                    
    def _configure_database(self):
        log.info("Configuring database with name \"%s\"." % self.name)
        self._collection = database.getCollection(self.name)
        self._collection_error = database.getCollection(self.name + '_error')
        
        dashboard = DeterDashboard()
        units = [ 
            {'data_key': 'frame_rate', 'display':'Frames per Second', 'unit':'frames'},
        ]
        dashboard.add_time_plot('RTP Agent', self.name, 'host', units)
        self._db_configured = True
        log.info("Configured database.")
    
    def _save_metrics(self, avgfps):
        self._collection.insert({
            'frame_rate': avgfps,
        })
        log.info("Saving progress stats")

    @agentmethod()
    def startReporting(self, msg):
        self.active = True
        return

    def confirmConfiguration(self):
        log.info('Checking given configuration...')
        if self.dir_to_check == None:
            log.critical('Not given directory to watch and find data.')
            return False
        try:
            self.interval= int(self.interval)
        except ValueError:
            log.error('Not given reporting interval in integer seconds: %s', self.interval)
            return False
        return True
        

def getAgent(**kwargs):
    agent = GstreamerRTPAgentViz()
    if agent:
        agent.setConfiguration(None, **kwargs)

    return agent

if __name__ == "__main__":
    agent = GstreamerRTPAgentViz()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()

