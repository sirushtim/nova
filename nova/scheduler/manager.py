# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright (c) 2010 OpenStack Foundation
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Scheduler Service
"""

import sys

from oslo.config import cfg

from nova.compute import rpcapi as compute_rpcapi
from nova.compute import task_states
from nova.compute import utils as compute_utils
from nova.compute import vm_states
from nova.conductor import api as conductor_api
import nova.context
from nova import exception
from nova import manager
from nova import notifications
from nova.openstack.common import excutils
from nova.openstack.common import importutils
from nova.openstack.common import jsonutils
from nova.openstack.common import log as logging
from nova.openstack.common.notifier import api as notifier
from nova import quota


LOG = logging.getLogger(__name__)

scheduler_driver_opt = cfg.StrOpt('scheduler_driver',
        default='nova.scheduler.filter_scheduler.FilterScheduler',
        help='Default driver to use for the scheduler')

CONF = cfg.CONF
CONF.register_opt(scheduler_driver_opt)

QUOTAS = quota.QUOTAS


class SchedulerManager(manager.Manager):
    """Chooses a host to run instances on."""

    RPC_API_VERSION = '2.6'

    def __init__(self, scheduler_driver=None, *args, **kwargs):
        if not scheduler_driver:
            scheduler_driver = CONF.scheduler_driver
        self.driver = importutils.import_object(scheduler_driver)
        super(SchedulerManager, self).__init__(service_name='scheduler',
                                               *args, **kwargs)

    def post_start_hook(self):
        """After we start up and can receive messages via RPC, tell all
        compute nodes to send us their capabilities.
        """
        ctxt = nova.context.get_admin_context()
        compute_rpcapi.ComputeAPI().publish_service_capabilities(ctxt)

    def update_service_capabilities(self, context, service_name,
                                    host, capabilities):
        """Process a capability update from a service node."""
        if not isinstance(capabilities, list):
            capabilities = [capabilities]
        for capability in capabilities:
            if capability is None:
                capability = {}
            self.driver.update_service_capabilities(service_name, host,
                                                    capability)

    def create_volume(self, context, volume_id, snapshot_id,
                      reservations=None, image_id=None):
        #function removed in RPC API 2.3
        pass

    def live_migration(self, context, instance, dest,
                       block_migration, disk_over_commit):
        try:
            return self.driver.schedule_live_migration(
                context, instance, dest,
                block_migration, disk_over_commit)
        except (exception.NoValidHost,
                exception.ComputeServiceUnavailable,
                exception.InvalidHypervisorType,
                exception.UnableToMigrateToSelf,
                exception.DestinationHypervisorTooOld,
                exception.InvalidLocalStorage,
                exception.InvalidSharedStorage) as ex:
            request_spec = {'instance_properties': {
                'uuid': instance['uuid'], },
            }
            with excutils.save_and_reraise_exception():
                self._set_vm_state_and_notify('live_migration',
                            dict(vm_state=instance['vm_state'],
                                 task_state=None,
                                 expected_task_state=task_states.MIGRATING,),
                                              context, ex, request_spec)
        except Exception as ex:
            with excutils.save_and_reraise_exception():
                self._set_vm_state_and_notify('live_migration',
                                             {'vm_state': vm_states.ERROR},
                                             context, ex, {})

    def run_instance(self, context, request_spec, admin_password,
            injected_files, requested_networks, is_first_time,
            filter_properties):
        """Tries to call schedule_run_instance on the driver.
        Sets instance vm_state to ERROR on exceptions
        """
        instance_uuids = request_spec['instance_uuids']
        with compute_utils.EventReporter(context, conductor_api.LocalAPI(),
                                         'schedule', *instance_uuids):
            try:
                return self.driver.schedule_run_instance(context,
                        request_spec, admin_password, injected_files,
                        requested_networks, is_first_time, filter_properties)

            except exception.NoValidHost as ex:
                # don't re-raise
                self._set_vm_state_and_notify('run_instance',
                                              {'vm_state': vm_states.ERROR,
                                              'task_state': None},
                                              context, ex, request_spec)
            except Exception as ex:
                with excutils.save_and_reraise_exception():
                    self._set_vm_state_and_notify('run_instance',
                                                  {'vm_state': vm_states.ERROR,
                                                  'task_state': None},
                                                  context, ex, request_spec)

    def prep_resize(self, context, image, request_spec, filter_properties,
                    instance, instance_type, reservations):
        """Tries to call schedule_prep_resize on the driver.
        Sets instance vm_state to ACTIVE on NoHostFound
        Sets vm_state to ERROR on other exceptions
        """
        instance_uuid = instance['uuid']
        with compute_utils.EventReporter(context, conductor_api.LocalAPI(),
                                         'schedule', instance_uuid):
            try:
                kwargs = {
                    'context': context,
                    'image': image,
                    'request_spec': request_spec,
                    'filter_properties': filter_properties,
                    'instance': instance,
                    'instance_type': instance_type,
                    'reservations': reservations,
                }
                return self.driver.schedule_prep_resize(**kwargs)
            except exception.NoValidHost as ex:
                self._set_vm_state_and_notify('prep_resize',
                                             {'vm_state': vm_states.ACTIVE,
                                              'task_state': None},
                                             context, ex, request_spec)
                if reservations:
                    QUOTAS.rollback(context, reservations)
            except Exception as ex:
                with excutils.save_and_reraise_exception():
                    self._set_vm_state_and_notify('prep_resize',
                                                 {'vm_state': vm_states.ERROR,
                                                  'task_state': None},
                                                 context, ex, request_spec)
                    if reservations:
                        QUOTAS.rollback(context, reservations)

    def _set_vm_state_and_notify(self, method, updates, context, ex,
                                 request_spec):
        """changes VM state and notifies."""
        # FIXME(comstud): Re-factor this somehow. Not sure this belongs in the
        # scheduler manager like this. We should make this easier.
        # run_instance only sends a request_spec, and an instance may or may
        # not have been created in the API (or scheduler) already. If it was
        # created, there's a 'uuid' set in the instance_properties of the
        # request_spec.
        # (littleidea): I refactored this a bit, and I agree
        # it should be easier :)
        # The refactoring could go further but trying to minimize changes
        # for essex timeframe

        LOG.warning(_("Failed to schedule_%(method)s: %(ex)s") % locals())

        vm_state = updates['vm_state']
        properties = request_spec.get('instance_properties', {})
        # NOTE(vish): We shouldn't get here unless we have a catastrophic
        #             failure, so just set all instances to error. if uuid
        #             is not set, instance_uuids will be set to [None], this
        #             is solely to preserve existing behavior and can
        #             be removed along with the 'if instance_uuid:' if we can
        #             verify that uuid is always set.
        uuids = [properties.get('uuid')]
        for instance_uuid in request_spec.get('instance_uuids') or uuids:
            if instance_uuid:
                state = vm_state.upper()
                LOG.warning(_('Setting instance to %(state)s state.'),
                            locals(), instance_uuid=instance_uuid)

                # update instance state and notify on the transition
                (old_ref, new_ref) = self.db.instance_update_and_get_original(
                        context, instance_uuid, updates)
                notifications.send_update(context, old_ref, new_ref,
                        service="scheduler")
                compute_utils.add_instance_fault_from_exc(context,
                        conductor_api.LocalAPI(),
                        new_ref, ex, sys.exc_info())

            payload = dict(request_spec=request_spec,
                           instance_properties=properties,
                           instance_id=instance_uuid,
                           state=vm_state,
                           method=method,
                           reason=ex)

            notifier.notify(context, notifier.publisher_id("scheduler"),
                            'scheduler.' + method, notifier.ERROR, payload)

    # NOTE (masumotok) : This method should be moved to nova.api.ec2.admin.
    # Based on bexar design summit discussion,
    # just put this here for bexar release.
    def show_host_resources(self, context, host):
        """Shows the physical/usage resource given by hosts.

        :param context: security context
        :param host: hostname
        :returns:
            example format is below::

                {'resource':D, 'usage':{proj_id1:D, proj_id2:D}}
                D: {'vcpus': 3, 'memory_mb': 2048, 'local_gb': 2048,
                    'vcpus_used': 12, 'memory_mb_used': 10240,
                    'local_gb_used': 64}

        """
        # Getting compute node info and related instances info
        service_ref = self.db.service_get_by_compute_host(context, host)
        instance_refs = self.db.instance_get_all_by_host(context,
                                                         service_ref['host'])

        # Getting total available/used resource
        compute_ref = service_ref['compute_node'][0]
        resource = {'vcpus': compute_ref['vcpus'],
                    'memory_mb': compute_ref['memory_mb'],
                    'local_gb': compute_ref['local_gb'],
                    'vcpus_used': compute_ref['vcpus_used'],
                    'memory_mb_used': compute_ref['memory_mb_used'],
                    'local_gb_used': compute_ref['local_gb_used']}
        usage = dict()
        if not instance_refs:
            return {'resource': resource, 'usage': usage}

        # Getting usage resource per project
        project_ids = [i['project_id'] for i in instance_refs]
        project_ids = list(set(project_ids))
        for project_id in project_ids:
            vcpus = [i['vcpus'] for i in instance_refs
                     if i['project_id'] == project_id]

            mem = [i['memory_mb'] for i in instance_refs
                   if i['project_id'] == project_id]

            root = [i['root_gb'] for i in instance_refs
                    if i['project_id'] == project_id]

            ephemeral = [i['ephemeral_gb'] for i in instance_refs
                         if i['project_id'] == project_id]

            usage[project_id] = {'vcpus': sum(vcpus),
                                 'memory_mb': sum(mem),
                                 'root_gb': sum(root),
                                 'ephemeral_gb': sum(ephemeral)}

        return {'resource': resource, 'usage': usage}

    @manager.periodic_task
    def _expire_reservations(self, context):
        QUOTAS.expire(context)

    # NOTE(russellb) This method can be removed in 3.0 of this API.  It is
    # deprecated in favor of the method in the base API.
    def get_backdoor_port(self, context):
        return self.backdoor_port

    def select_hosts(self, context, request_spec, filter_properties):
        """Returns host(s) best suited for this request_spec and
        filter_properties"""
        hosts = self.driver.select_hosts(context, request_spec,
            filter_properties)
        return jsonutils.to_primitive(hosts)
