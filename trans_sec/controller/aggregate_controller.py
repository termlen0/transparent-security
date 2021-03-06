# Copyright (c) 2019 Cable Television Laboratories, Inc.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
from logging import getLogger
from trans_sec.controller.abstract_controller import AbstractController
from trans_sec.controller.ddos_sdn_controller import AGG_CTRL_KEY
from trans_sec.p4runtime_lib.bmv2 import AggregateSwitch

logger = getLogger('aggregate_controller')


class AggregateController(AbstractController):
    """
    Implementation of the controller for a switch running the aggregate.p4
    program
    """
    def __init__(self, platform, p4_build_out, topo, log_dir, load_p4=True):
        super(self.__class__, self).__init__(
            platform, p4_build_out, topo, AGG_CTRL_KEY, log_dir, load_p4)

    def instantiate_switch(self, sw_info):
        return AggregateSwitch(
            p4info_helper=self.p4info_helper,
            sw_info=sw_info,
            proto_dump_file='{}/{}-switch-controller.log'.format(
                self.log_dir, sw_info['name']))

    def make_north_rules(self, sw, sw_info, north_link):
        if north_link.get('north_facing_port'):
            logger.info('Creating north switch rules - [%s]', north_link)

            # north_node = self.topo['switches'][north_link['north_node']]
            if (self.topo.get('switches')
                    and north_link['north_node'] in self.topo['switches']):
                logger.debug('North node from switches')
                north_node = self.topo['switches'][north_link['north_node']]
            else:
                logger.debug('North node from hosts')
                north_node = self.topo['hosts'][north_link['north_node']]

            logger.info(
                'Aggregate: %s connects northbound to Core: %s on physical '
                'port %s to physical port %s',
                sw_info['name'], north_node,
                north_link.get('north_facing_port'),
                north_link.get('south_facing_port'))

            logger.info('Installed Northbound from port %s to port %s',
                        north_link.get('north_facing_port'),
                        north_link.get('south_facing_port'))
        else:
            logger.info('No north links to install')
