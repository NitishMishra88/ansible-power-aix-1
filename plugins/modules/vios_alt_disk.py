#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright: (c) 2018- IBM, Inc
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

ANSIBLE_METADATA = {'metadata_version': '1.1',
                    'status': ['preview'],
                    'supported_by': 'community'}

DOCUMENTATION = r'''
---
author:
- AIX Development Team (@pbfinley1911)
module: vios_alt_disk
short_description: Create/Cleanup an alternate rootvg disk on a VIOS
description:
- Copy the rootvg to an alternate disk or cleanup an existing one.
version_added: '2.9'
requirements:
- AIX >= 7.1 TL3
- Python >= 2.7
options:
  action:
    description:
    - Specifies the operation to perform on the VIOS.
    - C(alt_disk_copy) to perform and alternate disk copy.
    - C(alt_disk_clean) to cleanup an existing alternate disk copy.
    type: str
    choices: [ alt_disk_copy, alt_disk_clean ]
    required: true
  targets:
    description:
    - Specifies the target disks.
    type: list
    elements: str
  disk_size_policy:
    description:
    - Specifies how to choose the alternate disk if not specified.
    - C(minimize) smallest disk that can be selected.
    - C(upper) first disk found bigger than the rootvg disk.
    - C(lower) disk size less than rootvg disk size but big enough to contain the used PPs.
    - C(nearest)
    type: str
    choices: [ minimize, upper, lower, nearest ]
    default: nearest
  force:
    description:
    - Forces removal of any existing alternate disk copy on target disks.
    - Stops any active rootvg mirroring during the alternate disk copy.
    type: bool
    default: no
notes:
  - C(alt_disk_copy) only backs up mounted file systems. Mount all file
    systems that you want to back up.
  - when no target is specified, copy is performed to only one alternate
    disk even if the rootvg contains multiple disks
'''

EXAMPLES = r'''
- name: Perform an alternate disk copy of the rootvg to hdisk1
  vios_alt_disk:
    action: alt_disk_copy
    targets: hdisk1

- name: Perform an alternate disk copy of the rootvg to the smallest disk that can be selected
  vios_alt_disk:
    action: alt_disk_copy
    disk_size_policy: minimize

- name: Perform a cleanup of any existing alternate disk copy
  vios_alt_disk:
    action: alt_disk_clean
'''

RETURN = r'''
msg:
    description: The execution message.
    returned: always
    type: str
    sample: 'VIOS alt disk operation completed successfully'
stdout:
    description: The standard output
    returned: always
    type: str
    sample: 'Bootlist is set to the boot disk: hdisk0 blv=hd5'
stderr:
    description: The standard error
    returned: always
    type: str
'''

import re

from ansible.module_utils.basic import AnsibleModule


def get_pvs(module):
    """
    Get the list of PVs on the VIOS.

    return: dictionary with PVs information
    """
    global results

    cmd = ['/usr/ios/cli/ioscli', 'lspv']
    ret, stdout, stderr = module.run_command(cmd)
    if ret != 0:
        results['stdout'] = stdout
        results['stderr'] = stderr
        results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
        return None

    # NAME             PVID                                 VG               STATUS
    # hdisk0           000018fa3b12f5cb                     rootvg           active
    pvs = {}
    for line in stdout.split('\n'):
        line = line.rstrip()
        match_key = re.match(r"^(hdisk\S+)\s+(\S+)\s+(\S+)\s*(\S*)", line)
        if match_key:
            pvs[match_key.group(1)] = {}
            pvs[match_key.group(1)]['pvid'] = match_key.group(2)
            pvs[match_key.group(1)]['vg'] = match_key.group(3)
            pvs[match_key.group(1)]['status'] = match_key.group(4)

    module.debug('List of PVs:')
    for key in pvs.keys():
        module.debug('    pvs[{0}]: {1}'.format(key, pvs[key]))

    return pvs


