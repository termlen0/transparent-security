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
#
# Originally copied from:
#
# Copyright 2017-present Open Networking Foundation
#
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
#
# noinspection PyCompatibility
import logging
from Queue import Queue
from abc import abstractmethod
from datetime import datetime

import grpc
from threading import Thread
from p4.v1 import p4runtime_pb2, p4runtime_pb2_grpc

from trans_sec.controller.ddos_sdn_controller import GATEWAY_CTRL_KEY
from trans_sec.utils.convert import decode_mac

MSG_LOG_MAX_LEN = 1024

logger = logging.getLogger('switch')


class SwitchConnection(object):

    def __init__(self, p4info_helper, sw_info, p4_ingress, p4_egress,
                 proto_dump_file=None):
        self.p4info_helper = p4info_helper
        self.sw_info = sw_info
        self.p4_ingress = p4_ingress
        self.p4_egress = p4_egress
        self.name = sw_info['name']
        self.device_id = sw_info['id']
        channel = grpc.insecure_channel(sw_info['grpc'])
        if proto_dump_file is not None:
            logger.info('Adding interceptor with file - [%s]', proto_dump_file)
            interceptor = GrpcRequestLogger(proto_dump_file)
            channel = grpc.intercept_channel(channel, interceptor)

        logger.info('Creating client stub to channel - [%s] at address - [%s]',
                    channel, self.sw_info['grpc'])
        self.client_stub = p4runtime_pb2_grpc.P4RuntimeStub(channel)
        self.requests_stream = IterableQueue()
        self.stream_msg_resp = self.client_stub.StreamChannel(iter(
            self.requests_stream))
        self.proto_dump_file = proto_dump_file

        self.digest_thread = Thread(target=self.receive_digests)

    def start_digest_listeners(self):
        logger.info('Starting Digest thread')
        digest_entry, digest_id = self.p4info_helper.build_digest_entry(
            digest_name="mac_learn_digest")
        self.write_digest_entry(digest_entry)
        self.digest_thread.start()

    def stop_digest_listeners(self):
        self.requests_stream.close()
        self.stream_msg_resp.cancel()
        self.digest_thread.join()

    def receive_digests(self):
        """
        Runnable method for self.digest_thread
        """
        logger.info("Started listening digest thread on device [%s] with "
                    "name [%s]", self.device_id, self.name)
        while True:
            try:
                logger.debug('Requesting digests from device [%s]',
                             self.device_id)
                digests = self.digest_list()
                logger.debug('digests from device [%s] - [%s]',
                             self.device_id, digests)
                digest_data = digests.digest.data
                logger.debug('Received digest data from device [%s]: [%s]',
                             self.device_id, digest_data)
                self.interpret_digest(digest_data)
                logger.debug('Interpreted digest data')
            except Exception as e:
                logger.error(
                    'Unexpected error reading digest from device [%s] - [%s]',
                    self.device_id, e.message)

    def interpret_digest(self, digest_data):
        logger.debug("Digest data from switch [%s] - [%s]",
                     self.name, digest_data)

        if not digest_data or len(digest_data) == 0:
            logger.warn('No digest data to process')
            return
        for members in digest_data:
            logger.debug("Digest members: %s", members)
            if members.WhichOneof('data') == 'struct':
                source_mac = decode_mac(members.struct.members[0].bitstring)
                logger.debug('Digested MAC Address is: %s', source_mac)
                ingress_port = int(
                    members.struct.members[1].bitstring.encode('hex'), 16)
                logger.debug('Ingress Port is %s', ingress_port)
                self.add_data_forward(source_mac, ingress_port)
            else:
                logger.warn('Digest could not be processed - [%s]',
                            digest_data)

        logger.info('Completed digest processing')

    def insert_p4_table_entry(self, table_name, action_name, match_fields,
                              action_params, ingress_class=True):

        logger.info(
            'Adding to table - [%s], with fields - [%s], and params - [%s]',
            table_name, match_fields, action_params)

        if ingress_class:
            tbl_class = self.p4_ingress
        else:
            tbl_class = self.p4_egress

        table_entry = self.p4info_helper.build_table_entry(
            table_name='{}.{}'.format(tbl_class, table_name),
            match_fields=match_fields,
            action_name='{}.{}'.format(tbl_class, action_name),
            action_params=action_params)
        logger.debug(
            'Writing table entry to device [%s] table [%s], '
            'with action name - [%s], '
            'match fields - [%s], action_params - [%s]',
            self.device_id, table_name, action_name, match_fields,
            action_params)
        self.write_table_entry(table_entry)

    def add_data_forward(self, source_mac, ingress_port):
        logger.info(
            'Adding data forward to device [%s] with source_mac '
            '- [%s] and ingress port - [%s]',
            self.device_id, source_mac, ingress_port)

        if self.sw_info['type'] == GATEWAY_CTRL_KEY:
            action_params = {
                'port': ingress_port,
                'switch_mac': self.sw_info['mac']
            }
        else:
            action_params = {'port': ingress_port}

        table_name = '{}.data_forward_t'.format(self.p4_ingress)

        table_keys = self.get_data_forward_macs()
        logger.debug('Table keys to [%s] - [%s] on device [%s]',
                     table_name, table_keys, self.device_id)

        if source_mac not in table_keys:
            logger.info(
                'Inserting entry into table [%s] with key [%s] - port [%s] '
                'on device [%s]',
                table_name, source_mac, ingress_port, self.device_id)
            table_entry = self.p4info_helper.build_table_entry(
                table_name=table_name,
                match_fields={
                    'hdr.ethernet.dst_mac': source_mac
                },
                action_name='{}.data_forward'.format(self.p4_ingress),
                action_params=action_params
            )
            self.write_table_entry(table_entry)

            table_keys = self.get_data_forward_macs()
            logger.debug(
                'Keys after insert on device [%s] to table [%s] - [%s]',
                self.device_id, table_name, table_keys)
            return True
        else:
            logger.info(
                'Data forward entry already inserted with key - [%s] '
                'on device [%s]',
                source_mac, self.device_id)
            return False

    @abstractmethod
    def add_data_inspection(self, dev_id, dev_mac):
        raise NotImplemented

    @abstractmethod
    def build_device_config(self, **kwargs):
        raise NotImplemented

    def master_arbitration_update(self):
        logger.info('Master arbitration update on switch - [%s]',
                    self.device_id)
        request = p4runtime_pb2.StreamMessageRequest()
        request.arbitration.device_id = self.device_id
        request.arbitration.election_id.high = 0
        request.arbitration.election_id.low = 1
        self.requests_stream.put(request)

    def set_forwarding_pipeline_config(self, device_config):
        logger.info('Setting Forwarding Pipeline Config on switch - [%s] ',
                    self.device_id)
        logger.debug('P4Info - [%s] ', self.p4info_helper.p4info)
        request = p4runtime_pb2.SetForwardingPipelineConfigRequest()
        request.election_id.low = 1
        request.device_id = self.device_id
        config = request.config

        config.p4info.CopyFrom(self.p4info_helper.p4info)
        config.p4_device_config = device_config.SerializeToString()

        request.action = \
            p4runtime_pb2.SetForwardingPipelineConfigRequest.VERIFY_AND_COMMIT

        logger.info('Request for SetForwardingPipelineConfig to device - [%s]',
                    self.name)
        self.client_stub.SetForwardingPipelineConfig(request)
        logger.info('Completed SetForwardingPipelineConfig to device - [%s]',
                    request.device_id)

    def write_table_entry(self, table_entry):
        self.__write_to_table(table_entry, p4runtime_pb2.Update.INSERT)

    def delete_table_entry(self, table_entry):
        self.__write_to_table(table_entry, p4runtime_pb2.Update.DELETE)

    def __write_to_table(self, table_entry, update_type):
        logger.info('Writing table entry on device [%s] - [%s]',
                    self.device_id, table_entry)
        request = p4runtime_pb2.WriteRequest()
        request.device_id = self.device_id
        request.election_id.low = 1
        update = request.updates.add()
        update.type = update_type
        update.entity.table_entry.CopyFrom(table_entry)
        try:
            logger.debug(
                'Request for writing table entry to device [%s] - [%s]',
                self.device_id, request)
            self.client_stub.Write(request)
        except Exception as e:
            logging.error('Error writing table entry to device [%s] - [%s]',
                          self.device_id, e)
            raise e

    def get_data_forward_macs(self):
        """
        Returns the key MAC values from the data_forward_t table
        :return: set dst_mac values
        """
        table_name = '{}.data_forward_t'.format(self.p4_ingress)
        match_vals = self.get_match_values(table_name)

        out = set()
        for match_val in match_vals:
            for key, value in match_val.items():
                if key == 'hdr.ethernet.dst_mac':
                    out.add(decode_mac(value))

        logger.debug('Data forward macs from device [%s] - [%s]',
                     self.device_id, out)
        return out

    def get_data_inspection_src_mac_keys(self):
        """
        Returns a set of macs that is a key to the data_inspection_t table
        :return: set src_mac values
        """
        table_name = '{}.data_inspection_t'.format(self.p4_ingress)
        match_vals = self.get_match_values(table_name)

        out = set()
        for match_val in match_vals:
            for key, value in match_val.items():
                if key == 'hdr.ethernet.src_mac':
                    out.add(decode_mac(value))
        logger.debug('Data Inspection macs from device [%s] - [%s]',
                     self.device_id, out)
        return out

    def get_match_values(self, table_name):
        """
        Returns a dict of match values in a list of dict where the dict
        objects' keys will be the field name and the value will be the raw
        byte value to be converted by the client
        :param table_name: the name of the table to query
        :return: a list of dict
        """
        logger.info('Retrieving keys from table [%s] on device [%s]',
                    table_name, self.device_id)
        out = list()
        table_entries = self.get_table_entries(table_name)
        entities = table_entries.next().entities
        for entity in entities:
            logger.debug('Entity to find matches on table [%s] - [%s]',
                         table_name, entity)
            table_entry = entity.table_entry
            match = table_entry.match.pop()

            while match is not None:
                logger.debug('match.field_id - [%s]', match.field_id)
                field_name = self.p4info_helper.get_match_field_name(
                    table_name, match.field_id)
                logger.debug('field_name - [%s]', field_name)
                out.append({field_name: match.exact.value})

                # TODO - find method to determine if more entries available
                try:
                    match = table_entry.match.pop()
                except Exception as e:
                    logger.warn('No more match items')
                    break
        logger.info('Table keys from table [%s] on device [%s] - [%s]',
                    table_name, self.device_id, out)
        return out

    def get_table_entries(self, table_name):
        """
        Returns a P4Runtime object for iterating through a table
        :param table_name:
        :return:
        """
        table_id = self.p4info_helper.get_id('tables', table_name)
        logger.debug('Reading table [%s] with ID - [%s]', table_name, table_id)
        return self.read_table_entries(table_id)

    def read_table_entries(self, table_id=None):
        logger.info('Reading table entry on switch [%s] with table ID - [%s]',
                    self.name, table_id)
        request = p4runtime_pb2.ReadRequest()
        request.device_id = self.device_id
        entity = request.entities.add()
        table_entry = entity.table_entry
        if table_id is not None:
            table_entry.table_id = table_id
        else:
            table_entry.table_id = 0

        logger.debug('Request for reading table entries to device [%s]',
                     self.device_id)
        return self.client_stub.Read(request)

    def write_clone_entries(self, pre_entry):
        logger.info(
            'Packet info for insertion to device [%s] cloning table - %s',
            self.device_id, pre_entry)
        request = p4runtime_pb2.WriteRequest()
        request.device_id = self.device_id
        request.election_id.low = 1
        update = request.updates.add()
        update.type = p4runtime_pb2.Update.INSERT
        update.entity.packet_replication_engine_entry.CopyFrom(pre_entry)

        try:
            logger.debug(
                'Request for writing a clone request to device [%s] - [%s]',
                self.device_id, request)
            self.client_stub.Write(request)
        except Exception as e:
            logging.error('Error requesting [%s] clone - [%s]', request, e)
            raise e

    def delete_clone_entries(self, pre_entry):
        logger.info(
            'Packet info from device [%s] for deleting the clone entry - %s',
            self.device_id, pre_entry)
        request = p4runtime_pb2.WriteRequest()
        request.device_id = self.device_id
        request.election_id.low = 1
        update = request.updates.add()
        update.type = p4runtime_pb2.Update.DELETE
        update.entity.packet_replication_engine_entry.CopyFrom(pre_entry)

        try:
            logger.debug(
                'Request for deleting a clone entry on the device [%s] - [%s]',
                self.device_id, request)
            self.client_stub.Write(request)
        except Exception as e:
            logging.error('Error requesting [%s] clone - [%s]', request, e)
            raise e

    def read_counters(self, counter_id=None, index=None):
        logger.info(
            'Read counter on device [%s] with ID - [%s] and index - [%s]',
            self.device_id, counter_id, index)
        request = p4runtime_pb2.ReadRequest()
        request.device_id = self.device_id
        entity = request.entities.add()
        counter_entry = entity.counter_entry
        if counter_id is not None:
            counter_entry.counter_id = counter_id
        else:
            counter_entry.counter_id = 0
        if index is not None:
            counter_entry.index.index = index

        logger.debug('Request for reading counters to device [%s] - [%s]',
                     self.device_id, request)
        for response in self.client_stub.Read(request):
            yield response

    def reset_counters(self, counter_id=None, index=None):
        logger.info('Reset counter with ID - [%s] and index - [%s]',
                    counter_id, index)
        request = p4runtime_pb2.WriteRequest()
        request.device_id = self.device_id
        request.election_id.low = 1
        update = request.updates.add()
        update.type = p4runtime_pb2.Update.MODIFY
        counter_entry = p4runtime_pb2.CounterEntry()
        if counter_id is not None:
            counter_entry.counter_id = counter_id
        else:
            counter_entry.counter_id = 0
        if index is not None:
            counter_entry.index.index = index
        counter_entry.data.byte_count = 0
        counter_entry.data.packet_count = 0
        update.entity.counter_entry.CopyFrom(counter_entry)

        logger.debug('Request for resetting counters to device [%s] - [%s]',
                     self.device_id, request)
        self.client_stub.Write(request)

    def write_digest_entry(self, digest_entry):
        request = p4runtime_pb2.WriteRequest()
        request.device_id = self.device_id
        request.election_id.low = 1
        update = request.updates.add()
        update.type = p4runtime_pb2.Update.INSERT
        update.entity.digest_entry.CopyFrom(digest_entry)
        self.client_stub.Write(request)

    def digest_list_ack(self, digest_ack):
        request = p4runtime_pb2.StreamMessageRequest()
        request.digest_ack.CopyFrom(digest_ack)
        self.requests_stream.put(request)

    def digest_list(self):
        logger.info('Retrieving digest list for device [%s]', self.device_id)
        for item in self.stream_msg_resp:
            logger.debug('Returning digest - [%s]', item)
            return item
        logger.warn('No digests found on device [%s]', self.device_id)

    def write_multicast_entry(self, hosts):
        mc_group_id = 1
        mc_entries = self.sw_info.get('multicast_entries')
        if mc_entries:
            multicast_entry = self.p4info_helper.build_multicast_group_entry(
                mc_group_id, mc_entries)
            logger.info('Build Multicast Entry on device [%s]: [%s]',
                        multicast_entry)

            request = p4runtime_pb2.WriteRequest()
            request.device_id = self.device_id
            request.election_id.low = 1
            update = request.updates.add()
            update.type = p4runtime_pb2.Update.INSERT
            update.entity.packet_replication_engine_entry.CopyFrom(
                multicast_entry)
            try:
                logger.debug(
                    'Request for writing a multicast entry to '
                    'device [%s] - [%s]',
                    self.device_id, request)
                self.client_stub.Write(request)
            except Exception as e:
                logging.error('Error requesting [%s] multicast - [%s]',
                              request, e)
                raise e

    def write_arp_flood(self):
        logger.info('Adding ARP Flood to device [%s]', self.device_id)
        table_entry = self.p4info_helper.build_table_entry(
            table_name='{}.arp_flood_t'.format(self.p4_ingress),
            match_fields={'hdr.ethernet.dst_mac': 'ff:ff:ff:ff:ff:ff'},
            action_name='{}.arp_flood'.format(self.p4_ingress),
            action_params={})
        self.write_table_entry(table_entry)


class GrpcRequestLogger(grpc.UnaryUnaryClientInterceptor,
                        grpc.UnaryStreamClientInterceptor):
    """Implementation of a gRPC interceptor that logs request to a file"""

    def __init__(self, log_file):
        self.log_file = log_file
        with open(self.log_file, 'w') as f:
            # Clear content if it exists.
            f.write("")

    def log_message(self, method_name, body):
        with open(self.log_file, 'a') as f:
            ts = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            msg = str(body)
            f.write("\n[%s] %s\n---\n" % (ts, method_name))
            if len(msg) < MSG_LOG_MAX_LEN:
                f.write(str(body))
            else:
                f.write(
                    "Message too long (%d bytes)! Skipping log...\n" % len(
                        msg))
            f.write('---\n')

    def intercept_unary_unary(self, continuation, client_call_details,
                              request):
        self.log_message(client_call_details.method, request)
        return continuation(client_call_details, request)

    def intercept_unary_stream(self, continuation, client_call_details,
                               request):
        self.log_message(client_call_details.method, request)
        return continuation(client_call_details, request)


class IterableQueue(Queue):
    _sentinel = object()

    def __iter__(self):
        return iter(self.get, self._sentinel)

    def close(self):
        self.put(self._sentinel)
