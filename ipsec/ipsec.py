from magi.util.agent import agentmethod, DispatchAgent
from magi.util.execl import run
import logging
import socket
import os

"""
Agent for configuring IPsec with strongswan.

Given a set of endpoint hosts, creates a fully connected mesh of IPsec tunnels.
"""

def getAgent(**kwargs):
   agent = ipsec()
   agent.setConfiguration(None, **kwargs)
   return agent

log = logging.getLogger(__name__)

class ipsec(DispatchAgent):
    def __init__(self, *args, **kwargs):
        DispatchAgent.__init__(self, *args, **kwargs)
        self.psk = 'you must be kidding'
        self.endpoints = []
        self.hostname = socket.gethostname().split('.')[0]

    @agentmethod()
    def confirmConfiguration(self):
        if self.psk is None:
            log.error("PSK is not set")
            return False
        if not isinstance(self.endpoints, list):
            log.error("endpoints is not a list")
            return False
        if len(self.endpoints) == 0:
            log.error("no endpoints defined")
            return False

        # write configuration file
        log.info('writing configuration file')

        # determine my own name
        for obj in self.endpoints:
            if obj['host'] == self.hostname:
                here = obj
                break
        else:
            log.error('could not myself ({}) in endpoints'.format(self.hostname))
            return False

        with open('/tmp/ipsec.conf', 'w') as tempfp:
            for obj in self.endpoints:
                if obj is not here:
                    # use of auto=route is important.  auto=start does not seem
                    # to work--the tunnels are added but they are not brought
                    # up.  seems to be some sort of race condition when all
                    # endpoints are brought up simultaneously.
                    cfg = """conn {}
    left={}
    leftsubnet={}
    right={}
    rightsubnet={}
    auto=route
    authby=secret""".format(
                        here['host'] + '_' + obj['host'],
                        here['ip'],
                        here['subnet'],
                        obj['ip'],
                        obj['subnet']
                    )
                    print >>tempfp
                    print >>tempfp, cfg
        os.rename('/tmp/ipsec.conf', '/etc/ipsec.conf')

        with open('/tmp/ipsec.secrets', 'w') as tempfp:
            for obj in self.endpoints:
                if obj is not here:
                    print >>tempfp, '{} {} : PSK "{}"'.format(here['ip'], obj['ip'], self.psk)
        os.rename('/tmp/ipsec.secrets', '/etc/ipsec.secrets')

        return True

    @agentmethod()
    def startIpsec(self, msg):
        log.info('starting ipsec')
        # use 'restart' to catch the case where the daemon is already running
        run('ipsec restart', log=log)

    @agentmethod()
    def stopIpsec(self, msg):
        log.info('stopping ipsec')
        run('ipsec stop', log=log)
