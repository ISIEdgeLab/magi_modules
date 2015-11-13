import logging
import shutil
import os
import stat
import platform

from magi.util.execl import run, execAndRead
from magi.util.agent import SharedServer, agentmethod
from magi.util.processAgent import initializeProcessAgent

log = logging.getLogger(__name__)

class ApacheAgentException(Exception):
    pass

def getAgent(**kwargs):
    a = ApacheAgent()
    if kwargs: 
        a.setConfiguration(None, **kwargs)
    
    return a


class ApacheAgent(SharedServer):
    def __init__(self):
        SharedServer.__init__(self)

        # supported platforms are constrained.
        # may work on others, but would not want
        # to rely on that.
        dist = platform.dist()
        if dist[0] != 'Ubuntu':
            raise ApacheAgentException('Unsupported OS: {}'.format(dist))

        maj = int(dist[1].split('.')[0])
        if not 12 <= maj <= 14:
            raise ApacheAgentException('Unsupported Ubuntu Version: {}'.format(maj))

        self.configure()
        self.terminateserver()

    def runserver(self):
        cmd = 'sudo service apache2 restart'
        log.info('Running cmd: {}'.format(cmd))
        o, e = execAndRead(cmd, close_fds=True)
        log.info('cmd output: {}'.format(o))
        log.info('cmd error: {}'.format(e))
        log.info('Apache started.')
        return True

    def terminateserver(self):
        run("sudo service apache2 stop", close_fds=True)
        log.info('Apache stopped.')
        return True

    def configure(self):
        # create and enable our site.
        cwd = os.path.dirname(__file__)
        shutil.copyfile(os.path.join(cwd, 'traffic_gen'),
                        '/etc/apache2/sites-available/traffic_gen')

        # Have apache use our site and remove the default one.
        run('a2ensite traffic_gen')
        run('a2dissite default')

        # create the WSGI site itself.
        sitedir = '/var/www/traffic_gen'
        try:
            os.stat(sitedir)
        except OSError:
            os.mkdir(sitedir)

        for f in ['traffic_gen.py', 'traffic_gen.wsgi']:
            shutil.copyfile(os.path.join(cwd, f), os.path.join(sitedir, f))
        
        # the script must be executable by apache
        os.chmod(os.path.join(sitedir, 'traffic_gen.py'), 0755)

if __name__ == '__main__':
    from sys import argv
    agent = ApacheAgent()
    kwargs = initializeProcessAgent(agent, argv)
    agent.run()
