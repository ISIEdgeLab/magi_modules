#!/usr/bin/env python

from magi.util.agent import DispatchAgent
from magi.util.processAgent import initializeProcessAgent
from magi.util.distributions import *
from magi.util import database
from magi.testbed import testbed

from subprocess import check_call, CalledProcessError, Popen, PIPE

import logging

log = logging.getLogger(__name__)

class IronAgent(DispatchAgent):
    """
        Control IRON components running on an experiment node.
	"""
    def __init__(self):
        super(IronAgent, self).__init__()

        # dirty background vars. - no idea what this is. zero'd for now.
        self.dirty_background_ratio = 0.0
        self.dirty_ratio = 0.0
        self.dirty_expire_centisecs = 0.0

        self.exp_dir = None         # must be set and must be the IRON experiment name in the dist tarfile.
        self.node_map = None        # must be set and must be a dict from node role to (short) node name.
        self.iface_in_map = None    # must be set and must be short node name to ip address of "in" interface

        # do not touch below here.
        self._procs = {}
        self._install_dir = '/iron/install'   # MAGI install script puts it here. 
        self._bin_dir = '{}/bin'.format(self._install_dir)
        self._log_dir = '{}/logs'.format(self._install_dir)
        self._cfgs = {}

    def confirmConfiguration(self):
        log.info('Checking given configuration...')
        if not self.init_agent():
            log.error('Error initializing the agent.')
            return False

        if not self.exp_dir:
            log.critical('You need to set the exp_dir variable in the AAL file.')
            return False

        log.info('Config: \n\tnode_map: {}\n\tiface_in_map: {}\n\texp_dir: {}'.format(
            self.node_map, self.iface_in_map, self.exp_dir))

        cfg_dir = '{}/{}/cfgs'.format(self._install_dir, self.exp_dir)
        nodename = testbed.nodename
        log.info('Looking for config files for node {}'.format(nodename))
        # we could read the dir directly and discover cfg files meant for us...
        for name in ['bpf', 'udp_proxy']:
            if nodename in self.node_map:
                log.warn('Adding {} cfg to node {} ({})'.format(name, nodename, self.node_map[nodename]))
                self._cfgs[name] = '{}/{}_{}.cfg'.format(cfg_dir, name, self.node_map[nodename])
            else:
                log.warn('Config for {} node {} not found.'.format(name, nodename))

        return True

    def init_agent(self):
        # taken from run_iron.sh script. Dunno why this is needed.
        if self.dirty_background_ratio:
            cmds = [
                'sudo sysctl -w vm.dirty_background_ratio={}'.format(self.dirty_background_ratio),
                'sudo sysctl -w vm.dirty_ratio={}'.format(self.dirty_ratio),
                'sudo sysctl -w vm.dirty_expire_centisecs={}'.format(dirty_expire_centisecs),
            ]
            for cmd in cmds:
                try:
                    check_call(cmd.split())
                except CalledProcessError as e:
                    log.critical('Error calling "{}": {}'.format(cmd, e))
                    return False

        return True

    def start_iron(self, msg):
        '''Start IRON components on the experiment node.'''
        # this code could be cleaner.
        if 'bfp' in self._procs and self._procs['bpf']:
            log.warn('bpf already started, not restarting')
        else:
            log.info('Starting bpf')
            cmd = '{}/bpf -l {}/bpf.log'.format(self._bin_dir, self._log_dir)
            if 'bpf' not in self._cfgs:
                log.info('Running bpf without config file.')
            else:
                log.info('Running bpf with config file {}'.format(self._cfgs['bpf']))
                cmd += ' -c {}'.format(self._cfgs['bpf'])

            log.info('Running cmd: {}'.format(cmd))
            self._procs['bpf'] = Popen(cmd.split(), close_fds=True)

        if 'udp_proxy' in self._procs and self._procs['udp_proxy']:
            log.warn('udp_proxy already running. Not restarting')
        else:
            log.info('Starting udp_proxy')
            cmd = '{}/udp_proxy -c {} -l {}/udp_proxy.log'.format(self._bin_dir, self._cfgs['udp_proxy'],
                                                                  self._log_dir)
            if testbed.nodename in self.iface_in_map:
                iface = self._ip2if(self.iface_in_map[testbed.nodename])
                if not iface:
                    log.critical('Unable to find interface that maps to {}'.format(
                        self.iface_in_map[testbed.nodename]))
                    return False

                cmd += ' -I {}'.format(iface)

            log.info('Running cmd: {}'.format(cmd))
            self._procs['udp_proxy'] = Popen(cmd.split(), close_fds=True)
        
        return True

    def stop_iron(self, msg):
        # Do we want to kill nicely and check exit values? Does iron support understandable exit values?
        for name, proc in self._procs.iteritems():
            log.info('Stopping {}'.format(name))
            proc.kill()

        self._procs = {}  # goodbye procs!

        # Just to be safe. 
        for proc in ['bpf', 'udp_proxy']:
            try:
                check_call('pkill -f {}'.format(proc).split())
            except CalledProcessError:
                pass

        return True

    def _ip2if(self, ip):
        '''Given an ipv4 address as a string, return the iface that is bound to that address if it exists.'''
        # There is probably a better way to do this. I'm just doing this:
        #      ifconfig | grep -B1 ipaddr | cut -d' ' -f1
        p = Popen('ifconfig', stdout=PIPE)
        lines = [l for l in p.stdout]
        p.wait()  # be nice
        for i, line in enumerate(lines):
            if ip in line:
                return lines[i-1].split()[0]   # will break on Fedora, which does "eth0:"
                                               # if there is a proc file, I'd use that instead.

        return None
    
def getAgent(**kwargs):
    agent = IronAgent()
    if not agent.setConfiguration(None, **kwargs):
        msg = 'Bad configuration given to agent'
        log.critical(msg)
        raise(Exception(msg))  # Don't know how else to get Magi's attention here.

    return agent

if __name__ == "__main__":
    agent = CurlAgent()
    kwargs = initializeProcessAgent(agent, sys.argv)
    agent.setConfiguration(None, **kwargs)
    agent.run()

            
