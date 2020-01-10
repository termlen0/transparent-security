/*
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
*/
/* -*- P4_16 -*- */
#include <core.p4>
#include <v1model.p4>
#include <tps_consts.p4>

/*************************************************************************
*********************** P A R S E R  ***********************************
*************************************************************************/
parser TpsParser(packet_in packet,
                out headers hdr,
                inout metadata meta,
                inout standard_metadata_t standard_metadata) {

    state start {
        transition parse_ethernet;
    }

    state parse_ethernet {
        packet.extract(hdr.ethernet);
        transition select(hdr.ethernet.etherType) {
            TYPE_INSPECTION: parse_gw_int;
            TYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }

    state parse_gw_int {
        packet.extract(hdr.gw_int);
        transition select(hdr.gw_int.proto_id) {
            TYPE_IPV4: parse_sw_int_ipv4;
            default: accept;
        }
    }

    state parse_sw_int_ipv4 {
        packet.extract(hdr.sw_int);
        transition select(hdr.sw_int.switch_id) {
            default: parse_ipv4;
        }
    }

    state parse_ipv4 {
        packet.extract(hdr.ipv4);
        transition select(hdr.ipv4.protocol) {
            TYPE_UDP : parse_udp;
            default: accept;
        }
    }

    state parse_udp {
        packet.extract(hdr.udp);
        transition select(hdr.udp.dst_port) {
            default: accept;
        }
    }

}
