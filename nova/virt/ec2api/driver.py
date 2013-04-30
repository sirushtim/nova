# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 Justin Santa Barbara
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
Driver base-classes:

    (Beginning of) the contract that compute drivers must follow, and shared
    types that support that contract
"""

import sys

from oslo.config import cfg

from nova.openstack.common import importutils
from nova.openstack.common import log as logging
from nova import utils
from nova.virt import configdrive
from nova.virt.disk import api as disk
from nova.virt import driver
from nova.virt import event as virtevent
from nova.virt import firewall
import boto.ec2
import simplejson as json
from pprint import pprint
import time
import base64

driver_opts = [
    cfg.StrOpt('ec2API',default='boto',
               help='Driver to use for controlling virtualization. Options '
                   'include: libvirt.LibvirtDriver, xenapi.XenAPIDriver, '
                   'fake.FakeDriver, baremetal.BareMetalDriver, '
                   'vmwareapi.VMwareESXDriver, vmwareapi.VMwareVCDriver'
                   'ec2api.EC2Driver'),
    cfg.StrOpt('default_ephemeral_format',
               default=None,
               help='The default format an ephemeral_volume will be '
                    'formatted with on creation.'),
    cfg.StrOpt('preallocate_images',
               default='none',
               help='VM image preallocation mode: '
                    '"none" => no storage provisioning is done up front, '
                    '"space" => storage is fully allocated at instance start'),
    cfg.BoolOpt('use_cow_images',
                default=True,
                help='Whether to use cow images'),
]

CONF = cfg.CONF
CONF.register_opts(driver_opts)
LOG = logging.getLogger(__name__)


def driver_dict_from_config(named_driver_config, *args, **kwargs):
    driver_registry = dict()

    for driver_str in named_driver_config:
        driver_type, _sep, driver = driver_str.partition('=')
        driver_class = importutils.import_class(driver)
        driver_registry[driver_type] = driver_class(*args, **kwargs)

    return driver_registry


def block_device_info_get_root(block_device_info):
    block_device_info = block_device_info or {}
    return block_device_info.get('root_device_name')


def block_device_info_get_swap(block_device_info):
    block_device_info = block_device_info or {}
    return block_device_info.get('swap') or {'device_name': None,
                                             'swap_size': 0}


def swap_is_usable(swap):
    return swap and swap['device_name'] and swap['swap_size'] > 0


def block_device_info_get_ephemerals(block_device_info):
    block_device_info = block_device_info or {}
    ephemerals = block_device_info.get('ephemerals') or []
    return ephemerals


def block_device_info_get_mapping(block_device_info):
    block_device_info = block_device_info or {}
    block_device_mapping = block_device_info.get('block_device_mapping') or []
    return block_device_mapping


class EC2Driver(driver.ComputeDriver):
    """Base class for compute drivers.

    The interface to this class talks in terms of 'instances' (Amazon EC2 and
    internal Nova terminology), by which we mean 'running virtual machine'
    (XenAPI terminology) or domain (Xen or libvirt terminology).

    An instance has an ID, which is the identifier chosen by Nova to represent
    the instance further up the stack.  This is unfortunately also called a
    'name' elsewhere.  As far as this layer is concerned, 'instance ID' and
    'instance name' are synonyms.

    Note that the instance ID or name is not human-readable or
    customer-controlled -- it's an internal ID chosen by Nova.  At the
    nova.virt layer, instances do not have human-readable names at all -- such
    things are only known higher up the stack.

    Most virtualization platforms will also have their own identity schemes,
    to uniquely identify a VM or domain.  These IDs must stay internal to the
    platform-specific layer, and never escape the connection interface.  The
    platform-specific layer is responsible for keeping track of which instance
    ID maps to which platform-specific ID, and vice versa.

    Some methods here take an instance of nova.compute.service.Instance.  This
    is the data structure used by nova.compute to store details regarding an
    instance, and pass them into this layer.  This layer is responsible for
    translating that generic data structure into terms that are specific to the
    virtualization platform.

    """

    capabilities = {
        "has_imagecache": False,
        "supports_recreate": False,
        }

    def __init__(self, virtapi):
        super(EC2Driver, self).__init__(virtapi)
        self._compute_event_callback = None
        self.conn = boto.ec2.connect_to_region("us-east-1",aws_access_key_id='AKIAID3OJUWPDDSYJ6ZQ',aws_secret_access_key='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')

    def init_host(self, host):
        """Initialize anything that is necessary for the driver to function,
        including catching up with currently running VM's on the given host."""
        # TODO(Vek): Need to pass context in for access to auth_token
        pass

    def get_info(self, instance):
        """Get the current status of an instance, by name (not ID!)

        Returns a dict containing:

        :state:           the running state, one of the power_state codes
        :max_mem:         (int) the maximum memory in KBytes allowed
        :mem:             (int) the memory in KBytes used by the domain
        :num_cpu:         (int) the number of virtual CPUs for the domain
        :cpu_time:        (int) the CPU time used in nanoseconds
        """
        # TODO(Vek): Need to pass context in for access to auth_token
        dic = {'state' : 'building',
        'max_mem' : 613,
        'mem' : 200,
        'num_vcpu' : 1,
        'cpu_time' : 100}
        return dic   

    def get_num_instances(self):
        """Return the total number of virtual machines.

        Return the number of virtual machines that the hypervisor knows
        about.

        .. note::

            This implementation works for all drivers, but it is
            not particularly efficient. Maintainers of the virt drivers are
            encouraged to override this method with something more
            efficient.
        """
        return len(self.list_instances())

    def instance_exists(self, instance_id):
        """Checks existence of an instance on the host.

        :param instance_id: The ID / name of the instance to lookup

        Returns True if an instance with the supplied ID exists on
        the host, False otherwise.

        .. note::

            This implementation works for all drivers, but it is
            not particularly efficient. Maintainers of the virt drivers are
            encouraged to override this method with something more
            efficient.
        """
        return instance_id in self.list_instances()

    def list_instances(self):
        """
        Return the names of all the instances known to the virtualization
        layer, as a list.
        """
        #Returns all of the Amazon EC2 Instance Id's
        # TODO(Vek): Need to pass context in for access to auth_token        
        reservations = self.conn.get_all_instances()
        instances = [i for r in reservations for i in r.instances]
        instDict = [instance.id for instance in instances]
        return instDict

    def list_instance_uuids(self):
        """
        Return the UUIDS of all the instances known to the virtualization
        layer, as a list.
        """
        reservations = self.conn.get_all_instances()
        instances = [i for r in reservations for i in r.instances]
        instDict = [instance.id for instance in instances]
        return instDict

    def spawn(self, context, instance, image_meta, injected_files,
              admin_password, network_info=None, block_device_info=None):
        """
        Create a new instance/VM/domain on the virtualization platform.

        Once this successfully completes, the instance should be
        running (power_state.RUNNING).

        If this fails, any partial instance should be completely
        cleaned up, and the virtualization platform should be in the state
        that it was before this call began.

        :param context: security context
        :param instance: Instance object as returned by DB layer.
                         This function should use the data there to guide
                         the creation of the new instance.
        :param image_meta: image object returned by nova.image.glance that
                           defines the image from which to boot this instance
        :param injected_files: User files to inject into instance.
        :param admin_password: Administrator password to set in instance.
        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param block_device_info: Information about block devices to be
                                  attached to the instance.
        """
        import ipdb; ipdb.set_trace()
        nw_info = json.loads(network_info.json())
	elastic_ip = self.conn.allocate_address(domain='vpc')
        private_address = nw_info[0]['network']['subnets'][0]['ips'][0]['address']
        reservation = self.conn.run_instances('ami-3dadcf54',key_name='sirus',instance_type='t1.micro',security_group_ids=['sg-a4c105cb'],private_ip_address=private_address,subnet_id='subnet-1de45b71',user_data = base64.b64decode(instance['user_data']))
        public_instance = reservation.instances[0]        
        while(public_instance.update()!='running'):
            time.sleep(10)
	
	if public_instance.update() == 'running':
	    public_instance.add_tag("uuid",instance['uuid'])

	self.conn.associate_address(instance_id = public_instance.id, allocation_id = elastic_ip.allocation_id)

        #nw_info = json.loads(network_info.json())
        #nw_info[0]['network']['subnets'][0]['ips'][0]['address'] = publicInstance.ip_address
        #nw_info=json.dumps(nw_info)
        #self.virtapi.instance_info_cache_update(context,instance,{'network_info':nw_info})
        #modified_nw_info = (json.loads(instance['info_cache']['network_info']))
        #modified_nw_info[0]['network']['subnets'][0]['ips'][0]['address'] = publicInstance.ip_address
        #modified_info_cache=instance['info_cache']
        #modified_info_cache['network_info']=unicode(json.dumps(modified_nw_info))
        #self.virtapi.instance_update(context,instance['uuid'],{'info_cache':modified_info_cache})
        #Assign instance details here.        

    def destroy(self, instance, network_info, block_device_info=None,
                destroy_disks=True):
        """Destroy (shutdown and delete) the specified instance.

        If the instance is not found (for example if networking failed), this
        function should still succeed.  It's probably a good idea to log a
        warning in that case.
        
        :param instance: Instance object as returned by DB layer.
        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param block_device_info: Information about block devices that should
                                  be detached from the instance.
        :param destroy_disks: Indicates if disks should be destroyed

        """
        import ipdb; ipdb.set_trace()
        try:
	    public_instance = self.get_public_instance(instance)
	    elastic_ip = self.conn.get_all_addresses(addresses = [public_instance.ip_address])[0]
	    if(self.conn.disassociate_address(association_id = elastic_ip.association_id)==True):
	        elastic_ip.delete()
	        self.conn.terminate_instances([public_instance.id])
        except AttributeError:
	    print 'Bug Fix Needed'

	# TODO(Vek): Need to pass context in for access to auth_token

    #Deprecated
    def get_public_instance_by_user_data(self,instance):
        reservations = self.conn.get_all_instances()
        instances = [i for r in reservations for i in r.instances]
        corresponding_public_instance = {}
        for public_instance in instances:
            public_uuid = public_instance.get_attribute('userData')['userData'];
            if public_uuid != None:
                if public_uuid.decode('base64')==instance['uuid']:
                    return public_instance

    def get_public_instance(self,instance):
	reservations = self.conn.get_all_instances()
	instances = [i for r in reservations for i in r.instances] 
	for public_instance in instances:
	    if public_instance.tags.has_key("uuid"):
		public_uuid = public_instance.tags["uuid"]
		if public_uuid == instance['uuid']:
		    return public_instance	

    def reboot(self, context, instance, network_info, reboot_type,
               block_device_info=None):
        """Reboot the specified instance.

        After this is called successfully, the instance's state
        goes back to power_state.RUNNING. The virtualization
        platform should ensure that the reboot action has completed
        successfully even in cases in which the underlying domain/vm
        is paused or halted/stopped.

        :param instance: Instance object as returned by DB layer.
        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param reboot_type: Either a HARD or SOFT reboot
        """
        public_instance = get_public_instance(instance)
        self.conn.reboot_instances(public_instance.id)

    def get_console_pool_info(self, console_type):
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def get_console_output(self, instance):
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def get_vnc_console(self, instance):
        # TODO(Vek): Need to pass context in for access to auth_token
        "EC2 doesn't support vnc_console just yet natively, or do they?"
        pass

    def get_spice_console(self, instance):
        # TODO(Vek): Need to pass context in for access to auth_token
        pass

    def get_diagnostics(self, instance):
        """Return data about VM diagnostics."""
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def get_all_bw_counters(self, instances):
        """Return bandwidth usage counters for each interface on each
           running VM"""
        raise NotImplementedError()

    def get_all_volume_usage(self, context, compute_host_bdms):
        """Return usage info for volumes attached to vms on
           a given host"""
        raise NotImplementedError()

    def get_host_ip_addr(self):
        """
        Retrieves the IP address of the dom0
        """
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def attach_volume(self, connection_info, instance, mountpoint):
        """Attach the disk to the instance at mountpoint using info."""
        raise NotImplementedError()

    def detach_volume(self, connection_info, instance, mountpoint):
        """Detach the disk attached to the instance."""
        raise NotImplementedError()

    def attach_interface(self, instance, image_meta, network_info):
        """Attach an interface to the instance."""
        raise NotImplementedError()

    def detach_interface(self, instance, network_info):
        """Detach an interface from the instance."""
        raise NotImplementedError()

    def migrate_disk_and_power_off(self, context, instance, dest,
                                   instance_type, network_info,
                                   block_device_info=None):
        """
        Transfers the disk of a running instance in multiple phases, turning
        off the instance before the end.
        """
        raise NotImplementedError()

    def snapshot(self, context, instance, image_id, update_task_state):
        """
        Snapshots the specified instance.

        :param context: security context
        :param instance: Instance object as returned by DB layer.
        :param image_id: Reference to a pre-created image that will
                         hold the snapshot.
        """
        raise NotImplementedError()

    def finish_migration(self, context, migration, instance, disk_info,
                         network_info, image_meta, resize_instance,
                         block_device_info=None):
        """Completes a resize, turning on the migrated instance

        :param network_info:
           :py:meth:`~nova.network.manager.NetworkManager.get_instance_nw_info`
        :param image_meta: image object returned by nova.image.glance that
                           defines the image from which this instance
                           was created
        """
        raise NotImplementedError()

    def confirm_migration(self, migration, instance, network_info):
        """Confirms a resize, destroying the source VM."""
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def finish_revert_migration(self, instance, network_info,
                                block_device_info=None):
        """Finish reverting a resize, powering back on the instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def pause(self, instance):
        """Pause the specified instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        public_instance = self.get_public_instance(instance)
        self.conn.stop_instances(public_instance.id)

    def unpause(self, instance):
        """Unpause paused VM instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        public_instance = self.get_public_instance(instance)
        self.conn.start_instance(public_instance.id)

    def suspend(self, instance):
        """suspend the specified instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        # TODO(sirus): Find out difference between pause and suspend, snapshot and terminate maybe?
        raise NotImplementedError()

    def resume(self, instance, network_info, block_device_info=None):
        """resume the specified instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        # TODO(sirus): Find out difference between resume and unpause?
        raise NotImplementedError()

    def resume_state_on_host_boot(self, context, instance, network_info,
                                  block_device_info=None):
        """resume guest state when a host is booted."""
        raise NotImplementedError()

    def rescue(self, context, instance, network_info, image_meta,
               rescue_password):
        """Rescue the specified instance."""
        raise NotImplementedError()

    def unrescue(self, instance, network_info):
        """Unrescue the specified instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def power_off(self, instance):
        """Power off the specified instance."""
        raise NotImplementedError()

    def power_on(self, instance):
        """Power on the specified instance."""
        raise NotImplementedError()

    def soft_delete(self, instance):
        """Soft delete the specified instance."""
        raise NotImplementedError()

    def restore(self, instance):
        """Restore the specified instance."""
        raise NotImplementedError()

    def get_available_resource(self, nodename):
        """Retrieve resource information.

        This method is called when nova-compute launches, and
        as part of a periodic task

        :param nodename:
            node which the caller want to get resources from
            a driver that manages only one node can safely ignore this
        :returns: Dictionary describing resources
        """        
        dic = {'vcpus': 5000,
               'memory_mb': 100000,
               'local_gb': 100000,
               'vcpus_used': 0,
               'memory_mb_used': 0,
               'local_gb_used': 0,
               'hypervisor_type': 'EC2',
               'hypervisor_version': '0.1',
               'hypervisor_hostname': nodename,
               'cpu_info': 'LOL'}

        return dic

    def pre_live_migration(self, ctxt, instance_ref,
                           block_device_info, network_info,
                           migrate_data=None):
        """Prepare an instance for live migration

        :param ctxt: security context
        :param instance_ref: instance object that will be migrated
        :param block_device_info: instance block device information
        :param network_info: instance network information
        :param migrate_data: implementation specific data dict.
        """
        raise NotImplementedError()

    def pre_block_migration(self, ctxt, instance_ref, disk_info):
        """Prepare a block device for migration

        :param ctxt: security context
        :param instance_ref: instance object that will have its disk migrated
        :param disk_info: information about disk to be migrated (as returned
                          from get_instance_disk_info())
        """
        raise NotImplementedError()

    def live_migration(self, ctxt, instance_ref, dest,
                       post_method, recover_method, block_migration=False,
                       migrate_data=None):
        """Live migration of an instance to another host.

        :params ctxt: security context
        :params instance_ref:
            nova.db.sqlalchemy.models.Instance object
            instance object that is migrated.
        :params dest: destination host
        :params post_method:
            post operation method.
            expected nova.compute.manager.post_live_migration.
        :params recover_method:
            recovery method when any exception occurs.
            expected nova.compute.manager.recover_live_migration.
        :params block_migration: if true, migrate VM disk.
        :params migrate_data: implementation specific params.

        """
        raise NotImplementedError()

    def post_live_migration_at_destination(self, ctxt, instance_ref,
                                           network_info,
                                           block_migration=False,
                                           block_device_info=None):
        """Post operation of live migration at destination host.

        :param ctxt: security context
        :param instance_ref: instance object that is migrated
        :param network_info: instance network information
        :param block_migration: if true, post operation of block_migration.
        """
        raise NotImplementedError()

    def check_can_live_migrate_destination(self, ctxt, instance_ref,
                                           src_compute_info, dst_compute_info,
                                           block_migration=False,
                                           disk_over_commit=False):
        """Check if it is possible to execute live migration.

        This runs checks on the destination host, and then calls
        back to the source host to check the results.

        :param ctxt: security context
        :param instance_ref: nova.db.sqlalchemy.models.Instance
        :param src_compute_info: Info about the sending machine
        :param dst_compute_info: Info about the receiving machine
        :param block_migration: if true, prepare for block migration
        :param disk_over_commit: if true, allow disk over commit
        """
        raise NotImplementedError()

    def check_can_live_migrate_destination_cleanup(self, ctxt,
                                                   dest_check_data):
        """Do required cleanup on dest host after check_can_live_migrate calls

        :param ctxt: security context
        :param dest_check_data: result of check_can_live_migrate_destination
        """
        raise NotImplementedError()

    def check_can_live_migrate_source(self, ctxt, instance_ref,
                                      dest_check_data):
        """Check if it is possible to execute live migration.

        This checks if the live migration can succeed, based on the
        results from check_can_live_migrate_destination.

        :param context: security context
        :param instance_ref: nova.db.sqlalchemy.models.Instance
        :param dest_check_data: result of check_can_live_migrate_destination
        """
        raise NotImplementedError()

    def refresh_security_group_rules(self, security_group_id):
        """This method is called after a change to security groups.

        All security groups and their associated rules live in the datastore,
        and calling this method should apply the updated rules to instances
        running the specified security group.

        An error should be raised if the operation cannot complete.

        """
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def refresh_security_group_members(self, security_group_id):
        """This method is called when a security group is added to an instance.

        This message is sent to the virtualization drivers on hosts that are
        running an instance that belongs to a security group that has a rule
        that references the security group identified by `security_group_id`.
        It is the responsibility of this method to make sure any rules
        that authorize traffic flow with members of the security group are
        updated and any new members can communicate, and any removed members
        cannot.

        Scenario:
            * we are running on host 'H0' and we have an instance 'i-0'.
            * instance 'i-0' is a member of security group 'speaks-b'
            * group 'speaks-b' has an ingress rule that authorizes group 'b'
            * another host 'H1' runs an instance 'i-1'
            * instance 'i-1' is a member of security group 'b'

            When 'i-1' launches or terminates we will receive the message
            to update members of group 'b', at which time we will make
            any changes needed to the rules for instance 'i-0' to allow
            or deny traffic coming from 'i-1', depending on if it is being
            added or removed from the group.

        In this scenario, 'i-1' could just as easily have been running on our
        host 'H0' and this method would still have been called.  The point was
        that this method isn't called on the host where instances of that
        group are running (as is the case with
        :py:meth:`refresh_security_group_rules`) but is called where references
        are made to authorizing those instances.

        An error should be raised if the operation cannot complete.

        """
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def refresh_provider_fw_rules(self):
        """This triggers a firewall update based on database changes.

        When this is called, rules have either been added or removed from the
        datastore.  You can retrieve rules with
        :py:meth:`nova.db.provider_fw_rule_get_all`.

        Provider rules take precedence over security group rules.  If an IP
        would be allowed by a security group ingress rule, but blocked by
        a provider rule, then packets from the IP are dropped.  This includes
        intra-project traffic in the case of the allow_project_net_traffic
        flag for the libvirt-derived classes.

        """
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def reset_network(self, instance):
        """reset networking for specified instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        pass

    def ensure_filtering_rules_for_instance(self, instance_ref, network_info):
        """Setting up filtering rules and waiting for its completion.

        To migrate an instance, filtering rules to hypervisors
        and firewalls are inevitable on destination host.
        ( Waiting only for filtering rules to hypervisor,
        since filtering rules to firewall rules can be set faster).

        Concretely, the below method must be called.
        - setup_basic_filtering (for nova-basic, etc.)
        - prepare_instance_filter(for nova-instance-instance-xxx, etc.)

        to_xml may have to be called since it defines PROJNET, PROJMASK.
        but libvirt migrates those value through migrateToURI(),
        so , no need to be called.

        Don't use thread for this method since migration should
        not be started when setting-up filtering rules operations
        are not completed.

        :params instance_ref: nova.db.sqlalchemy.models.Instance object

        """
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def filter_defer_apply_on(self):
        """Defer application of IPTables rules."""
        pass

    def filter_defer_apply_off(self):
        """Turn off deferral of IPTables rules and apply the rules now."""
        pass

    def unfilter_instance(self, instance, network_info):
        """Stop filtering instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def set_admin_password(self, context, instance_id, new_pass=None):
        """
        Set the root password on the specified instance.

        The first parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name. The second
        parameter is the value of the new password.
        """
        raise NotImplementedError()

    def inject_file(self, instance, b64_path, b64_contents):
        """
        Writes a file on the specified instance.

        The first parameter is an instance of nova.compute.service.Instance,
        and so the instance is being specified as instance.name. The second
        parameter is the base64-encoded path to which the file is to be
        written on the instance; the third is the contents of the file, also
        base64-encoded.
        """
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def change_instance_metadata(self, context, instance, diff):
        """
        Applies a diff to the instance metadata.

        This is an optional driver method which is used to publish
        changes to the instance's metadata to the hypervisor.  If the
        hypervisor has no means of publishing the instance metadata to
        the instance, then this method should not be implemented.
        """
        pass

    def inject_network_info(self, instance, nw_info):
        """inject network info for specified instance."""
        # TODO(Vek): Need to pass context in for access to auth_token
        pass

    def poll_rebooting_instances(self, timeout, instances):
        """Poll for rebooting instances

        :param timeout: the currently configured timeout for considering
                        rebooting instances to be stuck
        :param instances: instances that have been in rebooting state
                          longer than the configured timeout
        """
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def host_power_action(self, host, action):
        """Reboots, shuts down or powers up the host."""
        raise NotImplementedError()

    def host_maintenance_mode(self, host, mode):
        """Start/Stop host maintenance window. On start, it triggers
        guest VMs evacuation."""
        raise NotImplementedError()

    def set_host_enabled(self, host, enabled):
        """Sets the specified host's ability to accept new instances."""
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def get_host_uptime(self, host):
        """Returns the result of calling "uptime" on the target host."""
        # TODO(Vek): Need to pass context in for access to auth_token
        raise NotImplementedError()

    def plug_vifs(self, instance, network_info):
        """Plug VIFs into networks."""
        # TODO(Vek): Need to pass context in for access to auth_token
        LOG.debug(_("plug_vifs called"), instance=instance)

    def unplug_vifs(self, instance, network_info):
        """Unplug VIFs from networks."""
        LOG.debug(_("unplug_vifs called"), instance=instance)

    def get_host_stats(self, refresh=False):
        dic = {'vcpus': 10,
               'memory_mb': 10000,
               'local_gb': 100,
               'vcpus_used': 0,
               'memory_mb_used': 400,
               'local_gb_used': 10,
               'hypervisor_type': 'EC2',
               'hypervisor_version': '0.1',
               'hypervisor_hostname': 'ip-10-0-0-10',
               'cpu_info': 'LOL'}

        return dic

    def block_stats(self, instance_name, disk_id):
        """
        Return performance counters associated with the given disk_id on the
        given instance_name.  These are returned as [rd_req, rd_bytes, wr_req,
        wr_bytes, errs], where rd indicates read, wr indicates write, req is
        the total number of I/O requests made, bytes is the total number of
        bytes transferred, and errs is the number of requests held up due to a
        full pipeline.

        All counters are long integers.

        This method is optional.  On some platforms (e.g. XenAPI) performance
        statistics can be retrieved directly in aggregate form, without Nova
        having to do the aggregation.  On those platforms, this method is
        unused.

        Note that this function takes an instance ID.
        """
        raise NotImplementedError()

    def interface_stats(self, instance_name, iface_id):
        """
        Return performance counters associated with the given iface_id on the
        given instance_id.  These are returned as [rx_bytes, rx_packets,
        rx_errs, rx_drop, tx_bytes, tx_packets, tx_errs, tx_drop], where rx
        indicates receive, tx indicates transmit, bytes and packets indicate
        the total number of bytes or packets transferred, and errs and dropped
        is the total number of packets failed / dropped.

        All counters are long integers.

        This method is optional.  On some platforms (e.g. XenAPI) performance
        statistics can be retrieved directly in aggregate form, without Nova
        having to do the aggregation.  On those platforms, this method is
        unused.

        Note that this function takes an instance ID.
        """
        raise NotImplementedError()

    def legacy_nwinfo(self):
        """True if the driver requires the legacy network_info format."""
        # TODO(tr3buchet): update all subclasses and remove this method and
        # related helpers.
        return False

    def macs_for_instance(self, instance):
        """What MAC addresses must this instance have?

        Some hypervisors (such as bare metal) cannot do freeform virtualisation
        of MAC addresses. This method allows drivers to return a set of MAC
        addresses that the instance is to have. allocate_for_instance will take
        this into consideration when provisioning networking for the instance.

        Mapping of MAC addresses to actual networks (or permitting them to be
        freeform) is up to the network implementation layer. For instance,
        with openflow switches, fixed MAC addresses can still be virtualised
        onto any L2 domain, with arbitrary VLANs etc, but regular switches
        require pre-configured MAC->network mappings that will match the
        actual configuration.

        Most hypervisors can use the default implementation which returns None.
        Hypervisors with MAC limits should return a set of MAC addresses, which
        will be supplied to the allocate_for_instance call by the compute
        manager, and it is up to that call to ensure that all assigned network
        details are compatible with the set of MAC addresses.

        This is called during spawn_instance by the compute manager.

        :return: None, or a set of MAC ids (e.g. set(['12:34:56:78:90:ab'])).
            None means 'no constraints', a set means 'these and only these
            MAC addresses'.
        """
        return None

    def manage_image_cache(self, context, all_instances):
        """
        Manage the driver's local image cache.

        Some drivers chose to cache images for instances on disk. This method
        is an opportunity to do management of that cache which isn't directly
        related to other calls into the driver. The prime example is to clean
        the cache and remove images wh_aich are no longer of interest.
        """
        pass

    def add_to_aggregate(self, context, aggregate, host, **kwargs):
        """Add a compute host to an aggregate."""
        #NOTE(jogo) Currently only used for XenAPI-Pool
        raise NotImplementedError()

    def remove_from_aggregate(self, context, aggregate, host, **kwargs):
        """Remove a compute host from an aggregate."""
        raise NotImplementedError()

    def undo_aggregate_operation(self, context, op, aggregate,
                                  host, set_error=True):
        """Undo for Resource Pools."""
        raise NotImplementedError()

    def get_volume_connector(self, instance):
        """Get connector information for the instance for attaching to volumes.

        Connector information is a dictionary representing the ip of the
        machine that will be making the connection, the name of the iscsi
        initiator and the hostname of the machine as follows::

            {
                'ip': ip,
                'initiator': initiator,
                'host': hostname
            }
        """
        raise NotImplementedError()

    def get_available_nodes(self):
        """Returns nodenames of all nodes managed by the compute service.

        This method is for multi compute-nodes support. If a driver supports
        multi compute-nodes, this method returns a list of nodenames managed
        by the service. Otherwise, this method should return
        [hypervisor_hostname].
        """
        stats = self.get_host_stats(refresh=True)
        if not isinstance(stats, list):
            stats = [stats]
        return [s['hypervisor_hostname'] for s in stats]

    def get_per_instance_usage(self):
        """Get information about instance resource usage.

        :returns: dict of  nova uuid => dict of usage info
        """
        return {}

    def instance_on_disk(self, instance):
        """Checks access of instance files on the host.

        :param instance: instance to lookup

        Returns True if files of an instance with the supplied ID accessible on
        the host, False otherwise.

        .. note::
            Used in rebuild for HA implementation and required for validation
            of access to instance shared disk files
        """
        return False

    def register_event_listener(self, callback):
        """Register a callback to receive events.

        Register a callback to receive asynchronous event
        notifications from hypervisors. The callback will
        be invoked with a single parameter, which will be
        an instance of the nova.virt.event.Event class."""

        self._compute_event_callback = callback

    def emit_event(self, event):
        """Dispatches an event to the compute manager.

        Invokes the event callback registered by the
        compute manager to dispatch the event. This
        must only be invoked from a green thread."""

        if not self._compute_event_callback:
            LOG.debug("Discarding event %s" % str(event))
            return

        if not isinstance(event, virtevent.Event):
            raise ValueError(
                _("Event must be an instance of nova.virt.event.Event"))

        try:
            LOG.debug("Emitting event %s" % str(event))
            self._compute_event_callback(event)
        except Exception, ex:
            LOG.error(_("Exception dispatching event %(event)s: %(ex)s")
                        % locals())


def load_compute_driver(virtapi, compute_driver=None):
    """Load a compute driver module.

    Load the compute driver module specified by the compute_driver
    configuration option or, if supplied, the driver name supplied as an
    argument.

    Compute drivers constructors take a VirtAPI object as their first object
    and this must be supplied.

    :param virtapi: a VirtAPI instance
    :param compute_driver: a compute driver name to override the config opt
    :returns: a ComputeDriver instance
    """
    if not compute_driver:
        compute_driver = CONF.compute_driver

    if not compute_driver:
        LOG.error(_("Compute driver option required, but not specified"))
        sys.exit(1)

    LOG.info(_("Loading compute driver '%s'") % compute_driver)
    try:
        driver = importutils.import_object_ns('nova.virt',
                                              compute_driver,
                                              virtapi)
        return utils.check_isinstance(driver, ComputeDriver)
    except ImportError as e:
        LOG.error(_("Unable to load the virtualization driver: %s") % (e))
        sys.exit(1)


def compute_driver_matches(match):
    return CONF.compute_driver.endswith(match)

