#!/bin/bash
./build-docker.sh
ssh root@drillpress.hal9k.dk "docker pull bullestock/acs-gateway"
pushd ../drillpress/ansible/
ansible-playbook site.yml
popd