def get_free_pvs(module):
    """
    Get the list of free PVs on the VIOS.

    return: dictionary with free PVs information
    """
    global results

    cmd = ['/usr/ios/cli/ioscli', 'lspv', '-free']
    ret, stdout, stderr = module.run_command(cmd)
    if ret != 0:
        results['stdout'] = stdout
        results['stderr'] = stderr
        results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
        return None

    # NAME            PVID                                SIZE(megabytes)
    # hdiskX          none                                572325
    free_pvs = {}
    for line in stdout.split('\n'):
        line = line.rstrip()
        match_key = re.match(r"^(hdisk\S+)\s+(\S+)\s+(\S+)", line)
        if match_key:
            free_pvs[match_key.group(1)] = {}
            free_pvs[match_key.group(1)]['pvid'] = match_key.group(2)
            free_pvs[match_key.group(1)]['size'] = int(match_key.group(3))

    module.debug('List of available PVs:')
    for key in free_pvs.keys():
        module.debug('    free_pvs[{0}]: {1}'.format(key, free_pvs[key]))

    return free_pvs


def find_valid_altdisk(module, hdisks, rootvg_info, disk_size_policy, force):
    """
    Find a valid alternate disk that:
    - exists,
    - is not part of a VG
    - with a correct size
    and so can be used.
    """
    global results

    # check rootvg
    if rootvg_info['status'] != 0:
        results['msg'] = 'Wrong rootvg state'
        module.fail_json(**results)

    # get pv list
    pvs = get_pvs(module)
    if pvs is None:
        module.fail_json(**results)
    # check an alternate disk does not already exist
    found_altdisk = ''
    for pv in pvs:
        if pvs[pv]['vg'] == 'altinst_rootvg':
            found_altdisk = pv
            break
    if found_altdisk:
        if not force:
            results['msg'] = 'An alternate disk already exists on disk {0}'.format(found_altdisk)
            module.fail_json(**results)
        # Clean existing altinst_rootvg
        module.log('Removing altinst_rootvg')

        cmd = ['/usr/sbin/alt_rootvg_op', '-X', 'altinst_rootvg']
        ret, stdout, stderr = module.run_command(cmd)
        if ret != 0:
            results['stdout'] = stdout
            results['stderr'] = stderr
            results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
            module.fail_json(**results)

        results['changed'] = True

        for pv in pvs:
            if pvs[pv]['vg'] == 'altinst_rootvg':
                module.log('Clearing the owning VG from disk {0}'.format(pv))

                cmd = ['/usr/sbin/chpv', '-C', pv]
                ret, stdout, stderr = module.run_command(cmd)
                if ret != 0:
                    results['stdout'] = stdout
                    results['stderr'] = stderr
                    results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
                    module.fail_json(**results)

    pvs = get_free_pvs(module)
    if pvs is None:
        module.fail_json(**results)
    if not pvs:
        results['msg'] = 'No free disk available'
        module.fail_json(**results)

    used_size = rootvg_info["used_size"]
    rootvg_size = rootvg_info["rootvg_size"]
    # in auto mode, find the first alternate disk available
    if not hdisks:
        selected_disk = ""
        prev_disk = ""
        diffsize = 0
        prev_diffsize = 0
        # parse free disks in increasing size order
        for key in sorted(pvs, key=lambda k: pvs[k]['size']):
            hdisk = key

            # disk too small, skip
            if pvs[hdisk]['size'] < used_size:
                continue

            # smallest disk that can be selected
            if disk_size_policy == 'minimize':
                selected_disk = hdisk
                break

            diffsize = pvs[hdisk]['size'] - rootvg_size
            # matching disk size
            if diffsize == 0:
                selected_disk = hdisk
                break

            if diffsize > 0:
                # diffsize > 0: first disk found bigger than the rootvg disk
                if disk_size_policy == 'upper':
                    selected_disk = hdisk
                elif disk_size_policy == 'lower':
                    if not prev_disk:
                        # Best Can Do...
                        selected_disk = hdisk
                    else:
                        selected_disk = prev_disk
                else:
                    # disk_size_policy == 'nearest'
                    if prev_disk == "":
                        selected_disk = hdisk
                    elif abs(prev_diffsize) > diffsize:
                        selected_disk = hdisk
                    else:
                        selected_disk = prev_disk
                break
            # disk size less than rootvg disk size
            #   but big enough to contain the used PPs
            prev_disk = hdisk
            prev_diffsize = diffsize

        if not selected_disk:
            if prev_disk:
                # Best Can Do...
                selected_disk = prev_disk
            else:
                results['msg'] = 'No available alternate disk with size greater than {0} MB'\
                                 ' found'.format(rootvg_size)
                module.fail_json(**results)

        module.debug('Selected disk is {0} (select mode: {1})'
                     .format(selected_disk, disk_size_policy))
        hdisks.append(selected_disk)

    # hdisks specified by the user
    else:
        tot_size = 0
        for hdisk in hdisks:
            if hdisk not in pvs:
                results['msg'] = 'Alternate disk {0} is either not found or not available'\
                                 .format(hdisk)
                module.fail_json(**results)
            tot_size += pvs[hdisk]['size']

        # check the specified hdisks are large enough
        if tot_size < rootvg_size:
            if tot_size >= used_size:
                module.log('[WARNING] Alternate disks smaller than the current rootvg.')
            else:
                results['msg'] = 'Alternate disks too small ({0} < {1}).'\
                                 .format(tot_size, rootvg_size)
                module.fail_json(**results)


