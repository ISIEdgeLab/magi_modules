#!/usr/bin/env python

import logging
import socket
import os.path
import os
import re, time

log = logging.getLogger(__name__)

class ClickConfigParserException(Exception):
    pass

def click_config_except(msg):
    log.critical(msg)
    raise ClickConfigParserException(msg)

class ClickConfigParser(object):
    '''
        Create an empty click configu parser.
    '''
    def __init__(self):
        super(ClickConfigParser, self).__init__()
        self._confpath = None
        self._config = {}
        self._socket = None
        self._socket_set = False
        self._parse_time = -1.0

    def get_value(self, node, key):
        try:
            return self._config[node][key]
        except ClickConfigParser:
            pass

        return None

    def set_value(self, node, key, value):
        '''Set a spefic value. i.e. write this value to the active click configuration.'''
        rval = self._write(node, key, value)
        if not rval:
            log.info('Error setting key {} to value {}'.format(value, key))

        return rval

    def get_configuration(self):
        '''
            Return the click configuration as dict[node][key] = ['value1', 'value2', ...] data structure. 
            You must call parse() to do the actual parsing before calling this.
        '''
        return self._config

    def parse(self, confpath='/click', force=False):
        '''
        Parse the active click configuration pointed to by the confpath argument.
        '''
        
        self._confpath = confpath
        if not os.path.exists(self._confpath):
            click_config_except('Click config path ({}) not found.'.format(self._confpath))

        if os.path.getmtime(self._confpath) < self._parse_time and not force:
            return

        self._parse_time = time.time()
            
        if not os.path.isdir(self._confpath):
            self._read = self._read_socket
            self._write = self._write_socket
        else:
            self._read = self._read_file
            self._write = self._write_file

        self._config = {}
        nodes = self._read('list')   # this is in the protocol.

        for node in nodes:
            lines = self._read('{}.{}'.format(node, 'handlers'))  # 'handlers' is also in the protocol.
            for line in lines:
                try:
                    handler, perm = line.split()
                except ValueError as e:
                    click_config_except('Error reading {}/{}: {} (from {})'.format(node, handler, line, lines))

                if handler == 'handlers':   # handlers lists itself. skip it.
                    continue

                if perm.startswith('r'):   # only read permissions have readable values...
                    value = self._read('{}.{}'.format(node, handler))
                    if value:
                        if node not in self._config:
                            self._config[node] = {}
                     
                        self._config[node][handler] = {'lines': value, 'permission': perm}
        log.info("Parsed Config")
        
    def _read_socket(self, path):
        '''Send msg to the connected click control socket and return the parsed response.'''
        # for protocol details see: http://read.cs.ucla.edu/click/elements/controlsocket
        # Basic protocol response is like:
        # XXX: <msg>
        # DATA NNN
        # ...
        # Where XXX is 200 success; not 200 error and NNN is len of DATA in bytes.
        msg = path.replace(os.sep, '.')
        s = self._open_control_socket(self._confpath)
        s.sendall('READ ' + msg + '\r\n')   # CRLF is expected.
        success, resp = self._read_socket_response(s)
        if not success:
            return False, 'Error reading click socket: {}'.format(resp)

        lines = []
        datasize = int(resp)
        if datasize > 0:
            log.debug('reading {} bytes'.format(datasize))
            buf = s.recv(datasize)
            # Not sure why, but "list" puts the number of items first. So remove that
            # if this is a 'list' command.
            lines = [t.strip() for t in buf.split('\n') if t]  # remove empty lines and split on \n
            if msg.lower() == 'list':
                lines = lines[1:]

        self._close_control_socket()
        return lines

    def _read_write_status(self, s):
        def _readline(s):
            buf = ''
            while True:
                c = s.recv(1)
                if c == '\r':
                    c = s.recv(1)
                    if c == '\n':
                        break
                
                buf += c
            return buf

        resp_line = _readline(s)
        line = re.split(' |-', resp_line)
        if line[0] != '200':
            try:
                err_msg = _readline(s)
            except socket.timeout:
                log.debug('socket read timeout')
                err_msg = None

            return False, err_msg

        return True, line  
    
    def _read_socket_response(self, s):
        '''Read the click response. Return response and amounf of data to read.'''
        def _readline(s):
            buf = ''
            while True:
                c = s.recv(1)
                if c == '\r':
                    c = s.recv(1)
                    if c == '\n':
                        break
                
                buf += c
            return buf

        resp_line = _readline(s)
        line = re.split(' |-', resp_line)
        if line[0] != '200':
            try:
                err_msg = _readline(s)
            except socket.timeout:
                log.debug('socket read timeout')
                err_msg = None

            return False, err_msg

        _, bytecnt = _readline(s).split()
        return True, bytecnt

    def _read_file(self, subpath):
        path = os.path.join(self._confpath, subpath.replace('.', os.sep))
        lines = []
        try:
            log.debug('Reading file {}'.format(path))
            with open(path, 'r') as fd:
                lines = [l.strip() for l in fd.readlines()]
                # Not sure why, but "list" puts the number of items first. So remove that
                # if this is a 'list' command.
                if subpath.lower() == 'list':
                    lines = lines[1:]
        except IOError as e:
            # not an error. some nodes don't have all keys.
            log.debug('path does not exist: {}'.format(path))

        return lines

    def _close_control_socket(self):
        if self._socket_set:
            self._socket.close()

        self._socket_set = False

    
    def _open_control_socket(self, path):
        if self._socket_set:
            return self._socket
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(path)
        except Exception as e:
            click_config_except('Unable to open click control UNIX socket {}: {}'.format(self._confpath, e))

        s.settimeout(1)  # should be very quick as it's local.

        # read proto header and version.
        buf = s.recv(64)
        # should be like "Click::ControlSocket/1.3"
        if not buf.startswith('Click::ControlSocket'):
            click_except('Bad protocol on click control socket, exiting.')

        try:
            _, v = buf.strip().split('/')
            if float(v) < 1.3:
                click_except('Click control protocol too old at {}'.format(v))
        except (ValueError, TypeError):
            click_except('Error in click control protocol.')

        self._socket = s
        self._socket_set = True
        return s

    def _write_socket(self, node, key, value):
        s = self._open_control_socket(self._confpath)
        try: 
            log.debug('writing value "{}" to socket at {}'.format(value, self._confpath))
            # GTL: TODO - check return value of socket send.
            values = value if isinstance(value, list) else [value]
            for v in values:
                cmd = 'write {}.{} {}\r\n'.format(node, key, v)
                log.info('writing cmd to socket: {}'.format(cmd))
                s.send(cmd)
        except IOError as e:
            log.warn('Unable to write to socket: {} --> {}'.format(key, value))
            return False

        s.settimeout(0)
        try:
            success, resp = self._read_write_status(s)
            if not success:
                log.info('click responded with error code to write: {}'.format(resp))
                return False   # error in response. 

            log.info('write response: {}'.format(resp))
        except IOError as e:
            log.info('Unable to read response to write: {} --> {}'.format(key, value))
            # This is OK.
        s.settimeout(1)
            
        return True

    def _write_file(self, node, key, value):
        path = os.path.join(self._confpath, node, key)
        try: 
            log.debug('writing value "{}" to file {}'.format(value, path))
            with open(path, 'w') as fd:
                if isinstance(value, list):
                    for v in value:
                        fkeyd.write(v)
                else:
                    fd.write(value)
        except IOError as e:
            log.warn('Unable to write to file. ({} <-- {})'.format(path, value))
            return False

        return True


