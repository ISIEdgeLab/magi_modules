import logging
import re
from subprocess import Popen, PIPE, check_call, CalledProcessError, STDOUT
from os import devnull

log = logging.getLogger(__name__)

class OneHopNeighbors(object):
    def __init__(self):
        super(OneHopNeighbors, self).__init__()
        self.hosts = self._get_hosts('/etc/hosts')

    def _get_hosts(self, path):
        ''' read local /etc/hosts file and return an {ip: hostname, ...} dict of the info found there.'''
        ret = {}
        with open(path, 'r') as fd:
            for line in fd:
                line = line.strip('\n')
                # 10.0.1.3    xander-landos xander-0 xander
                m = re.match('([\d\.]+)\s+([^ ]+)', line)
                if m:
                    ret[m.group(1)] = m.group(2)

        return ret

    def get_neighbors(self):
        local_addrs = self.get_local_addresses()
        if not local_addrs:
            log.warn('Unable to get local addresses - cannot continue.')
            return None
        
        DEVNULL = open(devnull, 'w')
        one_hop_nbrs = {}
        for addr, host in self.hosts.iteritems():
            log.debug('comp: {} <--> {}'.format(addr, local_addrs.keys()))
            if addr not in local_addrs.values():
                # traceroute -r sends directly to a host on an attached network
                # and errors if network is not there. -m 1 limits the max TTL to 1
                cmd = 'traceroute -m 1 -r {}'.format(addr)
                try: 
                    check_call(cmd.split(' '), stdout=DEVNULL, stderr=STDOUT)
                except (OSError, ValueError) as e:
                    log.warn('Unable to run "{}": {}'.format(cmd, e))
                    continue
                except CalledProcessError as e:
                    log.info('{} ({}) does not appear to be one hop neighbor.'.format(host, addr))
                    continue

                # so at this point, it looks like this host entry is a one hop 
                # neighbor. Add it to the list.
                one_hop_nbrs[host] = addr

        return one_hop_nbrs

    def get_local_addresses(self):
        try:
            o, _ = Popen(['ifconfig'], stdout=PIPE).communicate()
        except (OSError, ValueError) as e:
            log.critical('Unable to read interface information via ifconfig: {}'.format(e))
            return False
        
        local_addrs = {'localhost': '127.0.0.1'}
        for line in o.split('\n'):
            # inet addr:10.0.6.2  Bcast:10.0.6.255  Mask:255.255.255.0
            m = re.search('addr:([\d\.]+)\s+Bcast:([\d\.]+)\s+Mask:([\d\.]+)', line)
            if m:
                if m.group(1) in self.hosts.keys():
                    local_addrs[self.hosts[m.group(1)]] = m.group(1) 
                else:
                    log.warn('Found unnamed address in local interfaces (probably control '
                             'net): {}'.format(m.group(1)))

        log.debug('local addresses: {}'.format(local_addrs))
        return local_addrs

if __name__ == "__main__":
    from sys import argv
    if '-d' in argv or '-v' in argv:
        logging.basicConfig(level=logging.DEBUG)

    nbrs = OneHopNeighbors()
    print('Hosts: {}'.format(nbrs.hosts))
    print('Local Addrs: {}'.format(nbrs.get_local_addresses()))
    print('One hop neighbors: {}'.format(nbrs.get_neighbors()))
