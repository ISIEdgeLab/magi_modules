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

class GstreamerRTSPAgent(DispatchAgent):
    """
        Run Gstreamer RTSP server(s) and client(s).
    """
    def __init__(self):
        super(GstreamerRTSPAgent, self).__init__()

        # A flow is a list of client->server pairs:
        # ex: 
        #   self.flows = [
        #                   {'client': 'foo' 'server': 'baz'}, 
        #                   {'client': 'bar' 'server': 'koala'}, 
        #                ]
        self.flows = None
        self.client_args = ''
        self.start_port = 5000
        self.logdir = os.path.join('/', 'tmp', 'magi_gstreamer_rtsp')
        self.runname = ''       # include this in log file name if given.
        self.json = True        # if True, output json logs instead of text.

        # do not touch below here.
        self._proc = []
        self._isrunning = False
        self._logfd = None
        self._started_servers = {}

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
            filename = os.path.join(self.logdir, '{}_{}{}_gstreamer_rtsp.log'.format(
                strftime("%Y%m%d_%H%M%S", gmtime()),
                runname,
                testbed.nodename))
            self._logfd = open(filename, 'w')

        return self._logfd

    def _clear_logfd(self):
        if self._logfd:
            self._logfd.close()
            self._logfd = None

    def start_servers(self, msg):
        '''Start gstreamer RTSP servers.'''
        if len(self._proc) > 0:
            log.info('Stopping older RTSP servers.')
            self.stop_servers()
        port_offset = 0
        for f in self.flows:
            port_offset = port_offset + 1
            self._started_servers[f['server']] = self.start_port + port_offset
            cmd = None
            _proc = None
            if f['server'] == testbed.nodename:
                cmd = '/usr/local/bin/RTSPgenServer -p {}'.format(self.start_port + port_offset)
            if cmd:
                count = 5
                while count:
                    try:
                        log.info('running gstreamer RTSP server as: "{}"'.format(cmd))
                        fd = self._get_logfd()
                        _proc = Popen(cmd.split(), stdout=fd, stderr=STDOUT, close_fds=True)
                    except CalledProcessError as e:
                        log.error('Unable to start RTSP gstreamer process: {}'.format(e))
                        self._clear_logfd()
                        _proc = None
                    if self._proc:
                        sleep(1)
                        if _proc.poll():
                            log.info('Error starting gstreamer RTSP server. Trying again.')
                        else:
                            break
                    log.info('Unable to start gstreamer RTSP server, trying again in a fwe seconds...')
                    count = count-1
                    sleep(1)
                if _proc != None:
                    log.info('gstreamer RTSP server %s:%d started' % (f['server'], self.start_port + port_offset))
                    self._proc.append(_proc)
                else:
                    log.warn('Problem starting server on %s:%d' %(f['server'], self.start_port + port_offset))

    def startTraffic(self, msg):
        '''Start gstreamer RTSP clients.'''
        if len(self._proc) > 0:
            log.info('Stopping older gstreamer RTSP processes.')
            self.stopTraffic(msg)
        
        self.start_servers(msg)
        
        port_offset = 0    
        for f in self.flows:
            cmd = None
            _proc = None
            port_offset = port_offset + 1
            if f['client'] == testbed.nodename:
                if f['server'] in self._started_servers:
                    port = self._started_servers[f['server']]
                    cmd = '/usr/local/bin/RTSPgenClient -p {} -s {}'.format(port, f['server'])
                else:
                    log.error('Cannot determine port of server. Was server (%s) started?' % f['server'])

            if cmd:
                # try a few times in case the servers have not started.
                count = 5
                while count:
                    try:
                        log.info('running gstreamer RTSP as: "{}"'.format(cmd))
                        fd = self._get_logfd()
                        _proc = Popen(cmd.split(), stdout=fd, stderr=STDOUT, close_fds=True)
                    except CalledProcessError as e:
                        log.error('Unable to start RTSP gstreamer process: {}'.format(e))
                        self._clear_logfd()
                        _proc = None
                  
                    if _proc:
                        sleep(1)    # let it fail or no.
                        if _proc.poll():   # poll() returns None if proc running else exit value.
                            log.info('Error starting gstreamer RTP. Trying again.')
                        else:
                             break

                    log.info('Unable to start gstreamer RTSP trying again in a few seconds....')
                    count = count-1
                    sleep(1)
                if _proc != None:
                    log.info('gstreamer RTSP client started on %s' % (f['client']))
                    self._proc.append(_proc)
                else:
                    return False
        return True


    def stopTraffic(self, msg):
        for proc in self._proc:
            if proc and not proc.poll():
                try:
                    log.info('killing gstreamer RTSP client')
                    proc.kill()
                except OSError:
                    pass   # meh.

        self._clear_logfd()
        self._proc = []

        # Just to be safe. 
        try:
            log.info('pkilling gstreamer RTSP')
            # XXX not as specific as it should be.
            call('pkill -f "RTSPgenClient"'.split(), shell=True)
            call('pkill -f "RTSPgenServer"'.split(), shell=True)
        except CalledProcessError as e:
            log.info('error pkilling gstreamer RTSP clients: {}'.format(e))
            pass

        return True

def getAgent(**kwargs):
    agent = GstreamerRTSPAgent()
    if not agent.setConfiguration(None, **kwargs):
        msg = 'Bad configuration given to agent'
        log.critical(msg)
        raise(Exception(msg))  # Don't know how else to get Magi's attention here.

    return agent

# handle process agent mode.
if __name__ == "__main__":
    agent = GstreamerRTSPAgent()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
