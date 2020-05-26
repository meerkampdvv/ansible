#
# Copyright 2018 www.privaz.io Valletech AB
# Simplified BSD License (see licenses/simplified_bsd.txt or https://opensource.org/licenses/BSD-2-Clause)


import time
import ssl
from os import environ, path
from ansible.module_utils.six import string_types
from ansible.module_utils.basic import AnsibleModule


HAS_PYONE = True

try:
    from pyone import OneException
    from pyone.server import OneServer
except ImportError:
    OneException = Exception
    HAS_PYONE = False


class OpenNebulaModule:
    """
    Base class for all OpenNebula Ansible Modules.
    This is basically a wrapper of the common arguments, the pyone client and
    some utility methods.
    """

    common_args = dict(
        api_url=dict(type='str', aliases=['api_endpoint'], default=environ.get("ONE_URL")),
        api_username=dict(type='str', default=environ.get("ONE_USERNAME")),
        api_password=dict(type='str', no_log=True, aliases=['api_token'], default=environ.get("ONE_PASSWORD")),
        api_auth_file=dict(type='str', default=environ.get("ONE_AUTH")),
        validate_certs=dict(default=True, type='bool'),
        wait_timeout=dict(type='int', default=300),
    )

    def __init__(self, argument_spec, supports_check_mode=False, mutually_exclusive=None):

        module_args = OpenNebulaModule.common_args
        module_args.update(argument_spec)

        self.module = AnsibleModule(argument_spec=module_args,
                                    supports_check_mode=supports_check_mode,
                                    mutually_exclusive=mutually_exclusive)
        self.result = dict(changed=False,
                           original_message='',
                           message='')
        self.one = self.create_one_client()

        self.resolved_parameters = self.resolve_parameters()

    def create_one_client(self):
        """
        Creates an XMLPRC client to OpenNebula.

        Returns: the new xmlrpc client.

        """

        # context required for not validating SSL, old python versions won't validate anyway.
        if hasattr(ssl, '_create_unverified_context'):
            no_ssl_validation_context = ssl._create_unverified_context()
        else:
            no_ssl_validation_context = None

        # Check if the module can run
        if not HAS_PYONE:
            self.fail("pyone is required for this module")

        if self.module.params.get("api_url"):
            url = self.module.params.get("api_url")
        else:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            url = 'http://localhost:2633/RPC2'
            try:
                self.module.warn("api_url or the environment variable ONE_URL was not provided, trying default '%s'" % url)
                s.connect(('localhost', 2633))
                s.shutdown(2)
            except ConnectionError:
                self.fail(msg=("api_url or the environment variable ONE_URL was not provided, default '%s' also unavailable" % url))

        if self.module.params.get("api_username") and self.module.params.get("api_password"):
            username = self.module.params.get("api_username")
            password = self.module.params.get("api_password")
        else:
            if self.module.params.get("api_auth_file"):
                authfile = self.module.params.get("api_auth_file")
            else:
                authfile = path.join(environ.get("HOME"), ".one", "one_auth")
            try:
                authstring = open(authfile, "r").read().rstrip()
                username = authstring.split(":")[0]
                password = authstring.split(":")[1]
            except (OSError, IOError):
                self.fail(msg=("No Credentials provided and could not find or read ONE_AUTH file at '%s'" % authfile))
            except Exception:
                self.fail(msg=("Error occurs when read ONE_AUTH file at '%s'" % authfile))

        session = "%s:%s" % (username, password)

        if not self.module.params.get("validate_certs") and "PYTHONHTTPSVERIFY" not in environ:
            return OneServer(url, session=session, context=no_ssl_validation_context)
        else:
            return OneServer(url, session)

    def close_one_client(self):
        """
        Close the pyone session.
        """
        self.one.server_close()

    def fail(self, msg):
        """
        Utility failure method, will ensure pyone is properly closed before failing.
        Args:
            msg: human readable failure reason.
        """
        if hasattr(self, 'one'):
            self.close_one_client()
        self.module.fail_json(msg=msg)

    def exit(self):
        """
        Utility exit method, will ensure pyone is properly closed before exiting.

        """
        if hasattr(self, 'one'):
            self.close_one_client()
        self.module.exit_json(**self.result)

    def resolve_parameters(self):
        """
        This method resolves parameters provided by a secondary ID to the primary ID.
        For example if cluster_name is present, cluster_id will be introduced by performing
        the required resolution

        Returns: a copy of the parameters that includes the resolved parameters.

        """

        resolved_params = dict(self.module.params)

        if 'cluster_name' in self.module.params:
            clusters = self.one.clusterpool.info()
            for cluster in clusters.CLUSTER:
                if cluster.NAME == self.module.params.get('cluster_name'):
                    resolved_params['cluster_id'] = cluster.ID

        return resolved_params

    def is_parameter(self, name):
        """
        Utility method to check if a parameter was provided or is resolved
        Args:
            name: the parameter to check
        """
        if name in self.resolved_parameters:
            return self.get_parameter(name) is not None
        else:
            return False

    def get_parameter(self, name):
        """
        Utility method for accessing parameters that includes resolved ID
        parameters from provided Name parameters.
        """
        return self.resolved_parameters.get(name)

    def get_host_by_name(self, name):
        '''
        Returns a host given its name.
        Args:
            name: the name of the host

        Returns: the host object or None if the host is absent.

        '''
        hosts = self.one.hostpool.info()
        for h in hosts.HOST:
            if h.NAME == name:
                return h
        return None

    def get_cluster_by_name(self, name):
        """
        Returns a cluster given its name.
        Args:
            name: the name of the cluster

        Returns: the cluster object or None if the host is absent.
        """

        clusters = self.one.clusterpool.info()
        for c in clusters.CLUSTER:
            if c.NAME == name:
                return c
        return None

    def get_template(self, predicate):
        pool = self.one.templatepool.info(-2, -1, -1, -1)
        # Filter -2 means fetch all templates user can Use
        found = 0
        found_template = None
        template_name = ''

        for template in pool.VMTEMPLATE:
            if predicate(template):
                found = found + 1
                found_template = template
                template_name = template.NAME

        if found == 0:
            return None
        elif found > 1:
            self.module.fail_json(msg='There are more templates with name: ' + template_name)
        return found_template

    def get_template_by_name(self, template_name):
        return self.get_template(lambda template: (template.NAME == template_name))


    def get_template_by_id(self, template_id):
        return self.get_template(lambda template: (template.ID == template_id))


    def get_template_id(self, requested_id, requested_name):
        template = self.get_template_by_id(requested_id) if requested_id is not None else self.get_template_by_name(requested_name)
        if template:
            return template.ID
        else:
            return None

    def get_datastore(self, predicate):
        pool = self.one.datastorepool.info()
        found = 0
        found_datastore = None
        datastore_name = ''

        for datastore in pool.DATASTORE:
            if predicate(datastore):
                found = found + 1
                found_datastore = datastore
                datastore_name = datastore.NAME

        if found == 0:
            return None
        elif found > 1:
            self.module.fail_json(msg='There are more datastores with name: ' + datastore_name)
        return found_datastore

    def get_datastore_by_name(self, datastore_name):
        return self.get_datastore(lambda datastore: (datastore.NAME == datastore_name))


    def get_datastore_by_id(self, datastore_id):
        return self.get_datastore(lambda datastore: (datastore.ID == datastore_id))


    def get_datastore_id(self, requested_id, requested_name):
        datastore = self.get_datastore_by_id(requested_id) if requested_id else self.get_datastore_by_name(requested_name)
        if datastore:
            return datastore.ID
        else:
            return None

    def cast_template(self, template):
        """
        OpenNebula handles all template elements as strings
        At some point there is a cast being performed on types provided by the user
        This function mimics that transformation so that required template updates are detected properly
        additionally an array will be converted to a comma separated list,
        which works for labels and hopefully for something more.

        Args:
            template: the template to transform

        Returns: the transformed template with data casts applied.
        """

        # TODO: check formally available data types in templates
        # TODO: some arrays might be converted to space separated

        for key in template:
            value = template[key]
            if isinstance(value, dict):
                self.cast_template(template[key])
            elif isinstance(value, list):
                template[key] = ', '.join(value)
            elif not isinstance(value, string_types):
                template[key] = str(value)

    def requires_template_update(self, current, desired):
        """
        This function will help decide if a template update is required or not
        If a desired key is missing from the current dictionary an update is required
        If the intersection of both dictionaries is not deep equal, an update is required
        Args:
            current: current template as a dictionary
            desired: desired template as a dictionary

        Returns: True if a template update is required
        """

        if not desired:
            return False

        self.cast_template(desired)
        intersection = dict()
        for dkey in desired.keys():
            if dkey in current.keys():
                intersection[dkey] = current[dkey]
            else:
                return True
        return not (desired == intersection)

    def wait_for_state(self, element_name, state, state_name, target_states,
                       invalid_states=None, transition_states=None,
                       wait_timeout=None):
        """
        Args:
            element_name: the name of the object we are waiting for: HOST, VM, etc.
            state: lambda that returns the current state, will be queried until target state is reached
            state_name: lambda that returns the readable form of a given state
            target_states: states expected to be reached
            invalid_states: if any of this states is reached, fail
            transition_states: when used, these are the valid states during the transition.
            wait_timeout: timeout period in seconds. Defaults to the provided parameter.
        """

        if not wait_timeout:
            wait_timeout = self.module.params.get("wait_timeout")

        start_time = time.time()

        while (time.time() - start_time) < wait_timeout:
            current_state = state()

            if current_state in invalid_states:
                self.fail('invalid %s state %s' % (element_name, state_name(current_state)))

            if transition_states:
                if current_state not in transition_states:
                    self.fail('invalid %s transition state %s' % (element_name, state_name(current_state)))

            if current_state in target_states:
                return True

            time.sleep(self.one.server_retry_interval())

        self.fail(msg="Wait timeout has expired!")

    def get_all_vms(self):
        pool = self.one.vmpool.info(-2, -1, -1, -1)
        # Filter -2 means fetch all vms user has

        return pool

    def parse_vm_permissions(self, vm):
        vm_PERMISSIONS = self.one.vm.info(vm.ID).PERMISSIONS

        owner_octal = int(vm_PERMISSIONS.OWNER_U) * 4 + int(vm_PERMISSIONS.OWNER_M) * 2 + int(vm_PERMISSIONS.OWNER_A)
        group_octal = int(vm_PERMISSIONS.GROUP_U) * 4 + int(vm_PERMISSIONS.GROUP_M) * 2 + int(vm_PERMISSIONS.GROUP_A)
        other_octal = int(vm_PERMISSIONS.OTHER_U) * 4 + int(vm_PERMISSIONS.OTHER_M) * 2 + int(vm_PERMISSIONS.OTHER_A)

        permissions = str(owner_octal) + str(group_octal) + str(other_octal)

        return permissions

    def set_vm_permissions(self, vms, permissions):
        changed = False

        for vm in vms:
            vm = self.one.vm.info(vm.ID)
            old_permissions = self.parse_vm_permissions(vm)
            changed = changed or old_permissions != permissions

            if not module.check_mode and old_permissions != permissions:
                permissions_str = bin(int(permissions, base=8))[2:]  # 600 -> 110000000
                mode_bits = [int(d) for d in permissions_str]
                try:
                    self.one.vm.chmod(
                        vm.ID, mode_bits[0], mode_bits[1], mode_bits[2], mode_bits[3], mode_bits[4], mode_bits[5], mode_bits[6], mode_bits[7], mode_bits[8])
                except pyone.OneAuthorizationException:
                    self.module.fail_json(msg="Permissions changing is unsuccessful, but instances are present if you deployed them.")

        return changed

    def set_vm_ownership(self, vms, owner_id, group_id):
        changed = False

        for vm in vms:
            vm = self.one.vm.info(vm.ID)
            if owner_id is None:
                owner_id = vm.UID
            if group_id is None:
                group_id = vm.GID

            changed = changed or owner_id != vm.UID or group_id != vm.GID

            if not self.module.check_mode and (owner_id != vm.UID or group_id != vm.GID):
                try:
                    self.one.vm.chown(vm.ID, owner_id, group_id)
                except pyone.OneAuthorizationException:
                    self.module.fail_json(msg="Ownership changing is unsuccessful, but instances are present if you deployed them.")

        return changed

    def get_all_users(self):
        pool = self.one.userpool.info()
        return pool

    def get_user_by_name(self, name):
        pool = self.get_all_users()
        for user in pool.USER:
            if name == user.NAME:
                return user.ID
        self.fail(msg="There is no User with name=" + name)

    def get_all_groups(self):
        pool = self.one.grouppool.info()
        return pool

    def get_group_by_name(self, name):
        pool = self.get_all_groups()
        for group in pool.GROUP:
            if name == group.NAME:
                return group.ID
        self.fail(msg="There is no Group with name=" + name)

    def get_vm_labels_and_attributes_dict(self, vm_id):
        vm_USER_TEMPLATE = self.one.vm.info(vm_id).USER_TEMPLATE

        attrs_dict = {}
        labels_list = []

        for key, value in vm_USER_TEMPLATE.items():
            if key != 'LABELS':
                attrs_dict[key] = value
            else:
                if key is not None:
                    labels_list = value.split(',')

        return labels_list, attrs_dict

    def get_vm_by_id(self, vm_id):
        try:
            vm = self.one.vm.info(int(vm_id))
        except BaseException:
            return None
        return vm

    def get_vms_by_ids(self, ids, state=None):
        vms = []
        pool = self.get_all_vms()

        for vm in pool.VM:
            if vm.ID in ids:
                vms.append(vm)
                ids.remove(vm.ID)
                if len(ids) == 0:
                    break

        if len(ids) > 0 and state != 'absent':
            self.fail(msg='There is no VM(s) with id(s)=' + ', '.join('{id}'.format(id=str(vm_id)) for vm_id in ids))

        return vms

    def get_vms_by_name(self, name_pattern):
        vms = []
        pattern = None

        pool = self.get_all_vms()

        if name_pattern.startswith('~'):
            import re
            if name_pattern[1] == '*':
                pattern = re.compile(name_pattern[2:], re.IGNORECASE)
            else:
                pattern = re.compile(name_pattern[1:])

        for vm in pool.VM:
            if pattern is not None:
                if pattern.match(vm.NAME):
                    vms.append(vm)
            elif name_pattern == vm.NAME:
                vms.append(vm)
                break

        if pattern is None and len(vms) == 0:
            self.fail(msg="There is no VM with name=" + name_pattern)

        return vms

    def run_module(self):
        """
        trigger the start of the execution of the module.
        Returns:

        """
        try:
            self.run(self.one, self.module, self.result)
        except OneException as e:
            self.fail(msg="OpenNebula Exception: %s" % e)

    def run(self, one, module, result):
        """
        to be implemented by subclass with the actual module actions.
        Args:
            one: the OpenNebula XMLRPC client
            module: the Ansible Module object
            result: the Ansible result
        """
        raise NotImplementedError("Method requires implementation")
