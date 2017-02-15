#!/usr/bin/env python
from magi.util.agent import DispatchAgent
from magi.util.processAgent import initializeProcessAgent
from magi.testbed import testbed
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

class GstreamerRTPAgent(DispatchAgent):
    """
        Run Gstreamer RTP server(s) and client(s).
    """
    def __init__(self):
        super(GstreamerRTPAgent, self).__init__()

        # A flow is a list of client->server pairs:
        # ex: 
        #   self.flows = [
        #                   {'client': 'foo' 'server': 'baz'}, 
        #                   {'client': 'bar' 'server': 'koala'}, 
        #                ]
        self.flows = None
        self.client_args = ''
        self.server_args = ''
        self.start_port = 5000
        self.logdir = os.path.join('/', 'tmp', 'magi_gstreamer_rtp')
        self.runname = ''       # include this in log file name if given.

        # do not touch below here.
        self._procs = []
        self._isrunning = False
        self._logfds = []

        self._loglevel = 'info'

    def confirmConfiguration(self):
        # python v3.2+ we would not need to do this.
        levelmap = {'debug': logging.DEBUG, 'info': logging.INFO,
                    'warning': logging.WARNING, 'error': logging.ERROR,
                    'critical': logging.CRITICAL}
        if not self._loglevel.lower() in levelmap.keys():
            log.warning('I do not know how to set log level {}'.format(
                self._loglevel))
            return False                                                                   
        log.setLevel(levelmap[self._loglevel.lower()])         

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
        return True

    def _get_logfd(self, runport):
        # append _ to runname if there. 
        runname = '{}_'.format(self.runname) if self.name else 'rtp'
        filename = os.path.join(self.logdir, '{}_{}_{}_{}.log'.format(
            strftime("%Y%m%d-%H%M%S", localtime()),
            runname,
            testbed.nodename,
            runport))
        log.info("Trying %s for log name." % filename)
        try:
            logfd = open(filename, 'w')
            self._logfds.append(logfd)
            return logfd
        except:
            log.warn("Unable to log some processes.")
        return None

    def _clear_logfds(self):
        for logfd in self._logfds:
            try:
                logfd.flush()
                logfd.close()
                logfd = None
            except:
                pass
                
    def startTraffic(self, msg):
        '''Start gstreamer RTP servers/clients.'''
        if len(self._procs) > 0:
            log.info('Stopping older gstreamer RTP processes (Found %d).' % (len(self._procs)))
            self.stopTraffic(msg)
        
        port_offset = 0    
        log.info("Should start %d flows." % len(self.flows))
        for f in self.flows:
            cmd = None
            if f['client'] == testbed.nodename:
                cmd = 'RTPgenClient -p {} {}'.format(self.start_port + port_offset, self.client_args)
            elif f['server'] == testbed.nodename:
                cmd = 'RTPgenServer -p {} -c {} {}'.format(self.start_port + port_offset, f['client'], self.server_args)

            if cmd:
                log.info("Trying command %s" % cmd)
                # try a few times in case the servers have not started.
                count = 5
                while count:
                    try:
                        log.info('Running gstreamer RTP as: "{}"'.format(cmd))
                        fd = self._get_logfd(self.start_port + port_offset)
                        proc = None
                        proc = Popen(cmd.split(), stdout=fd, stderr=STDOUT, close_fds=True)
                        self._procs.append(proc)
                    except CalledProcessError as e:
                        log.error('Unable to start RTP gstreamer process: {}'.format(e))
                  
                    if proc:
                        sleep(1)    # let it fail or no.
                        if proc.poll():   # poll() returns None if proc running else exit value.
                            log.info('Error starting gstreamer RTP. Trying again.')
                        else:
                             break

                    log.info('Unable to start gstreamer RTP trying again in a few seconds....')
                    count = count-1
                    sleep(1)
            else:
                log.warn('Unable to determine command to start on this instance.')
            
            port_offset = port_offset + 1
        
        if len(self._procs) > 0:
            log.info("%d processes started." % len(self._procs))
            return True
        else:
            log.warn("Failed to start any processes.")
            return False

    def stopTraffic(self, msg):
        log.info("Stopping traffic. Have %d processes to kill." % (len(self._procs)))
        for proc in self._procs:
            if proc and not proc.poll():
                # First try to send a break so program can clean up.
                try:
                    log.info('warning program of upcoming end')
                    proc.send_signal(signal.SIGINT)
                    sleep(1)
                except OSError:
                    pass # uh, do something?
                try:
                    log.info('killing gstreamer RTP')
                    proc.kill()
                    if proc.poll():
                        self._procs.remove(proc)
                except OSError as e:
                    log.warn("Error while killing proc %s" % (e))
                    pass   # meh.
        if len(self._procs) > 0:
            log.warn("Failed to kill %d processes." % (len(self._procs)))
        else:
            log.info("Killed all processes.")

        self._clear_logfds()

        # Just to be safe. 
        try:
            log.info('pkilling gstreamer RTP')
            call('pkill -f "RTPgenClient"'.split(), shell=True)
            call('pkill -f "RTPgenServer"'.split(), shell=True)
        except CalledProcessError as e:
            log.info('error pkilling gstreamer RTP: {}'.format(e))
            pass

        return True

def getAgent(**kwargs):
    agent = GstreamerRTPAgent()
    #if not agent.setConfiguration(None, **kwargs):
    #    msg = 'Bad configuration given to agent'
    #    log.critical(msg)
    #    raise(Exception(msg))  # Don't know how else to get Magi's attention here.

    return agent

# handle process agent mode.
if __name__ == "__main__":
    agent = GstreamerRTPAgent()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
