#!/usr/bin/env python

import pycurl
import time

class MyAgent(object):
    def __init__(self, host, sizes):
        self.host = host
        self.sizes = sizes
        self.prev_time = 0
        self.prev_bytes = 0
        self.intervals = []     # keep track of all intervals
        self.interval = 0       # keep sum of current interval
        self.period = 1.0       # accounting period in seconds.
        self.headers = {}
        self.c = pycurl.Curl()

    def header_callback(line):
        line = line.decode('iso-8859-1')
        if ':' not in line:
            return
        name, value = line.split(':', 1)
        name = name.strip()
        value = value.strip()
        name = name.lower()
        print('got header - {}: {}'.format(name, value))
        self.headers[name] = value

    # so pycurl takes class methods as callbacks, which is good.
    def progress_callback(self, to_dl, total_dl, to_ul, total_ul):
        now = time.time()
        self.interval += now-self.prev_time
        self.intervals.append(self.interval)
        if self.interval >= self.period:
            print('Elapsed time: {}'.format(self.interval))
            print('{} of {} downloaded.'.format(total_dl, to_dl))
            print('{} of {} uploaded.'.format(total_ul, to_ul))
            print('{} bytes downloaded this period.'.format(total_dl-self.prev_bytes))
            print('{}'.format('-' * 80))
            self.interval = 0.0
            self.prev_bytes = total_dl

        self.prev_time = now

    def do_it(self):
        self.size = eval(self.sizes)
        self.c.setopt(self.c.URL, "http://{}/gettext/{}".format(self.host, self.size))
        self.c.setopt(self.c.NOPROGRESS, 0)
        self.c.setopt(self.c.PROGRESSFUNCTION, self.progress_callback)
        self.c.setopt(self.c.WRITEFUNCTION, lambda x: None) # return bytes "written"
        self.prev_time = time.time() # seed the time
        self.c.perform()
        return self.c.getinfo(self.c.RESPONSE_CODE)

if __name__ == "__main__":
    from sys import argv, exit
    from os.path import basename
    host = argv[1] if len(argv) > 1 else None
    sizes = argv[2] if len(argv) > 2 else None

    if not host:
        print('Usage: {} host sizes_expression'.format(basename(argv[0])))
        exit(1)

    ma = MyAgent(host, sizes)
    ret = ma.do_it()
    if 200 != ret:
        print('Error in transfer: got HTTP code {}'.format(ret))
    else:
        print('Bytes retrieved: {}'.format(ma.size))
        # print('Content-Length : {}'.format(ma.headers['content-length']))
        print('Called {} times'.format(len(ma.intervals)))
        print('Avg time between calls: {}'.format(sum(ma.intervals)/len(ma.intervals)))
        print('Bytes between callbacks: {}'.format(ma.size/len(ma.intervals)))
        print('Total Time: %f' % ma.c.getinfo(ma.c.TOTAL_TIME))


