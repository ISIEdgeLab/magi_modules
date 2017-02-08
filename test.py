#!/usr/bin/env python

import unittest2
import os
import logging
import subprocess
import time
import sys
import yaml
import Queue

sys.path.append(os.path.join(__file__, '../../python'))

from magi.messaging.api import MAGIMessage
from magi.tests.util import AgentUnitTest 
from magi.testbed import testbed

import pktCountersAgent

class CounterTest(AgentUnitTest):
	""" Testing of counters agent """

	AGENT = pktCountersAgent
	test_IDL = AgentUnitTest.idltest(os.path.join(os.path.dirname(__file__), "pktCountersAgent.idl"))

	@classmethod
	def requirements(cls):
		if os.geteuid() != 0: 
			raise unittest2.SkipTest("Testing counters require root privleges, skipping.")

	def test_Record(self):
		""" Test basic recording of defaults """
		ip = testbed.getInterfaceList()[0].ip
		req = {
			'version': 1.0,
			'method':'setConfiguration',
			'args': {
				'interval': 2,
				'filters': {
					'cnt1': { 'input': ip },
					'cnt2': { 'output': ip },
				}
			}
		}

		start = {
			'version': 1.0,
			'method': 'startCollection',
		}

		stop = {
			'version': 1.0,
			'method': 'stopCollection',
		}
		
		self.fixture.inject(MAGIMessage(src='guinode', data=yaml.safe_dump(req)))
		self.fixture.inject(MAGIMessage(src='guinode', data=yaml.safe_dump(start)))
		time.sleep(5)
		self.fixture.inject(MAGIMessage(src='guinode', data=yaml.safe_dump(stop)))
		time.sleep(0.1) # let it process it first

		# TODO, open sqlite database and read in counters
			

if __name__ == '__main__':
	AgentUnitTest.agentMain(0)