def check_rootvg(module):
    """
    Check the rootvg
    - check if the rootvg is mirrored
    - check stale partitions
    - calculate the total and used size of the rootvg

    return:
        Dictionary with following keys: value
            "status":
                0 the rootvg can be saved in an alternate disk copy
                1 otherwise (cannot unmirror then mirror again)
            "copy_dict":
                dictionary key, value
                    key: copy number (int)
                    value: hdiskX
                    example: {1: 'hdisk4', : 2: 'hdisk8', 3: 'hdisk9'}
            "rootvg_size": size in Megabytes (int)
            "used_size": size in Megabytes (int)
    """
    global results

    vg_info = {}
    copy_dict = {}
    vg_info["status"] = 1
    vg_info["copy_dict"] = copy_dict
    vg_info["rootvg_size"] = 0
    vg_info["used_size"] = 0

    nb_lp = 0
    copy = 0
    used_size = -1
    total_size = -1
    pp_size = -1
    pv_size = -1
    hdisk_dict = {}

    cmd = ['/usr/sbin/lsvg', '-M', 'rootvg']
    ret, stdout, stderr = module.run_command(cmd)
    if ret != 0:
        results['stdout'] = stdout
        results['stderr'] = stderr
        results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
        return None

    # lsvg -M rootvg command OK, check mirroring
    # hdisk4:453      hd1:101
    # hdisk4:454      hd1:102
    # hdisk4:257      hd10opt:1:1
    # hdisk4:258      hd10opt:2:1
    # hdisk4:512-639
    # hdisk8:255      hd1:99:2        stale
    # hdisk8:256      hd1:100:2       stale
    # hdisk8:257      hd10opt:1:2
    # hdisk8:258      hd10opt:2:2
    # ..
    # hdisk9:257      hd10opt:1:3
    # ..
    if stdout.find('stale') > 0:
        results['stdout'] = stdout
        results['stderr'] = stderr
        results['msg'] = 'rootvg contains stale partitions'
        return None

    hdisk = ''

    for line in stdout.split('\n'):
        line = line.rstrip()
        mirror_key = re.match(r"^(\S+):\d+\s+\S+:\d+:(\d+)$", line)
        if mirror_key:
            hdisk = mirror_key.group(1)
            copy = int(mirror_key.group(2))
        else:
            single_key = re.match(r"^(\S+):\d+\s+\S+:\d+$", line)
            if single_key:
                hdisk = single_key.group(1)
                copy = 1
            else:
                continue

        if copy == 1:
            nb_lp += 1

        if hdisk in hdisk_dict.keys():
            if hdisk_dict[hdisk] != copy:
                results['msg'] = "rootvg data structure is not compatible with an "\
                                 "alt_disk_copy operation (2 copies on the same disk)"
                return None
        else:
            hdisk_dict[hdisk] = copy

        if copy not in copy_dict.keys():
            if hdisk in copy_dict.values():
                results['msg'] = "rootvg data structure is not compatible with an alt_disk_copy operation"
                return None
            copy_dict[copy] = hdisk

    if len(copy_dict.keys()) > 1:
        if len(copy_dict.keys()) != len(hdisk_dict.keys()):
            results['msg'] = "The rootvg is partially or completely mirrored but some "\
                             "LP copies are spread on several disks. This prevents the "\
                             "system from creating an alternate rootvg disk copy."
            return None

        # the (rootvg) is mirrored then get the size of hdisk from copy1
        cmd = ['/usr/sbin/lsvg', '-p', 'rootvg']
        ret, stdout, stderr = module.run_command(cmd)
        if ret != 0:
            results['stdout'] = stdout
            results['stderr'] = stderr
            results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
            return None

        # parse lsvg outpout to get the size in megabytes:
        # rootvg:
        # PV_NAME           PV STATE          TOTAL PPs   FREE PPs    FREE DISTRIBUTION
        # hdisk4            active            639         254         126..00..00..00..128
        # hdisk8            active            639         254         126..00..00..00..128

        for line in stdout.split('\n'):
            line = line.rstrip()
            match_key = re.match(r"^(\S+)\s+\S+\s+(\d+)\s+\d+\s+\S+", line)
            if match_key:
                pv_size = int(match_key.group(2))
                if match_key.group(1) == copy_dict[1]:
                    break
                continue

        if pv_size == -1:
            results['msg'] = 'Failed to get pv size, parsing error'
            return None

    # now get the rootvg pp size
    cmd = ['/usr/sbin/lsvg', 'rootvg']
    ret, stdout, stderr = module.run_command(cmd)
    if ret != 0:
        results['stdout'] = stdout
        results['stderr'] = stderr
        results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
        return None

    # parse lsvg outpout to get the size in megabytes:
    # VG PERMISSION:      read/write               TOTAL PPs:      558 (285696 megabytes)
    for line in stdout.split('\n'):
        line = line.rstrip()
        match_key = re.match(r".*TOTAL PPs:\s+\d+\s+\((\d+)\s+megabytes\).*", line)
        if match_key:
            total_size = int(match_key.group(1))
            continue

        match_key = re.match(r".*PP SIZE:\s+(\d+)\s+megabyte\(s\)", line)
        if match_key:
            pp_size = int(match_key.group(1))
            continue

    if pp_size == -1:
        results['msg'] = 'Failed to get rootvg pp size, parsing error'
        return None

    if len(copy_dict.keys()) > 1:
        total_size = pp_size * pv_size

    used_size = pp_size * (nb_lp + 1)

    vg_info["status"] = 0
    vg_info["copy_dict"] = copy_dict
    vg_info["rootvg_size"] = total_size
    vg_info["used_size"] = used_size
    return vg_info


