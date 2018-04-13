#!/usr/bin/env python

# Copyright (C) 2015 University of Southern California
# This software is licensed under the GPLv3 license, included in
# ./GPLv3-LICENSE.txt in the source distribution

import logging
import re
import os
import math
import time
import threading

from subprocess import Popen

import clickGraph
# pylint: disable=import-error
from magi.util.agent import agentmethod, DispatchAgent
# pylint: disable=import-error
from magi.util import execl
from click_config_parser import ClickConfigParser, ClickConfigParserException

class ClickControlError(Exception):
    pass

def getAgent(**kwargs):
    agent = ClickControlAgent()
    agent.setConfiguration(None, **kwargs)
    return agent

def validateClickInputs(orig_func):
    def wrapper(self, *args, **kwargs):
        ''' click excepts everything, this function should be
        gatekeeper telling user wether the key they are going
        to use, or maybe even value is going to be valid and
        cause a change in click '''
        # in order to get the configuration, we will need to make
        # sure we have parsed conf file (not guarenteed at time of
        # calling this function).  Also this will all break if the
        # confinguration file is not in '/click', but that is not
        # something that is allowed to be changed in clickControl
        logging.debug('args to click: %s', args)
        logging.debug('kwargs to click: %s', kwargs)
        self.ccp.parse()
        config = self.ccp.get_configuration()
        # verify node is valid
        valid_node = config.get(kwargs['node'], False)
        if not valid_node:
            raise ClickControlError(
                'NODE: {user_node} for click object not found. ' \
                'valid key targets are: {config_node_keys}\n'.format(
                    user_node=kwargs['node'],
                    config_node_keys=config.keys(),
                )
            )
        valid_key = valid_node.get(kwargs['key'], False)
        if not valid_key:
            raise ClickControlError(
                'KEY: {user_key} for click object: {node} was not found. '\
                'valid key targets are: {config_user_keys}\n'.format(
                    user_key=kwargs['key'],
                    node=kwargs['node'],
                    config_user_keys=valid_node.keys(),
                )
            )
        return orig_func(self, *args, **kwargs)
    return wrapper


