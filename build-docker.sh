#!/bin/bash
set -e
serial=`cat docker-serial`
docker build -t bullestock/acs-gateway:$serial -f Dockerfile --build-arg GIT_COMMIT=`git rev-parse HEAD` --build-arg VERSION_NUMBER=$serial .
docker tag bullestock/acs-gateway:$serial bullestock/acs-gateway:latest
docker push bullestock/acs-gateway:$serial
docker push bullestock/acs-gateway:latest
expr $serial + 1 > docker-serial