def alt_disk_copy(module, hdisks, disk_size_policy, force):
    """
    alt_disk_copy operation

    - check the rootvg, find and valid the hdisks for the operation
    - unmirror rootvg if necessary
    - perform the alt disk copy or cleanup operation
    - wait for the copy to finish
    - mirror rootvg if necessary
    """
    global results

    rootvg_info = check_rootvg(module)
    if rootvg_info is None:
        module.fail_json(**results)

    if hdisks is None:
        hdisks = []
    find_valid_altdisk(module, hdisks, rootvg_info, disk_size_policy, force)

    module.log('Using {0} as alternate disks'.format(hdisks))

    # unmirror the vg if necessary
    # check mirror

    copies_h = rootvg_info["copy_dict"]
    nb_copies = len(copies_h.keys())

    if nb_copies > 1:
        if not force:
            results['msg'] = 'The rootvg is mirrored and force option is not set'
            module.fail_json(**results)

        module.log('[WARNING] Stopping mirror')

        cmd = ['/usr/sbin/unmirrorvg', 'rootvg']
        ret, stdout, stderr = module.run_command(cmd)
        if ret != 0:
            # unmirror command failed
            results['stdout'] = stdout
            results['stderr'] = stderr
            results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
            module.fail_json(**results)
        if stderr.find('rootvg successfully unmirrored') == -1:
            # unmirror command failed
            results['stdout'] = stdout
            results['stderr'] = stderr
            results['msg'] = 'Failed to unmirror rootvg'
            module.fail_json(**results)
        # unmirror command OK
        module.info('Unmirror rootvg successful')

    # alt_disk_copy
    cmd = ['alt_disk_copy', '-B', '-d', ' '.join(hdisks)]

    ret_altdc, stdout_altdc, stderr_altdc = module.run_command(cmd)
    if ret_altdc == 0:
        results['changed'] = True

    # restore the mirroring if necessary
    if nb_copies > 1:
        module.log('Restoring mirror')

        cmd = ['/usr/sbin/mirrorvg', '-m', '-c', nb_copies, 'rootvg', copies_h[2]]
        if nb_copies > 2:
            cmd += [copies_h[3]]

        ret, stdout, stderr = module.run_command(cmd)
        if ret != 0:
            # mirror command failed
            results['stdout'] = stdout
            results['stderr'] = stderr
            results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
            module.fail_json(**results)
        if stderr.find('Failed to mirror the volume group') != -1:
            # mirror command failed
            results['stdout'] = stdout
            results['stderr'] = stderr
            results['msg'] = 'Failed to mirror rootvg'
            module.fail_json(**results)

    results['stdout'] = stdout_altdc
    results['stderr'] = stderr_altdc
    if ret_altdc != 0:
        # an error occured during alt_disk_copy
        results['msg'] = 'Failed to copy {0}: return code {1}.'.format(' '.join(hdisks), ret_altdc)
        module.fail_json(**results)


