
# use parallel-ssh to deploy code/manage an instance group
#
# Setup:
#   1. install gcloud client, authenticate it, and set the default account to ai2-tpu
#   2. (Matt P: I don't think this is needed as shell ssh is used when agent forwarding
#       is needed, and I could never get forwarding to work with the client...
#         but keeping it here for now)
#       Install a new version of libssh2 (>= 1.9.0 with brew), then to install ssh2-python
#           conda install -c conda-forge libssh2
#           export HAVE_AGENT_FWD=1
#           pip install --no-cache-dir -v -v -v ssh2-python
#   3. Install pip install parallel-ssh  with `pip install pip install parallel-ssh`

import subprocess
import os


def get_hosts_in_group(group):
    """Use gcloud to get a list of IPs in the group.  will match every instance that includes `group` in the name.
    Returns list of IPs"""
    # bash equivalent: hosts=(`gcloud compute instances list | grep $GROUP | tr -s " " | cut -f 9 -d " "`)
    cmd = 'gcloud compute instances list | grep {} | tr -s " " | cut -f 5 -d " "'.format(group)
    completed = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
    ips = completed.stdout.decode('utf-8').strip().split()
    return ips



class DistributedManager:
    def __init__(self, group, private_key):
        from pssh.clients import ParallelSSHClient

        self.group = group
        self.private_key = private_key

        self.hosts = get_hosts_in_group(group)
        self.client = ParallelSSHClient(self.hosts, pkey=private_key)
        print("Found {} hosts:".format(len(self.hosts)))
        print(self.hosts)

    def kill_python(self):
        output = self.client.run_command('pkill python')
        self._process_output(output)

    def _deploy_branch(self, branch):
        raise ValueError("can't seem to get host forwarding to work")
        cmd = [
            "cd /home/matthewp/git/ground",
            "git checkout master", 
            "git pull",
            "git checkout {}".format(branch),
            "git pull origin {}".format(branch),
            "source /anaconda3/bin/activate torch-xla-nightly",
            "python setup.py install"
        ]
        output = self.client.run_command(";".join(cmd))
        self._process_output(output)

    def _parallel_shell_ssh(self, cmd, hosts, max_parallelism=None):
        # run a command on hosts in parallel with subprocess
        # hosts is a list of ip addresses
        if max_parallelism is None:
            host_groups = [hosts]
        else:
            host_groups = []
            for start in range(0, len(hosts), max_parallelism):
                end = start + max_parallelism
                host_groups.append(hosts[start:end])

        for host_group in host_groups:
            processes = []
            for host in host_group:
                ssh_cmd = [
                    "ssh",
                    "-i", self.private_key,
                    "-oStrictHostKeyChecking=no",
                    host,
                    '"bash -c ' + "'" + ';'.join(cmd) + "'" + '"'
                ]
    
                process = subprocess.Popen(
                    ' '.join(ssh_cmd), shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                processes.append([process, host])

            for process, host in processes:
                process.wait()
                stdout, stderr = process.communicate()
                print("-" * 40 + " " + host)
                print(stdout.decode('utf-8'))
                print(stderr.decode('utf-8'))

    def deploy_branch(self, branch, code_dir="/home/armanc/code/longformer"):
        # use a shell command to deploy to work around agent forwarding
        # issues with ssh2-python
        # assumes conda env is called torch-xla-nightly
        cmd = [
            "cd {}".format(code_dir),
            # "git checkout master",
            # "git pull",
            # "git checkout {}".format(branch),
            # "git pull origin {}".format(branch),
            "source /anaconda3/bin/activate torch-xla-nightly",

            # "wget https://ai2-s2-research.s3-us-west-2.amazonaws.com/longformer/longformer-base-4096.tar.gz",
            # "tar -xvf longformer-base-4096",
            # "wget https://ai2-s2-research.s3-us-west-2.amazonaws.com/longformer/longformer-large-4096.tar.gz",
            # "tar -xvf longformer-large-4096",
            # "python setup.py install"
        ]
        self._parallel_shell_ssh(cmd, self.hosts)

    def create_imagenet100(self):
        cmd = [
            # first line so other nodes can write out logging that is ignored
            "source activate torch-xla-nightly",
            "mkdir -p /home/matthewp/data/imagenet100",
            "python /home/matthewp/git/ground/bin/imagenet100.py --input_dir /mnt/disks/matthewp_vision_disk/imagenet --output_dir /home/matthewp/data/imagenet100",
        ]
        output = self.client.run_command(";".join(cmd))
        self._process_output(output)

    def mount_disk(self, mount_location, dev_sd_location):
        """
        mount the disk in /dev/dev_sd_location to mount_location, e.g.
        self.mount_disk('/mnt/disks/matthewp_vision_disk', 'sdb') will mount
        /dev/sdb into /mnt/disks/matthewp_vision_disk
        """
        cmd = [
            #sudo mkdir -p /mnt/disks/matthewp_vision_disk",
            "sudo mkdir -p {}".format(mount_location),
            #sudo chmod a+w /mnt/disks/matthewp_vision_disk",
            "sudo chmod a+w {}".format(mount_location),
            #sudo mount -o discard,defaults /dev/sdb /mnt/disks/matthewp_vision_disk"
            "sudo mount -o discard,defaults /dev/{} {}".format(dev_sd_location, mount_location)
        ]
        output = self.client.run_command(";".join(cmd))
        self._process_output(output)

    def mount_model_disk(self):
        self.mount_disk("/mnt/disks/matthewp_vision_disk", "sdb")

        cmd = [
            # first line so other nodes can write out logging that is ignored
            "sudo mkdir -p /mnt/models-disk/",
            "sudo chmod a+w /mnt/models-disk/",
            # "sudo mkdir -p /mnt/disks/matthewp_vision_disk",
            # "sudo chmod a+w /mnt/disks/matthewp_vision_disk",
            # "sudo mount -o discard,defaults /dev/sdb /mnt/disks/matthewp_vision_disk"
        ]
        output = self.client.run_command(";".join(cmd))
        self._process_output(output)

    def mount_matthewp_models_head_node(self):
        # first attach the disk to the head mode
        self.attach_matthewp_models_head_node()

        # now mount it - this is just run on head node
        cmd = [
            # get the sb* location for mounting
            "sudo lsblk",
            'sb_loc=`sudo lsblk | grep 200G | cut -f 1 -d " "`',
            'echo "Got $sb_loc for mounting"',
            "sudo mkdir -p /mnt/models-disk/",
            "sudo chmod ugo+w /mnt/models-disk",
            "sudo mount -o discard,defaults /dev/$sb_loc /mnt/models-disk/",
            "sudo apt-get -y install dstat"
        ]

        from pssh.clients import ParallelSSHClient
        head_node_client = ParallelSSHClient([self.hosts[0]], pkey=self.private_key)
        output = head_node_client.run_command(";".join(cmd))
        self._process_output(output, [self.hosts[0]])

        # finally create /mnt/disks/matthewp_models on all nodes (it must exist so
        #   then can write out logging that is ignored... PTL "quirks")
        cmd = [
            # first line so other nodes can write out logging that is ignored
            "sudo mkdir -p /mnt/models-disk",
            "sudo chmod a+w /mnt/models-disk",
        ]
        output = self.client.run_command(";".join(cmd))
        self._process_output(output)

    def attach_matthewp_models_head_node(self):
        # get the head node name and attach the disk
        head_node_name_cmd = 'gcloud compute instance-groups list-instances {} --zone europe-west4-a | grep {} | head -n 1 | cut -f 1 -d " "'.format(self.group, self.group)
        completed = subprocess.run(head_node_name_cmd, shell=True, stdout=subprocess.PIPE)
        head_node_name = completed.stdout.decode('utf-8').strip()

        # now attach the disk
        cmd = "gcloud compute instances attach-disk {} --disk armanc-models1 --zone europe-west4-a".format(head_node_name)
        print("Running {}".format(cmd))
        completed = subprocess.run(cmd, shell=True)

    def copy_file_head_node_other_nodes(self, file_dir, file_name):
        # copy a file in file_dir/file_name from the head node to all other nodes
        # to do so, run scp from every machine except the head node
        file_full_path = os.path.join(file_dir, file_name)
        head_node_ip = self.hosts[0]
        cmd = [
            "mkdir -p {}".format(file_dir),
            "scp -oStrictHostKeyChecking=no {}:{} {}".format(head_node_ip, file_full_path, file_full_path)
        ]
        self._parallel_shell_ssh(cmd, self.hosts[1:], 4)

    def upgrade_transformers(self):
        cmd = [
            'source /anaconda3/bin/activate torch-xla-nightly',
            'yes | pip uninstall transformers',
            'pip install git+git://github.com/matt-peters/transformers.git@working'
        ]
        self._parallel_shell_ssh(cmd, self.hosts)

    def _process_output(self, output, hosts=None):
        if hosts is None:
            hosts = self.hosts

        n_failed = 0
        for host in hosts:
            response = output[host]
            print("-" * 40 + " " + host)
            if response.exit_code != 0:
                print("FAILED!")
                n_failed += 1
            else:
                print("SUCCEEDED!")
            print('\n'.join(list(response.stdout)))
            print('\n'.join(list(response.stderr)))
        return n_failed

    def increase_ulimit_a(self):
        """
        Increase ulimit -a to 500000 open files.
        This does two things:
            (a) replaces the ~/.profile to remove the line that was setting ulimit -a to 10000
            (b) increases the system wide limit in /etc/security/limits.conf to 500000
        """
        import tempfile
        import gevent

        profile = """
            # if running bash
            if [ -n "$BASH_VERSION" ]; then
                # include .bashrc if it exists
                if [ -f "$HOME/.bashrc" ]; then
                . "$HOME/.bashrc"
                fi
            fi
            
            # set PATH so it includes user's private bin if it exists
            if [ -d "$HOME/bin" ] ; then
                PATH="$HOME/bin:$PATH"
            fi

            export PATH=/anaconda3/bin:$HOME/bin:$HOME/.local/bin:$PATH
        """

        with tempfile.NamedTemporaryFile('w') as local_profile_file:
            local_profile_file.write(profile)
            local_profile_file.flush()
            greenlets = self.client.scp_send(local_profile_file.name, '/home/matthewp/.profile')
            joined_greenlets = gevent.joinall(greenlets)

        cmd = ["""sudo echo "*  soft    nofile       500000
*  hard    nofile       500000" | sudo tee /etc/security/limits.conf > /dev/null"""]
        output = self.client.run_command(";".join(cmd))
        self._process_output(output)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--group', type=str)
    parser.add_argument('--key', type=str, default='/Users/armanc/.ssh/google_compute_engine')
    args = parser.parse_args()

    #group = 'matthewp-tpu-group-23'
    #key = '/Users/matthewp/.ssh/google_tpu'

    manager = DistributedManager(args.group, args.key)

    # manager.deploy_branch('tpu2')
    # manager.mount_model_disk()
    # manager.mount_matthewp_models_head_node()
#    manager.upgrade_transformers()
    manager.copy_file_head_node_other_nodes('/home/armanc/code/longformer/', 'xla_distributed.py') 
#    manager.increase_ulimit_a()
