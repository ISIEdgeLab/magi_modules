#!/usr/bin/env python
from magi.util.agent import ReportingDispatchAgent
from magi.util.processAgent import initializeProcessAgent
from magi.testbed import testbed
from magi.util import database   
from libdeterdash import DeterDashboard
from time import gmtime, strftime, localtime
from shutil import copy
from time import sleep
import os.path
import os
import sys
import signal
import stat

from subprocess import CalledProcessError, Popen, STDOUT, call
import logging

logging.basicConfig()
log = logging.getLogger(__name__)

class GstreamerRTPAgentViz(ReportingDispatchAgent):
    def __init__(self):
        super(GstreamerRTPAgentViz, self).__init__()
        self.file_to_check = None
        self._db_configured = False
    
    def confirmConfiguration(self):
        return True
    
    def periodic(self, now):
        # Do this here so we have self.name defined (not defined in init yet).
        if not self._db_configured:
             self._configure_database()
             
        print("Reporting %s." % strftime("%Y%m%d_%H%M%S", localtime()))
        self._save_progress_metrics()
        return 1

    def _configure_database(self):
        self._collection = database.getCollection(self.name)
        self._collection_progress = database.getCollection(self.name + '_progress')
        self._collection_error = database.getCollection(self.name + '_error')
        
        dashboard = DeterDashboard()
        units = [ 
            {'data_key': 'frame_rate', 'display':'Frames per Second', 'unit':'frames'},
        ]                
        dashboard.add_time_plot('RTP Agent', self.name, 'host', units)
        self._db_configured = True
    
    def _save_progress_metrics(self):
        self._collection_progress.insert({
            'frame_rate': 30,
        })    

def getAgent(**kwargs):
    agent = GstreamerRTPAgentViz()
    if not agent.setConfiguration(None, **kwargs):
        msg = 'Bad configuration given to agent'
        log.critical(msg)
        raise(Exception(msg))  # Don't know how else to get Magi's attention here.
    return agent

# handle process agent mode.
if __name__ == "__main__":
    agent = GstreamerRTPAgentViz()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
