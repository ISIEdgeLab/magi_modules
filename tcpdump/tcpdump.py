#!/usr/bin/env python

# Copyright (C) 2012, 2017 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

from magi.testbed import testbed
from magi.util.agent import agentmethod, DispatchAgent
from magi.util.processAgent import initializeProcessAgent
from subprocess import Popen, STDOUT, check_output, CalledProcessError
from netifaces import interfaces, ifaddresses
from socket import AF_INET, gethostbyname

from os.path import exists, isdir, basename, join as path_join
from os import makedirs
from time import sleep
from shutil import copyfile
import logging

log = logging.getLogger(__name__)

class TcpdumpAgentException(Exception):
    pass

class TcpdumpAgent(DispatchAgent):
    '''
    '''
    def __init__(self):
        DispatchAgent.__init__(self)
        self.dumpfile = path_join('/', 'tmp', 'tcpdump.cap')
        self.agentlog = path_join('/', 'tmp', 'tcpdump_agent.log')

        # "private"
        self._proc = None
        self._lfile = None

    @agentmethod()
    def startCollection(self, msg, expression, dumpfile=None, destination=None, 
                        tcpdump_args='', capture_address=None):
        if self._proc:
            log.info('tcpdump already running. Stopping it so we can restart it...')
            self.stopCollection(None)

        log.info("starting collection")
        cmd = 'tcpdump'

        if destination:
            iface = self._dest2iface(destination)
            if not iface:
                log.critical('Unable to discover interface that routes to {}.'.format(destination))
                return False

            cmd += ' -i {}'.format(iface)

        elif capture_address:
            iface = self._addr2iface(capture_address)
            if not iface:
                log.critical('Unable to find iface for address: {}'.format(capture_address))
                return False

            cmd += ' -i {}'.format(iface) 

        else:
            cmd += ' -i any'

        df = dumpfile if dumpfile else self.dumpfile
        cmd += ' -w {}'.format(df)
    
        if tcpdump_args:
            cmd += ' {}'.format(tcpdump_args)

        cmd += ' {}'.format(expression)

        log.info('running: {}'.format(cmd))

        # Do not remove the stdout, stderr redirection! It turns out tcpdump really doesn't
        # like not having a stdout/err and will die if this is removed.
        self._lfile = open(self.agentlog, 'w')
        try:
            self._proc = Popen(cmd.split(), close_fds=True, stdout=self._lfile, stderr=STDOUT)
        except OSError as e:
            self._lfile.close()
            self._proc = None
            log.critical('Unable to run cmd {}: {}'.format(cmd, e))
            return False

        sleep(1)
        if not self._proc or self._proc.poll():
            log.info('Could not start tcpdump')
            self._proc = None
            return False

        log.info('tcpdump started with process id {}'.format(self._proc.pid))
        return True
    
    @agentmethod()
    def stopCollection(self, msg):
        log.info("stopping collection")
        if not self._proc:
            log.warn('No process running. Igorning stop.')
            return True

        if self._proc:
            try:
                self._proc.terminate()
                sleep(1)
                self._proc.kill()
                self._proc.wait()
            except OSError as e:
                log.error('error stopping tcpdump process: {}'.format(e))

            self._lfile.close()
            self._proc = None

        return True
  
    @agentmethod()
    def archiveDump(self, msg, archivepath, dumpfile=None):
        create = True
        if exists(archivepath):
            if not isdir(archivepath):
                return False

            create = False

        if create:
            try:
                makedirs(archivepath)
                log.info('Created archive dir: {}'.format(archivepath))
            except OSError as e:
                log.info('Directory already exists.')
        
        df = dumpfile if dumpfile else self.dumpfile
        destname = '{}-{}-{}'.format(testbed.getNodeName(), self.name, basename(df))
        copyfile(df, path_join(archivepath, destname))
        log.info('Copied file: {} --> {}'.format(df, path_join(archivepath, destname)))
        return True

    def confirmConfiguration(self):
        return True

    def _addr2iface(self, inaddr):
        for iface in interfaces():
            addrs = ifaddresses(iface)
            if AF_INET in addrs:
                for addr in addrs[AF_INET]:
                    if inaddr == addr['addr']:
                        return iface

        return None

    def _dest2iface(self, destination):
        addr = gethostbyname(destination)
        if not addr:
            log.critical('Unable to get address for {}'.format(destination))
            return None

        cmd = 'ip route get {}'.format(addr)
        try:
            out = check_output(cmd.split())
        except CalledProcessError as e:
            log.critical('Error invoking "{}": {}'.format(cmd, e))
            return None

        line = out.split('\n')[0].split()
        iface = line[line.index('dev')+1]  # dev name is always just after "dev" 

        if not iface:
            log.critical('Unable to find device name in ip route output: {}'.format(line))
            return None
        
        return iface

def getAgent(**kwargs):
    agent = TcpdumpAgent()
    agent.setConfiguration(None, **kwargs)
    return agent

if __name__ == "__main__":
    from sys import argv
    
    # run directly if debugging.
    if '-d' in argv:
        from time import sleep
        logging.basicConfig(level=logging.DEBUG)
        a = getAgent()

        a.startCollection(None, 'host traf11', destination='traf11')
        sleep(10)
        a.stopCollection(None)
        a.archiveDump(None, '/tmp/archives')
        exit(0)

    # else run as process agent.
    agent = TcpdumpAgent()
    kwargs = initializeProcessAgent(agent, argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()