# pylint: disable=too-many-instance-attributes
class ClickControlAgent(DispatchAgent):
    """
    This click control agent allows the dynamic control of click-based network emulation
    """
    def __init__(self):
        DispatchAgent.__init__(self)
        self.log = logging.getLogger(__name__)
        self.click_config = "/tmp/vrouter.click"
        self.udp_running = False
        self.is_flapping = False
        self.ccp = ClickConfigParser()
        self.clg = clickGraph.clickGraph(self.click_config)
        # assumes clicks installed, should we install?
        self._click_proc = None
        self._conf_path = '/click'   # handle to click's runtime configuration.

    @agentmethod()
    # pylint: disable=unused-argument,no-self-use
    def updateVisualization(self, msg):
        return True

    @agentmethod()
    def startClick(self, msg, user_mode=False, dpdk=True):
        click_running = False
        # Check if click configuration exists
        if not os.path.isfile(self.click_config):
            self.log.error("Click: no such configuration file %s", self.click_config)
            return False

        if not user_mode and not dpdk:
            # Check if the module is loaded.  If so, uninstall click first and reinstall
            (output, err) = execl.execAndRead("lsmod")
            if err != "":
                self.log.error("Click: %s", err)
                return False

            tokens = output.split()

            for token in tokens:
                if token == "click":
                    click_running = True
                    break

            if click_running:
                self.stopClick(msg)

            (output, err) = execl.execAndRead("sudo click-install -j 2 %s" % self.click_config)
            if err != "":
                self.log.error("Click: %s", err)
                return False

        else:  # not in kernel - does not daemonize itself, so we handle it as a proc.
            if dpdk:
                cmd = 'click  --dpdk -c 0xffffff -n 4 -- -u /click {}'.format(self.click_config)
            elif user_mode:
                cmd = 'click {} -u /click'.format(self.click_config)
            else:
                self.log.error('startClick must be one of kernel, user_mode, or dpdk')

            self.log.info('Running cmd: %s', cmd)
            self._click_proc = Popen(cmd.split())
            time.sleep(1)   # give it sec to fail...
            if self._click_proc.poll():
                self.log.error(
                    "Error starting click in user space. exit=%s",
                    self._click_proc.poll()
                )
                self._click_proc = None
                return False

            self.log.info('user space click started. pid=%s', self._click_proc.pid)

        return True


    @agentmethod()
    # pylint: disable=unused-argument
    def stopClick(self, msg):
        if not self._click_proc:
            (_, err) = execl.execAndRead("sudo click-uninstall")
            if err != "":
                self.log.error("Click: %s", err)
                return False

            os.rmdir('/click')   # process does not clean up the soket when killed.

        else:
            self._click_proc.kill()
            self._click_proc.wait()   # GTL may not want this.
            self._click_proc = None
            os.remove('/click')   # process does not clean up the soket when killed.

        return True

    @agentmethod()
    # pylint: disable=dangerous-default-value, too-many-arguments, too-many-return-statements, too-many-branches
    # FIXME: function needs to be re-written for clarity
    def updateLinks(self, msg, links=[], delays=[], capacities=[], losses=[]):
        # pylint: disable=len-as-condition
        if len(links) != len(delays):
            if len(delays) != 1 and len(delays) != 0:
                self.log.error("Click: must specify delay for each link or specify only 1 or 0")
                return False
        if len(links) != len(capacities):
            if len(capacities) != 1 and len(capacities) != 0:
                self.log.error("Click: must specify capacity for each link or specify only 1 or 0")
                return False
        if len(links) != len(losses):
            if len(losses) != 1 and len(losses) != 0:
                self.log.error(
                    "Click: must specify loss probibility for each link or specify only 1 or 0"
                )
                return False

        skip_delay = False
        skip_capacity = False
        skip_loss = False
        if len(delays) == 0:
            skip_delay = True
        if len(capacities) == 0:
            skip_capacity = True
        if len(losses) == 0:
            skip_loss = True

        for link_number, link in enumerate(links):
            if not skip_delay:
                c_delay = ""
                if len(delays) == 1:
                    c_delay = delays[0]
                else:
                    c_delay = delays[link_number]

                ret = self.updateDelay(msg, link=link, delay=c_delay)
                if not ret:
                    return False

            if not skip_capacity:
                c_cap = ""
                if len(capacities) == 1:
                    c_cap = capacities[0]
                else:
                    c_cap = capacities[link_number]

                ret = self.updateCapacity(msg, link=link, capacity=c_cap)
                if not ret:
                    return False

            if not skip_loss:
                c_loss = ""
                if len(losses) == 1:
                    c_loss = losses[0]
                else:
                    c_loss = losses[link_number]

                ret = self.updateLossProbability(msg, link=link, loss=c_loss)
                if not ret:
                    return False

        return True

    @validateClickInputs
    # pylint: disable=unused-argument
    def updateClickConfig(self, msg, node, key, value):
        '''If you know the exact click node and key you can update teh value directly.'''
        # Note: if you change the names of these arguments, also do so in validateClickInputs
        ret_val = False
        try:
            self.ccp.parse(self._conf_path)
            ret_val = self.ccp.set_value(node, key, value)
        except ClickConfigParserException as err:
            self.log.error(err)

        return ret_val

    @agentmethod()
    def updateDelay(self, msg, link="", delay="0.0ms"):
        # this config can be 'delay' or 'latency'
        ret_val = True
        allowed_keys = self.ccp.get_configuration()[link].keys()
        if 'latency' in allowed_keys:
            ret_val = self.updateClickConfig(msg, '{}_bw'.format(link), 'latency', delay)
        elif 'delay' in allowed_keys:
            ret_val = self.updateClickConfig(msg, '{}_bw'.format(link), 'delay', delay)
        else:
            ret_val = (False, None, 'key not found in link attribute list')
        return ret_val

    @agentmethod()
    def updateCapacity(self, msg, link="", capacity="1Gbps"):
        # Older versions of click use 'rate'. So we set both rate and bandwidth
        ret_val = True
        allowed_keys = self.ccp.get_configuration()[link].keys()
        if 'bandwidth' in allowed_keys:
            ret_val = self.updateClickConfig(msg, '{}_bw'.format(link), 'bandwidth', capacity)
        elif 'rate' in allowed_keys:
            ret_val = self.updateClickConfig(msg, '{}_bw'.format(link), 'rate', capacity)
        else:
            ret_val = (False, None, 'key not found in link attribute list')
        return ret_val

    @agentmethod()
    def updateLossProbability(self, msg, link="", loss="0.0"):
        return self.updateClickConfig(msg, '{}_loss'.format(link), 'drop_prob', loss)

    @agentmethod()
    # pylint: disable=unused-argument, too-many-arguments
    def updateTargetedLoss(self, msg, link=None, prefix=None, destination=None,
                           source=None, clear_drops=None,
                           burst=None, drop_prob=None, active=True):
        node = '{}_TL'.format(link)  # TL hardcoded here and in template as a TargetedLoss node.
        try:
            self.ccp.parse(self._conf_path)
            # set all values given. return False if any fail.
            args = {
                'prefix': prefix, 'dest': destination, 'source': source,
                'clear_drops': clear_drops, 'burst': burst, 'drop_prob': drop_prob
            }
            self.log.info('setting targeted loss config: %s', args)
            for key, value in args.iteritems():
                if value is not None:   # can be zero or ''!
                    if not self.ccp.set_value(node, key, value):
                        self.log.info('Error setting %s --> %s in updateTargetedLoss', key, value)
                        return False

            if not self.ccp.set_value(node, 'active', str(active).lower()):  # lower case in click
                self.log.info('Error setting targeted loss link active to %s', active)
                return False

        except ClickConfigParserException as err:
            self.log.error(err)
            return False

        return True

    @agentmethod()
    # pylint: disable=too-many-arguments, unused-argument
    def updateSimpleReorder(self, msg, link=None, timeout=None,
                            packets=None, sampling_prob=None, active=True):
        node = '{}_SR'.format(link)
        try:
            self.ccp.parse(self._conf_path)
            args = {'timeout': timeout, 'packets': packets, 'sampling_prob': sampling_prob}
            self.log.info('setting simple reorder config: %s', args)
            for key, value in args.iteritems():
                if value is not None:
                    if not self.ccp.set_value(node, key, value):
                        self.log.info('Error setting %s --> %s in updateSimpleReorder', key, value)
                        return False

            if not self.ccp.set_value(node, 'active', str(active).lower()):  # lower case in click
                self.log.info('Error setting simple reorder link active to %s', active)
                return False

        except ClickConfigParserException as err:
            self.log.error(err)
            return False

        return True

    @agentmethod()
    # pylint: disable=too-many-arguments
    def updateRoute(self, msg, router="", ip_addr="", port="", next_hop=""):
        re_match = re.match("[0-9]+", router)
        if re_match:
            router = "router%s" % re_match.group(0)

        if port == "":
            re_match = re.match("[0-9]+", next_hop)
            if re_match:
                next_hop = "router%s" % re_match.group(0)
            port = self.clg.getPort(router, next_hop)
            if not port:
                self.log.error("Click: Cannot find link between %s and %s\n", router, next_hop)
                return False

        return self.updateClickConfig(msg, router, 'set', '{} {}'.format(ip_addr, port))

    @agentmethod()
    # pylint: disable=dangerous-default-value
    def updateRoutes(self, msg, path=[], ip_addr=""):
        # this is a hack to do range(len(path)-1)
        for path_number, router in enumerate(path[:-1]):
            next_hop = path[path_number + 1]
            self.updateRoute(msg, router=router, ip_addr=ip_addr, next_hop=next_hop)

    @agentmethod()
    # pylint: disable=unused-argument
    def anycastHijack(self, msg, prefix, advertisers, random_start=False):
        updated_hops = self.clg.anycastSPF(prefix, advertisers, random_start)
        if not updated_hops:
            # need better error messages
            return False

        for hop in updated_hops:
            if os.path.exists("/proc/click/%s/set" % hop[0]):
                self.updateRoute(None, router=hop[0], ip_addr=prefix, next_hop=hop[1])
        return True

    @agentmethod()
    # pylint: disable=unused-argument
    def startRouteFlaps(self, msg, flaps, rate):
        if not self.is_flapping:
            self.is_flapping = True
        flap_thread = threading.Thread(target=self.routeFlap, args=(flaps, rate, -1))
        flap_thread.start()
        return True

    @agentmethod()
    # pylint: disable=unused-argument
    def stopRouteFlaps(self, msg):
        self.is_flapping = False
        return True

    @agentmethod()
    # pylint: disable=unused-argument, no-self-use
    def flapForDuration(self, msg, flaps, rate, duration):
        return True


    @agentmethod()
    def routeFlap(self, flaps, duration):
        while self.is_flapping:
            for flap in flaps:
                self.log.warn("ip = %s, router = %s, next_hop = %s", flap[0], flap[1], flap[2])
                self.updateRoute(None, ip_addr=flap[0], router=flap[1], next_hop=flap[2])
            time.sleep(duration)
            if not self.is_flapping:
                break
            for flap in flaps:
                self.updateRoute(None, ip_addr=flap[0], router=flap[1], next_hop=flap[3])
            time.sleep(duration)
        return True


    @agentmethod()
    def startUDPTraffic(self, msg, node="source"):
        self.udp_running = self.updateClickConfig(msg, node, 'active', 'true')
        return self.udp_running

    @agentmethod()
    def stopUDPTraffic(self, msg, node="source"):
        self.udp_running = self.updateClickConfig(msg, node, 'active', 'false')
        return self.udp_running

    @agentmethod()
    def setUDPRate(self, msg, rate="100Mbps", node="source"):
        packet_size = 8000 # 1 KB
        re_match = re.match("[1-9][0-9]*[ ]*[GKMgkm]?[Bb]ps", rate)
        if not re_match:
            self.log.error("Click: Invalid rate %s", rate)
            return False

        rate = re_match.group(0)
        re_match = re.search("[1-9][0-9]*", rate)
        if not re_match:
            self.log.error("Click: Invalid rate %s", rate)
            return False

        init_rate = int(re_match.group(0))

        re_match = re.search("[GKMgkm]", rate)
        factor = 1
        if re_match:
            if re_match.group(0) == "G" or re_match.group(0) == "g":
                factor = 1000000000
            elif re_match.group(0) == "M" or re_match.group(0) == "m":
                factor = 1000000
            elif re_match.group(0) == "K" or re_match.group(0) == "k":
                factor = 1000
            else:
                factor = 1

        re_match = re.search("[Bb]", rate)
        multiplier = 1
        if not re_match:
            self.log.error("Click: Invalid rate %s", rate)
            return False
        if re_match.group(0) == "B":
            multiplier = 8

        rate_in_pps = math.floor(init_rate * factor * multiplier / packet_size)

        was_running = self.udp_running
        if self.udp_running:
            self.stopUDPTraffic(msg, node)

        # FIXME: whoever, does this need to be verified to have worked?
        self.updateClickConfig(msg, node, 'rate', rate_in_pps)

        if was_running:
            self.startUDPTraffic(msg, node)

        return True
