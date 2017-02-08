#!/usr/bin/env python

# Copyright (C) 2016 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

from magi.util.agent import agentmethod, DispatchAgent
from magi.util.processAgent import initializeProcessAgent
from magi.testbed import testbed

from subprocess import Popen, STDOUT, CalledProcessError, check_call

import logging

log = logging.getLogger(__name__)

class RunProcAgent(DispatchAgent):
    '''
        Just run a command line as a process, redirecting stdout/stderr to the given file. 
    '''
    def __init__(self):
        DispatchAgent.__init__(self)
        self.proc = None
        self.logfile = None
        self._lfd = None    # log file descriptor
        
    @agentmethod()
    def run(self, msg, cmd, logfile, blocking):
        log.info('running: {} > {}'.format(cmd, logfile))

        # stop possibly running process. 
        self.stop()
    
        try:
            self._lfd = open(self.logfile, 'w')
        except IOError as e:
            log.critical('Unable to open logfile for writing, {}'.format(logfile))
            return False

        try:
            if blocking:
                ret = call(cmd.split(), close_fds=True, stdout=self._lfd, stderr=STDOUT, shell=False)
            else:
                self.proc = Popen(cmd.split(), close_fds=True, stdout=self._lfd, stderr=STDOUT, shell=False)
        except CalledProcessError as e:
            log.critical('error running cmd: {}'.format(e))
            return False

        return True
    
    @agentmethod()
    def stop(self, msg):
        if self.proc:
            log.info('killing running process')
            self.proc.kill()
        
        self._close_log()
    
    def _close_log(self):
        if self._lfd:
            log.info('closing logfile {}'.format(self.logfile))
            self._lfd.close()
            self._lfd = None
            self.logfile = None

def getAgent(**kwargs):
    agent = RunProcAgent()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    agent = RunProcAgent()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
