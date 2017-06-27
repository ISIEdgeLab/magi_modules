#!/usr/bin/env python

from magi.util.agent import DispatchAgent
from magi.util.processAgent import initializeProcessAgent
from magi.testbed import testbed
from time import gmtime, strftime
from shutil import copy
from time import sleep
import os.path
import os
import stat

from subprocess import CalledProcessError, Popen, STDOUT, call

import logging

log = logging.getLogger(__name__)

class IperfAgent(DispatchAgent):
    """
        Run iperf3 on clients and servers.
    """
    def __init__(self):
        super(IperfAgent, self).__init__()

        # A flow is a list of client->server pairs:
        # ex: 
        #   self.flows = [
        #                   {'client': 'foo' 'server': 'baz'}, 
        #                   {'client': 'bar' 'server': 'koala'}, 
        #                ]
        self.flows = None
        self.client_args = ''
        self.logdir = os.path.join('/', 'tmp', 'iperf')
        self.runname = ''       # include this in log file name if given.
        self.json = True        # if True, output json logs instead of text.

        # do not touch below here.
        self._proc = {}
        self._isrunning = False
        self._logfd = None

        self._loglevel = 'info'

    def confirmConfiguration(self):
        log.info('Checking given configuration...')
        if not self.logdir:
            log.critical('Logdir not set, unable to continue.')
            return False

        if not self.flows:
            log.critical('No flows given, unable to continue.')
            return False

        if not os.path.isdir(self.logdir):
            log.info('{} not found, creating it.'.format(self.logdir))
            try:
                os.mkdir(self.logdir)
                os.chmod(self.logdir, stat.S_IRWXU | stat.S_IRWXG | stat.S_IROTH)
            except (OSError, IOError) as e:
                log.critical('Unable to create {}: {}'.format(self.logdir, e))
                return False
       
        # python v3.2+ we would not need to do this.
        levelmap = {'debug': logging.DEBUG, 'info': logging.INFO,
                    'warning': logging.WARNING, 'error': logging.ERROR,
                    'critical': logging.CRITICAL}
        if not self._loglevel.lower() in levelmap.keys():
            log.warning('I do not know how to set log level {}'.format(
                self._loglevel))
            return FALSE

        log.setLevel(levelmap[self._loglevel.lower()])

        return True

    def _get_logfd(self):
        if not self._logfd:
            # append _ to runname if there. 
            runname = '{}_'.format(self.runname) if self.runname else ''
            filename = os.path.join(self.logdir, '{}_{}{}_iperf.log'.format(
                strftime("%Y%m%d_%H%M%S", gmtime()),
                runname,
                testbed.nodename))
            self._logfd = open(filename, 'w')

        return self._logfd

    def _clear_logfd(self):
        if self._logfd:
            self._logfd.close()
            self._logfd = None

    def start_traffic(self, msg):
        '''Start iperf everywhere.'''
        if self._proc:
            log.info('Stopping older iperf3 process.')
            self.stop_traffic(msg)
            
        for f in self.flows:
            cmd = None
            if f['server'] == testbed.nodename:
                cmd = 'iperf3 -s '
            elif f['client'] == testbed.nodename:
                cmd = 'iperf3 -c {} {}'.format(f['server'], self.client_args)

            # iperf3 does not handle io buffering correctly, but it does seem to when --verbose
            # is given. If we leave this out, we will frequently not capture all lines in the log
            # file. 
            if cmd:
                cmd += ' --verbose'

                if self.json:
                    cmd += ' -J'

            if cmd:
                # try a few times in case teh servers have not started.
                count = 5
                while count:
                    try:
                        log.info('running iperf as: "{}"'.format(cmd))
                        fd = self._get_logfd()
                        self._proc = Popen(cmd.split(), stdout=fd, stderr=STDOUT, close_fds=True)
                    except CalledProcessError as e:
                        log.error('Unable to start iperf process: {}'.format(e))
                        self._clear_logfd()
                        self._proc = None
                  
                    if self._proc:
                        sleep(1)    # let it fail or no.
                        if self._proc.poll():   # poll() returns None if proc running else exit value.
                            log.info('Error starting iperf. Trying again.')
                        else:
                             break

                    log.info('Unable to start iperf trying again in a few seconds....')
                    count = count-1
                    sleep(1)

                log.info('iperf started')
                break

        return self._proc != None

    def stop_traffic(self, msg):
        if self._proc and not self._proc.poll():
            try:
                log.info('killing iperf3')
                self._proc.kill()
            except OSError:
                pass   # meh.

        self._clear_logfd()
        self._proc = None

        # Just to be safe. 
        try:
            log.info('pkilling iperf3')
            call('pkill iperf3'.split(), shell=True)
        except CalledProcessError as e:
            log.info('error pkilling iperf: {}'.format(e))
            pass

        return True

def getAgent(**kwargs):
    agent = IperfAgent()
    if not agent.setConfiguration(None, **kwargs):
        msg = 'Bad configuration given to agent'
        log.critical(msg)
        raise(Exception(msg))  # Don't know how else to get Magi's attention here.

    return agent

# handle process agent mode.
if __name__ == "__main__":
    agent = IperfAgent()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
