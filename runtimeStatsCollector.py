import logging
import subprocess
from platform import platform

log = logging.getLogger(__name__)

class PlatformNotSupportedException(Exception):
    pass


def getRuntimeStatsCollector():
    '''Return an instance of a CPU reporter based on the platform this is executing on.'''
    if platform().lower().startswith('linux'):
        return LinuxRuntimeStatsCollector()

    raise PlatformNotSupportedException(platform())


class RuntimeStatsCollector(object):
    '''base class API for reporting on CPU usage.'''

    def __init__(self):
        pass

    def getCpuUsage(self):
        pass

    def getLoadAverage(self):
        pass


class LinuxRuntimeStatsCollector(RuntimeStatsCollector):
    def __init__(self):
        self.stats_prev = dict()
        self._readstats(self.stats_prev)
        
        self.stats_prev_process = dict()


    def getCpuUsage(self):
        '''returns % of cpu time spent working (vs. idle) since getCpuUsage was last called.'''
        stats_now = dict()
        self._readstats(stats_now)
        total_jiffies = stats_now['total'] - self.stats_prev['total']
        work_jiffies = stats_now['work'] - self.stats_prev['work']
        self.stats_prev = stats_now
        return ((float(work_jiffies) / float(total_jiffies)) * 100.0, work_jiffies)


    def getLoadAverage(self):
        '''return a 4-tuple of number of processes for 1 minute, 5 minutes, 15 minutes, and total 
        number currently running.'''
        # /proc/loadav is 5 numbers: load average @ 1 minute, 5 minutes, 15 minutes, runnable tasks, total procs.
        load_avg = subprocess.Popen(['cat','/proc/loadavg'], stdout=subprocess.PIPE).communicate()[0].split()
        return float(load_avg[0]), float(load_avg[1]), float(load_avg[2]), int(load_avg[4])


    def _readstats(self, stats):
        # sample 
        # 'cpu  5849 0 2211 1704105 1916 0 32 0 0 0'
        cpu_stats = subprocess.Popen(['cat', '/proc/stat'], stdout=subprocess.PIPE).communicate()[0].split('\n')[0].split()
        # we store more than we currently report on
        cpu_stats = [int(x) for x in cpu_stats[1:]]
        stats['user'] = cpu_stats[0]
        stats['nice'] = cpu_stats[1]
        stats['system'] = cpu_stats[2]
        stats['idle'] = cpu_stats[3]
        stats['iowait'] = cpu_stats[4]
        stats['total'] = sum(cpu_stats[0:5])
        stats['work'] = sum(cpu_stats[0:3])
    
    
    def getCpuUsage_process(self, processId, threadId=None):
        '''returns % of cpu time spent working (vs. idle) since getCpuUsage_process was last called.'''
        processId = str(processId)
        if threadId == None:
            pid = processId
        else:
            threadId = str(threadId)
            pid = processId + '_' + threadId
            
        stats_now = dict()
        self._readstats(stats_now)
        self._readstats_process(stats_now, processId, threadId)
        
        if pid in self.stats_prev_process:
            total_jiffies = stats_now['total'] - self.stats_prev_process[pid]['total']
            utime_jiffies = stats_now['utime'] - self.stats_prev_process[pid]['utime']
            stime_jiffies = stats_now['stime'] - self.stats_prev_process[pid]['stime']
            work_jiffies = utime_jiffies + stime_jiffies
        else:
            total_jiffies = stats_now['total']
            work_jiffies = stats_now['utime'] + stats_now['stime']
        
        self.stats_prev_process[pid] = stats_now
        return ((float(work_jiffies) / float(total_jiffies)) * 100.0, work_jiffies)
    
    
    def _readstats_process(self, stats, processId, threadId=None):
        if threadId == None:
            procStatFile = '/proc/' + str(processId) + '/stat'
        else:
            procStatFile = '/proc/' + str(processId) + '/task/' + str(threadId) + '/stat'
            
        # sample 
        # '11624 (python) S 11575 11624 11575 34818 11624 4202496 1353 412 0 0 2 2 0 0 20 0 1 0 4945037 8716288 1067 4294967295 134512640 136478912 3221056672 3221055276 14636066 0 0 16781312 134217730 3223429366 0 0 17 0 0 0 0 0 0'
        cpu_stats = subprocess.Popen(['cat', procStatFile], stdout=subprocess.PIPE).communicate()[0].split('\n')[0].split()
        cpu_stats = [int(x) for x in cpu_stats[13:15]]
        stats['utime'] = cpu_stats[0]
        stats['stime'] = cpu_stats[1]
        
    