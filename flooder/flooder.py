#!/usr/bin/env python 

# Copyright (C) 2012 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

import logging
import os
import signal
import sys

from magi.util.agent import DispatchAgent, agentmethod
from magi.util.processAgent import initializeProcessAgent
from magi.util.cidr import CIDR
from magi.testbed import testbed
import magi.util.execl as execl

class flooder_agent(DispatchAgent):
	"""
	Provides an interface to the flooder utility. 
	"""
	def __init__(self):
		"""Init base class; install required software; setup initial configuration."""
		DispatchAgent.__init__(self)
		self.pids = []

		# 'Attack Source'
		self.nodes = []		# nodelist 
		self.src = None		# CIDR

		# 'Basics'
		self.dst = None			# CIDR
		self.proto = None		# Valid values are these strings: ['tcp', 'udp']
		self.length = None		# minmax() dist or int (fixed minmax)

		# 'Rate Info'
		self.ratetype = None	# One of string: one of: ['flat', 'rampup', 'pulse'] 
		self.highrate = None	# int
		self.lowrate = None		# int
		self.hightime = None	# int
		self.lowtime = None		# int
		self.risetime = 0		# int 
		self.falltime = 0		# int 

		# 'UDP/TCP' config
		self.sport = 'minmax(0,65535)'	# minmax() dist or int (fixed minmax)
		self.dport = 'minmax(0,65535)'  # minmax() dist or int (fixed minmax)
		self.tcpflags = ['SYN']    # one or more of string set: 'SYN', 'FIN', 'RST', 'ACK', 'PSH', 'URG'		

		self.log = logging.getLogger(__name__)

	@agentmethod()
	def startFlood(self, msg):
		self.log.debug('startFlood() called with msg: %s' % msg)

		cmd=['flooder']
		# awkward. Gotta be a better way than this...
		for var in ['proto', 'ratetype', 'highrate', 'hightime', 'lowrate', 'lowtime', 'risetime', 'falltime']:
			val = getattr(self, var, None)
			if val is not None and val != '':
				cmd.extend(["--%s" % var, str(val)])

		# handle dist vars separately so as to parse the values out.
		for var in ['length', 'sport','dport']:
			val = getattr(self, var, None)
			if val is not None and val != '':
				if 'minmax' in val:
					# ugly could use regex here, but....
					(mn,mx)=val.split(',')
					mn=mn.split('(')[1]
					mx=mx.split(')')[0]
					cmd.extend(['--%smin' % var, str(mn), '--%smax' % var, str(mx)]) 
				else:
					cmd.extend(['--%smin' % var, str(val), '--%smax' % var, str(val)]) 
				
		if self.src is not None:
			cmd.extend(["--src", self.src.basestr, "--srcmask", self.src.maskstr])

		if self.dst is not None:
			cmd.extend(["--dst", self.dst.basestr, "--dstmask", self.dst.maskstr])

		if type(self.tcpflags) is list:
			flagbyte = 0
			for fstring in self.tcpflags:
				flagbyte |=  { 'fin': 1, 'syn': 2, 'rst': 4,
					'psh': 8, 'ack': 16, 'urg': 32 } [ fstring.lower() ]
			cmd.extend(["--tcpflags", str(flagbyte)])
		
		cmdstring=' '.join(cmd)
		self.log.debug('Running cmd: %s' % cmdstring)
		self.pids.append(execl.spawn(cmdstring, self.log, close_fds=True))

		return True

	@agentmethod()
	def stopFlood(self, msg):
		self.log.debug('stopFlood() called with msg: %s' % msg)
		for pid in self.pids:
			self.log.debug('stopFlood() killing %s' % pid)
			os.kill(pid, signal.SIGTERM)

		self.pids = []
		return True
	
	@agentmethod()
	def stop(self, msg):
		self.stopFlood(msg)
		DispatchAgent.stop(self, msg)

	@agentmethod()
	def confirmConfiguration(self):
		"""Reimplemented to specialize variables."""
		# ...now we update src and dst to CIDR types. 
		if self.src != None:
			if not isinstance(self.src, CIDR): # can already be set if setConf() is called multiple times.
				try:
					if self.src.find("/") == -1:
						self.src = testbed.getHostForName(self.src)
					self.src = CIDR(inputstr=self.src)
				except:
					self.log.warn('src is not a valid string, I do not know what to do with it.')
					return False
		else:
			# Assume user wants the local node to do the flood.
			self.src = CIDR(inputstr=testbed.getLocalIPList()[0])

		if self.dst != None:
			if not isinstance(self.dst, CIDR): # can already be set if setConf() is called multiple times.
				try:
					if self.dst.find("/") == -1:
						self.dst = testbed.getHostForName(self.dst)
					self.dst = CIDR(inputstr=self.dst)
				except:
					self.log.warn('dst is not a valid string, I do not know what to do with it.')
					return False
				
		return True
	
def getAgent(**kwargs):
	agent = flooder_agent()
	agent.setConfiguration(None, **kwargs)
	return agent

if __name__ == "__main__":
	agent = flooder_agent()
	kwargs = initializeProcessAgent(agent, sys.argv)
	agent.setConfiguration(None, **kwargs)
	agent.run()