def alt_disk_clean(module, hdisks):
    """
    alt_disk_clean operation

    - cleanup alternate disk volume group (alt_rootvg_op -X)
    - clear the owning volume manager from each disk (chpv -C)
    """
    global results

    pvs = get_pvs(module)
    if pvs is None:
        module.fail_json(**results)

    if hdisks:
        # Check that all specified disks exist and belong to altinst_rootvg
        for hdisk in hdisks:
            if (hdisk not in pvs) or (pvs[hdisk]['vg'] != 'altinst_rootvg'):
                results['msg'] = 'Specified disk {0} is not an alternate install rootvg'\
                                 .format(hdisk)
                module.fail_json(**results)
    else:
        # Retrieve the list of disks that belong to altinst_rootvg
        hdisks = []
        for pv in pvs.keys():
            if pvs[pv]['vg'] == 'altinst_rootvg':
                hdisks.append(pv)
        if not hdisks:
            results['msg'] = 'There is no alternate install rootvg'
            module.fail_json(**results)

    # First remove the alternate VG
    module.log('Removing altinst_rootvg')

    cmd = ['/usr/sbin/alt_rootvg_op', '-X', 'altinst_rootvg']
    ret, stdout, stderr = module.run_command(cmd)

    results['stdout'] = stdout
    results['stderr'] = stderr
    if ret != 0:
        results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
        module.fail_json(**results)

    # Clears the owning VG from the disks
    for hdisk in hdisks:
        module.log('Clearing the owning VG from disk {0}'.format(hdisk))

        cmd = ['/usr/sbin/chpv', '-C', hdisk]
        ret, stdout, stderr = module.run_command(cmd)
        if ret != 0:
            results['stdout'] = stdout
            results['stderr'] = stderr
            results['msg'] = 'Command \'{0}\' failed with return code {1}.'.format(' '.join(cmd), ret)
            module.fail_json(**results)

    results['changed'] = True


def main():
    global results

    module = AnsibleModule(
        argument_spec=dict(
            action=dict(required=True, type='str',
                        choices=['alt_disk_copy', 'alt_disk_clean']),
            targets=dict(type='list', elements='str'),
            disk_size_policy=dict(type='str',
                                  choices=['minimize', 'upper', 'lower', 'nearest'],
                                  default='nearest'),
            force=dict(type='bool', default=False),
        )
    )

    results = dict(
        changed=False,
        msg='',
        stdout='',
        stderr='',
    )

    action = module.params['action']
    targets = module.params['targets']
    disk_size_policy = module.params['disk_size_policy']
    force = module.params['force']

    if action == 'alt_disk_copy':
        alt_disk_copy(module, targets, disk_size_policy, force)
    else:
        alt_disk_clean(module, targets)

    results['msg'] = 'VIOS alt disk operation completed successfully'
    module.exit_json(**results)


if __name__ == '__main__':
    main()