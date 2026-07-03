#!/bin/bash
set -e
serial=`cat docker-serial`
docker build -t bullestock/acs-gateway:$serial -t bullestock/acs-gateway:latest -f Dockerfile --build-arg GIT_COMMIT=`git rev-parse HEAD` --build-arg VERSION_NUMBER=$serial .
docker push bullestock/acs-gateway:$serial
docker push bullestock/acs-gateway:latest
expr $serial + 1 > docker-serial