if __name__ == '__main__':
    import random
    from sys import argv

    if '-d' in argv:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    try:
        # config is a dict of dicts where the value is array of string.
        ccp = ClickConfigParser()
        ccp.parse()
        config = ccp.get_configuration()

        nodes = config.keys()  # top level are click graph nodes.

        # use specialized knowledge about naming to find the routers.
        # this is good or bad depending on your data abstraction.
        routers = [k for k in config.keys() if k.startswith('router')]

        print('=' * 80)

        node = routers[0]   # some random router
        for node, handlers in config.iteritems():
            for handler, lines in config[node].iteritems():
                print('{}/{}: {}'.format(node, handler, config[node][handler]['lines']))

        print('=' * 80)

        # change first bw link
        link = [l for l in nodes if l.endswith('_bw')][0]
        key = 'bandwidth'
        change = 25000000
        old_bw = config[link][key]['lines'][0]
        new_bw = str(int(old_bw) + random.choice([-change, change]))

        print('changing {} on link {}'.format(key, link))
        print('before  A --> B {}: {}'.format(key, old_bw))
        ccp.set_value(link, key, new_bw)

        print('setting A --> B {} to {}'.format(key, new_bw))

        ccp.parse()  # reparse everything to get teh new value.
        config = ccp.get_configuration()
        print('after   A --> B {}: {}'.format(key, config[link][key]['lines'][0]))

        if config[link][key]['lines'][0] != str(new_bw):
            print('FAIL write test: {} != {}'.format(config[link][key]['lines'][0], new_bw))
        else:
            print('PASS write test')
            # change it back to be polite.
            print('Setting it back to {}'.format(old_bw))
            ccp.set_value(link, key, old_bw)

        val = ccp.set_value('doesnotexist', 'nope', 666)   # should fail and return False
        if val:
            print('set_value() returned true for non-existent node - this is an error.')
        else:
            print('set_value() returned False for non-existent lnk - this is correct.')

    except ClickConfigParserException as e:
        print('Caught click config exception: {}'.format(e))
        exit(1)

    exit(0)
