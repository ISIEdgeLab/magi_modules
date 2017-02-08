#!/usr/bin/env python

import logging
from os.path import isfile, join
from os import access, R_OK
from time import sleep

from subprocess import Popen, STDOUT, CalledProcessError
from magi.util.agent import ReportingDispatchAgent
from magi.util.processAgent import initializeProcessAgent
from magi.testbed import testbed

log = logging.getLogger(__name__)

# We inherit from ReportingDispatchAgent so we get a status callback
# from the magi daemon. We use this callback check for the end of the 
# spawned mgen process and cleanup when it's exited. 
class mgen_agent(ReportingDispatchAgent):
    """
        Simple aent for spawning an MGEN process with a given configuration file. 
	"""
    def __init__(self):
        super(mgen_agent, self).__init__()

        self.config_dir = None

        # If log is not specified, /tmp/mgen.log will be used. 
        self.log = None
        self.log_append = False

        # "private" below here.
        self._proc = None
        self._logfd = None

    def get_config_path(self):
        return join(self.config_dir, '{}.mgen'.format(testbed.nodename))

    def confirmConfiguration(self):
        # Make sure the config files exists and is readable. 
        if not self.config_dir:
            log.critical('Configuration dir not set in mgen_agent. Unable to continue.')
            return False

        cfg = self.get_config_path()
        if not isfile(cfg):
            log.critical('MGEN configuration file {} does not exist. Unable to continue.'.format(cfg))
            return False

        if not access(cfg, R_OK):
            log.critical('MGEN configuration file {} is not readable. Unable to continue.'.format(cfg))
            return False

        self.log = self.log if self.log else join('/', 'tmp', 'mgen.log')

        # \o/
        return True

    def clean(self):
        self._proc = None
        if self._logfd:
            self._logfd.close()
        self._logfd = None

    def start(self, msg):
        '''Start the MGEN process with the given configuration file. Restart the process if it's running.'''
        if self._proc:
            self.stop()

        self._logfd = open(self.log, 'w')
        if not self._logfd:
            log.critical('Unable to open MGEN log file {}. Unable to continue.'.format(self._logfd))
            return False

        try:
            cfg = self.get_config_path()
            self._proc = Popen('mgen input {}'.format(cfg).split(), stdout=self._logfd,
                               stderr=STDOUT, close_fds=True)
        except (CalledProcessError, OSError) as e:
            log.critical('Error running MGEN: {}'.format(e))
            self.clean()
            return False

        # check for immediate failure.
        sleep(0.5)  # GTL - blech
        if self._proc.poll():
            if self._proc.returncode != 0:
                log.critical('MGEN failed to start on node {}. Exit val: {}. Check the log at {}:{}'.format(
                    testbed.nodename, self._proc.returncode, testbed.nodename, self.log))
                self.clean()
                return False

        # Looks good.
        return True

    def stop(self, msg):
        '''Stop the MGEN process if it's running.'''
        if self._proc:
            log.debug('Killing running MGEN process ({})'.format(self._proc.pid))
            self._proc.kill()
        
        self.clean()
        return True

    def periodic(self, now):
        log.debug('Checking for end of mgen process.')
        if self._proc:
            if self._proc.poll() is not None:  # process complete.
                log.info('MGEN process complete. Cleaning up.')
                self.clean()
            else:
                log.debug('MGEN still running: {}'.format(self._proc))
        else:
            log.debug('MGEN not running.')

        return (1.0)  # check every second.

def getAgent(**kwargs):
    agent = mgen_agent()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    from sys import argv
    agent = mgen_agent()
    kwargs = initializeProcessAgent(agent, argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
