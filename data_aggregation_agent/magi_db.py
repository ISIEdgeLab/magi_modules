import logging
import yaml
from pymongo import MongoClient

log = logging.getLogger(__name__)


class MagiDatabaseException(Exception):
    pass

class MagiDatabase(object):
    def __init__(self):
        name, port = self._getdbserver()
        self._client = MongoClient(name, port)

    def db(self):
        return self._client['magi']

    def _getdbserver(self):
        name, port = None, None
        try:
            with open('/var/log/magi/config/experiment.conf', 'r') as fd:
                expconf = yaml.safe_load(fd)
        except Exception as e:
            raise MagiDatabaseException(e)

        if 'dbdl' not in expconf:
            raise MagiDatabaseException('did not file database config in experiment.conf')

        if 'configHost' in expconf['dbdl']:
            name = expconf['dbdl']['configHost']
        elif 'sensorToCollectorMap' in expconf['dbdl']:
            if '__DEFAULT__' not in expconf['dbdl']['sensorToCollectorMap']:
                raise MagiDatabaseException('no default collector in experiment.conf')
            else:
                name = expconf['dbdl']['sensorToCollectorMap']['__DEFAULT__']
        else:
            raise MagiDatabaseException('Unable to find database server in experiment.conf')

        if not 'collectorPort' in expconf['dbdl']:
            raise MagiDatabaseException('no collectorPort listed in config.')

        port = expconf['dbdl']['collectorPort']

        return name, port
